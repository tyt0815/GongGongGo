import json
import sqlite3
from pathlib import Path

import pytest

from src.config import DEFAULT_INSTITUTION_KEYWORDS, DEFAULT_ROLE_KEYWORDS
from src.database import connect, initialize_database


def test_migration_imports_only_strict_dates_maps_status_and_preserves_source(
    tmp_path: Path,
):
    source = tmp_path / "job_posts.json"
    source.write_text(
        json.dumps(
            [
                {
                    "category": "중앙공기업",
                    "title": "[A 채용] 정규직 신입 (전산)",
                    "deadline": "2026.08.10",
                    "link": "https://example/a",
                    "state": "대기",
                },
                {
                    "category": "중앙공기업",
                    "title": "[B 채용] 정규직 신입 (전산)",
                    "deadline": "2026.8.10",
                    "link": "https://example/non-strict",
                    "state": "지원 예정",
                },
                {
                    "category": "중앙공기업",
                    "title": "[C 채용] 정규직 신입 (전산)",
                    "deadline": "2026.08.11",
                    "link": "https://example/planned",
                    "state": "지원 예정",
                },
                {
                    "category": "중앙공기업",
                    "title": "[D 채용] 정규직 신입 (전산)",
                    "deadline": "2026.08.12",
                    "link": "https://example/applied",
                    "state": "완료",
                },
                {
                    "category": "중앙공기업",
                    "title": "[E 채용] 정규직 신입 (전산)",
                    "deadline": "채용시마감",
                    "link": "https://example/open",
                    "state": "완료",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_before = source.read_text(encoding="utf-8")
    db_path = tmp_path / "gonggonggo.db"

    initialize_database(db_path, source)

    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT link, status, deadline_date, deadline_kind, is_new "
            "FROM job_posts ORDER BY id"
        ).fetchall()
        migration = connection.execute(
            "SELECT value FROM app_meta WHERE key = 'json_migration_v1'"
        ).fetchone()

    assert [tuple(row) for row in rows] == [
        ("https://example/a", "review_pending", "2026-08-10", "dated", 0),
        ("https://example/planned", "planned", "2026-08-11", "dated", 0),
        ("https://example/applied", "applied", "2026-08-12", "dated", 0),
    ]
    assert migration["value"] == "complete"
    assert source.read_text(encoding="utf-8") == source_before


def test_initialize_database_creates_schema_defaults_and_idempotent_migration(
    tmp_path: Path,
):
    source = tmp_path / "job_posts.json"
    source.write_text(
        json.dumps(
            [
                {
                    "category": "중앙공기업",
                    "title": "[한국가스공사 채용] 정규직 신입 (전산)",
                    "deadline": "2026.08.10",
                    "link": "https://example/a",
                    "state": "지원 예정",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "gonggonggo.db"

    initialize_database(db_path, source)
    initialize_database(db_path, source)

    with connect(db_path) as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        settings = connection.execute(
            "SELECT concurrency, open_browser FROM app_settings WHERE id = 1"
        ).fetchone()
        institution_keywords = tuple(
            row["keyword"]
            for row in connection.execute(
                "SELECT keyword FROM filter_keywords "
                "WHERE kind = 'institution' ORDER BY id"
            )
        )
        role_keywords = tuple(
            row["keyword"]
            for row in connection.execute(
                "SELECT keyword FROM filter_keywords WHERE kind = 'role' ORDER BY id"
            )
        )
        post_count = connection.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert {
        "app_meta",
        "app_settings",
        "filter_keywords",
        "job_posts",
        "crawl_runs",
        "crawl_category_results",
        "deleted_links",
    } <= table_names
    assert tuple(settings) == (2, 0)
    assert institution_keywords == DEFAULT_INSTITUTION_KEYWORDS
    assert role_keywords == DEFAULT_ROLE_KEYWORDS
    assert post_count == 1
    assert journal_mode == "wal"
    assert busy_timeout >= 5000


def test_user_removed_keyword_lists_remain_empty_after_restart(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    source = tmp_path / "job_posts.json"
    source.write_text("[]", encoding="utf-8")
    initialize_database(db_path, source)
    with connect(db_path) as connection, connection:
        connection.execute("DELETE FROM filter_keywords")

    initialize_database(db_path, source)

    with connect(db_path) as connection:
        keywords = connection.execute(
            "SELECT kind, keyword FROM filter_keywords"
        ).fetchall()
    assert keywords == []


def test_existing_database_without_seed_metadata_preserves_empty_kind_on_upgrade(
    tmp_path: Path,
):
    db_path = tmp_path / "gonggonggo.db"
    source = tmp_path / "job_posts.json"
    source.write_text("[]", encoding="utf-8")
    initialize_database(db_path, source)
    with connect(db_path) as connection, connection:
        connection.execute(
            "DELETE FROM filter_keywords WHERE kind = 'institution'"
        )
        connection.execute(
            "DELETE FROM app_meta WHERE key LIKE 'default_keywords_seeded_%'"
        )

    initialize_database(db_path, source)

    with connect(db_path) as connection:
        institution_count = connection.execute(
            "SELECT COUNT(*) FROM filter_keywords WHERE kind = 'institution'"
        ).fetchone()[0]
        role_keywords = tuple(
            row["keyword"]
            for row in connection.execute(
                "SELECT keyword FROM filter_keywords WHERE kind = 'role' ORDER BY id"
            )
        )
    assert institution_count == 0
    assert role_keywords == DEFAULT_ROLE_KEYWORDS


def test_schema_enforces_status_deadline_concurrency_and_keyword_constraints(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    source = tmp_path / "empty.json"
    source.write_text("[]", encoding="utf-8")
    initialize_database(db_path, source)

    with connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE app_settings SET concurrency = 3 WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO filter_keywords(kind, keyword) VALUES ('other', 'x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO job_posts(
                    link, category, original_title, institution, employment, career,
                    roles_json, deadline_raw, deadline_date, deadline_kind, status,
                    discovered_at, last_seen_at, status_updated_at
                ) VALUES (
                    'https://example/invalid', '중앙공기업', '제목', '', '', '',
                    '[]', '2026.08.10', '2026-08-10', 'invalid', 'invalid',
                    '2026-08-03T00:00:00+00:00', '2026-08-03T00:00:00+00:00',
                    '2026-08-03T00:00:00+00:00'
                )
                """
            )


def test_existing_job_posts_schema_adds_non_new_flag_on_upgrade(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    source = tmp_path / "job_posts.json"
    source.write_text("[]", encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE job_posts (
                id INTEGER PRIMARY KEY,
                link TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                original_title TEXT NOT NULL,
                institution TEXT NOT NULL,
                employment TEXT NOT NULL,
                career TEXT NOT NULL,
                roles_json TEXT NOT NULL,
                deadline_raw TEXT NOT NULL,
                deadline_date TEXT,
                deadline_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                status_updated_at TEXT NOT NULL
            );
            INSERT INTO job_posts VALUES (
                1, 'https://example/old', 'central', '[Old] full-time (software)',
                'Old', 'full-time', '', '["software"]', '2099.08.10',
                '2099-08-10', 'dated', 'review_pending',
                '2026-08-03T00:00:00+00:00', '2026-08-03T00:00:00+00:00',
                '2026-08-03T00:00:00+00:00'
            );
            """
        )

    initialize_database(db_path, source)

    with connect(db_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(job_posts)")
        }
        row = connection.execute(
            "SELECT is_new FROM job_posts WHERE link = 'https://example/old'"
        ).fetchone()
    assert "is_new" in columns
    assert row["is_new"] == 0
