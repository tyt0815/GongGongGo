from dataclasses import replace
from pathlib import Path

import pytest

from src.database import connect, initialize_database
from src.domain import (
    CategoryResult,
    CrawledPost,
    CrawlRunStatus,
    PostStatus,
    Settings,
)
from src.repository import Repository


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


def test_keyword_replacement_normalizes_and_rejects_empty_lists(repository):
    repository.replace_keywords("institution", [" Target ", "target", "Other"])

    assert repository.get_keywords("institution") == ("Target", "Other")

    with pytest.raises(ValueError, match="(?i)at least one"):
        repository.replace_keywords("institution", [" ", ""])

    assert repository.get_keywords("institution") == ("Target", "Other")


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
        ("https://example.test/target", ("accounting",)),
        ("https://example.test/general-match", ("software",)),
    ]


def test_parse_failed_target_title_is_visible_as_a_raw_title_fallback(repository):
    """Catches a parser failure hiding a configured target institution's post."""
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
        ("https://example.test/raw-target", "", ()),
    ]


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
