from dataclasses import replace
from datetime import date
from pathlib import Path
import sqlite3

import pytest

from src.database import connect, initialize_database
from src.domain import (
    CategoryResult,
    CrawledPost,
    CrawlRunStatus,
    PostStatus,
    Settings,
)
import src.repository as repository_module
from src.repository import Repository


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(repository_module, "_today", lambda: date(2026, 8, 3))


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    initialize_database(db_path, json_path)
    return Repository(db_path)


@pytest.fixture
def seeded_post(repository: Repository) -> CrawledPost:
    post = CrawledPost(
        category="central",
        title="[Target Agency] full-time new (software/data)",
        deadline_raw="2026.08.10",
        link="https://example.test/jobs/1",
    )
    repository.upsert_crawled_posts([post])
    return post


def test_upsert_skips_new_posts_with_a_past_dated_deadline(
    repository, monkeypatch
):
    monkeypatch.setattr(repository_module, "_today", lambda: date(2026, 8, 3))
    expired = CrawledPost(
        category="central",
        title="[Expired Agency] full-time (software)",
        deadline_raw="2026.08.02",
        link="https://example.test/expired",
    )
    due_today = replace(
        expired,
        title="[Due Today Agency] full-time (software)",
        deadline_raw="2026.08.03",
        link="https://example.test/due-today",
    )
    open_post = replace(
        expired,
        title="[Open Agency] full-time (software)",
        deadline_raw="채용시마감",
        link="https://example.test/open",
    )
    unknown = replace(
        expired,
        title="[Unknown Agency] full-time (software)",
        deadline_raw="날짜 미정",
        link="https://example.test/unknown",
    )

    assert repository.upsert_crawled_posts([expired, due_today, open_post, unknown]) == 3
    assert repository.list_existing_links() == {
        due_today.link,
        open_post.link,
        unknown.link,
    }


def test_upsert_removes_stored_posts_after_their_deadline(repository, monkeypatch):
    monkeypatch.setattr(repository_module, "_today", lambda: date(2026, 8, 3))
    dated = CrawledPost(
        category="central",
        title="[Dated Agency] full-time (software)",
        deadline_raw="2026.08.10",
        link="https://example.test/dated",
    )
    open_post = replace(
        dated,
        title="[Open Agency] full-time (software)",
        deadline_raw="채용시마감",
        link="https://example.test/open",
    )
    repository.upsert_crawled_posts([dated, open_post])

    monkeypatch.setattr(repository_module, "_today", lambda: date(2026, 8, 11))
    repository.upsert_crawled_posts([])

    assert repository.list_existing_links() == {open_post.link}
    assert repository.list_deleted_links() == ()


def test_crawler_upsert_preserves_user_status(repository, seeded_post):
    repository.update_status(seeded_post.link, PostStatus.PLANNED)
    with connect(repository.db_path) as connection:
        before = connection.execute(
            "SELECT status, status_updated_at FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()
    changed = replace(seeded_post, title="Updated title", deadline_raw="2026.08.20")

    repository.upsert_crawled_posts([changed])

    row = repository.get_post(seeded_post.link)
    with connect(repository.db_path) as connection:
        after = connection.execute(
            "SELECT status, status_updated_at FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()
    assert row is not None
    assert row.status is PostStatus.PLANNED
    assert row.original_title == "Updated title"
    assert tuple(after) == tuple(before)


def test_new_flag_is_created_preserved_and_acknowledged(repository, seeded_post):
    repository.replace_keywords("role", ["software"])
    assert repository.list_visible_posts()[0]["is_new"] is True

    repository.upsert_crawled_posts(
        [replace(seeded_post, title="[Target Agency] updated (software/data)")]
    )
    assert repository.list_visible_posts()[0]["is_new"] is True

    assert repository.acknowledge_post(seeded_post.link) is True
    assert repository.list_visible_posts()[0]["is_new"] is False
    assert repository.acknowledge_post("https://example.test/missing") is False


def test_review_pending_new_posts_are_listed_newest_first(repository):
    repository.replace_keywords("role", ["software"])
    old = CrawledPost(
        category="central",
        title="[Old Agency] full-time (software)",
        deadline_raw="2099.08.10",
        link="https://example.test/old",
    )
    newer = replace(
        old,
        title="[New Agency] full-time (software)",
        link="https://example.test/new",
    )
    newest = replace(
        old,
        title="[Newest Agency] full-time (software)",
        link="https://example.test/newest",
    )
    repository.upsert_crawled_posts([old])
    repository.acknowledge_post(old.link)
    repository.upsert_crawled_posts([newer, newest])

    visible = repository.list_visible_posts()

    assert [(row["link"], row["is_new"]) for row in visible] == [
        (newest.link, True),
        (newer.link, True),
        (old.link, False),
    ]


def test_status_update_changes_no_crawler_owned_fields(repository, seeded_post):
    fields = (
        "category",
        "original_title",
        "institution",
        "employment",
        "career",
        "roles_json",
        "deadline_raw",
        "deadline_date",
        "deadline_kind",
        "discovered_at",
        "last_seen_at",
    )
    with connect(repository.db_path) as connection:
        before = connection.execute(
            "SELECT " + ", ".join(fields) + " FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()
        status_before = connection.execute(
            "SELECT status_updated_at FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()

    assert repository.update_status(seeded_post.link, PostStatus.APPLIED) is True

    with connect(repository.db_path) as connection:
        after = connection.execute(
            "SELECT " + ", ".join(fields) + " FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()
    assert tuple(after) == tuple(before)
    assert repository.get_post(seeded_post.link).status is PostStatus.APPLIED
    with connect(repository.db_path) as connection:
        status_row = connection.execute(
            "SELECT status, status_updated_at FROM job_posts WHERE link = ?",
            (seeded_post.link,),
        ).fetchone()
    assert status_row["status"] == PostStatus.APPLIED.value
    assert status_row["status_updated_at"] != status_before["status_updated_at"]
    with connect(repository.db_path) as connection:
        is_new = connection.execute(
            "SELECT is_new FROM job_posts WHERE link = ?", (seeded_post.link,)
        ).fetchone()["is_new"]
    assert is_new == 0


def test_permanent_delete_blocks_recollection(repository, seeded_post):
    assert repository.delete_permanently(seeded_post.link) is True
    repository.upsert_crawled_posts([seeded_post])

    assert repository.get_post(seeded_post.link) is None
    assert repository.list_deleted_links()[0].link == seeded_post.link


def test_unblocking_link_allows_a_future_crawler_insert(repository, seeded_post):
    repository.delete_permanently(seeded_post.link)
    deleted = repository.list_deleted_links()[0]

    assert repository.unblock_link(deleted.id) is True
    assert repository.unblock_link(deleted.id) is False
    assert repository.upsert_crawled_posts([seeded_post]) == 1
    assert repository.get_post(seeded_post.link) is not None


def test_keyword_replacement_normalizes_and_allows_an_intentional_empty_list(repository):
    repository.replace_keywords("institution", [" Target ", "target", "Other"])

    assert repository.get_keywords("institution") == ("Target", "Other")

    repository.replace_keywords("institution", [" ", ""])

    assert repository.get_keywords("institution") == ()


def test_preference_update_rolls_back_all_values_on_mid_save_failure(repository):
    repository.update_settings(Settings(concurrency=1, open_browser=False))
    repository.replace_keywords("institution", ["Old Institution"])
    repository.replace_keywords("role", ["Old Role"])
    with connect(repository.db_path) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER fail_role_keyword_insert
            BEFORE INSERT ON filter_keywords
            WHEN NEW.kind = 'role'
            BEGIN
                SELECT RAISE(ABORT, 'forced role failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced role failure"):
        repository.update_preferences(
            Settings(concurrency=4, open_browser=True),
            [" New Institution ", "new institution"],
            ["New Role"],
        )

    assert repository.get_settings() == Settings(concurrency=1, open_browser=False)
    assert repository.get_keywords("institution") == ("Old Institution",)
    assert repository.get_keywords("role") == ("Old Role",)


def test_settings_persist(repository):
    settings = Settings(concurrency=4, open_browser=True)

    repository.update_settings(settings)

    assert repository.get_settings() == settings


def test_visible_posts_apply_target_and_role_filters(repository):
    repository.replace_keywords("institution", ["Target"])
    repository.replace_keywords("role", ["software"])
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="central",
                title="[Target Agency] full-time (accounting)",
                deadline_raw="2026.08.10",
                link="https://example.test/target",
            ),
            CrawledPost(
                category="central",
                title="[General Agency] full-time (software/accounting)",
                deadline_raw="2026.08.10",
                link="https://example.test/general-match",
            ),
            CrawledPost(
                category="central",
                title="[General Agency] full-time (accounting)",
                deadline_raw="2026.08.10",
                link="https://example.test/general-hidden",
            ),
        ]
    )

    visible = repository.list_visible_posts()

    assert [(row["link"], row["display_roles"]) for row in visible] == [
        ("https://example.test/general-match", ("software",)),
        ("https://example.test/target", ("accounting",)),
    ]


def test_parse_failed_target_title_is_visible_as_a_raw_title_fallback(repository):
    """Catches loss of a bracketed institution when role parsing fails."""
    repository.replace_keywords("institution", ["한국교육학술정보원"])
    repository.replace_keywords("role", ["전산"])
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="central",
                title="[한국교육학술정보원 채용] 정규직 신입",
                deadline_raw="2026.08.10",
                link="https://example.test/raw-target",
            ),
            CrawledPost(
                category="central",
                title="경기도 공공기관 통합채용 사전공고",
                deadline_raw="2026.08.10",
                link="https://example.test/raw-general",
            ),
        ]
    )

    visible = repository.list_visible_posts()

    assert [(row["link"], row["institution"], row["display_roles"]) for row in visible] == [
        ("https://example.test/raw-target", "한국교육학술정보원", ()),
    ]


@pytest.mark.parametrize(
    "title",
    [
        "한국교육학술정보원 특별 공고",
        "[한국교육학술정보원 채용 정규직 신입",
    ],
)
def test_unstructured_raw_title_cannot_match_institution_keyword(repository, title):
    repository.replace_keywords("institution", ["한국교육학술정보원"])
    repository.replace_keywords("role", ["전산"])
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="central",
                title=title,
                deadline_raw="2026.08.10",
                link="https://example.test/not-bracketed",
            )
        ]
    )

    assert repository.list_visible_posts() == []


def test_explicitly_empty_status_filter_returns_no_posts(repository, seeded_post):
    repository.replace_keywords("institution", ["Target"])

    assert repository.list_visible_posts(()) == []


def test_crawl_history_records_partial_failure(repository):
    run_id = repository.create_crawl_run("startup")

    repository.finish_crawl_run(
        run_id,
        CrawlRunStatus.PARTIAL,
        2,
        (
            CategoryResult(category="central", posts=()),
            CategoryResult(category="local", error="timed out"),
        ),
    )

    latest = repository.latest_crawl_run()
    assert latest is not None
    assert latest.id == run_id
    assert latest.status is CrawlRunStatus.PARTIAL
    assert latest.new_count == 2
    with connect(repository.db_path) as connection:
        results = connection.execute(
            "SELECT category, status, new_count, error_summary "
            "FROM crawl_category_results WHERE run_id = ? ORDER BY category",
            (run_id,),
        ).fetchall()
    assert [tuple(result) for result in results] == [
        ("central", "succeeded", 0, None),
        ("local", "failed", 0, "timed out"),
    ]


def test_crawl_history_treats_an_empty_error_as_failure(repository):
    run_id = repository.create_crawl_run("manual")

    repository.finish_crawl_run(
        run_id,
        CrawlRunStatus.FAILED,
        0,
        (CategoryResult(category="central", error=""),),
    )

    with connect(repository.db_path) as connection:
        result = connection.execute(
            "SELECT status, error_summary FROM crawl_category_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert tuple(result) == ("failed", "")


def test_existing_link_set_includes_all_persisted_posts(repository):
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="central",
                title="[Target Agency] full-time (software)",
                deadline_raw="2026.08.10",
                link="https://example.test/one",
            ),
            CrawledPost(
                category="local",
                title="[General Agency] full-time (accounting)",
                deadline_raw="2026.08.11",
                link="https://example.test/two",
            ),
        ]
    )

    assert repository.list_existing_links() == {
        "https://example.test/one",
        "https://example.test/two",
    }
