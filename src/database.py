import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import DEFAULT_INSTITUTION_KEYWORDS, DEFAULT_ROLE_KEYWORDS
from src.domain import DeadlineKind, PostStatus
from src.parsing import parse_deadline, parse_title


_MIGRATION_KEY = "json_migration_v1"
_STRICT_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}\Z")
_LEGACY_STATUS_MAP = {
    "대기": PostStatus.REVIEW_PENDING.value,
    "지원 예정": PostStatus.PLANNED.value,
    "완료": PostStatus.APPLIED.value,
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(db_path: Path, json_path: Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                concurrency INTEGER NOT NULL CHECK (concurrency IN (1, 2, 4)),
                open_browser INTEGER NOT NULL CHECK (open_browser IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS filter_keywords (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('institution', 'role')),
                keyword TEXT NOT NULL,
                UNIQUE (kind, keyword)
            );

            CREATE TABLE IF NOT EXISTS job_posts (
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
                deadline_kind TEXT NOT NULL CHECK (
                    deadline_kind IN ('dated', 'open', 'unknown')
                ),
                status TEXT NOT NULL CHECK (
                    status IN ('review_pending', 'planned', 'applied', 'excluded')
                ),
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                status_updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY,
                trigger TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('running', 'succeeded', 'partial', 'failed')
                ),
                new_count INTEGER NOT NULL DEFAULT 0 CHECK (new_count >= 0)
            );

            CREATE TABLE IF NOT EXISTS crawl_category_results (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                new_count INTEGER NOT NULL DEFAULT 0 CHECK (new_count >= 0),
                error_summary TEXT,
                UNIQUE (run_id, category)
            );

            CREATE TABLE IF NOT EXISTS deleted_links (
                id INTEGER PRIMARY KEY,
                link TEXT NOT NULL UNIQUE,
                deleted_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO app_settings(id, concurrency, open_browser)
            VALUES (1, 2, 0) ON CONFLICT(id) DO NOTHING
            """
        )
        _insert_default_keywords(connection)

    _migrate_json_once(db_path, json_path)


def _insert_default_keywords(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO filter_keywords(kind, keyword)
        VALUES ('institution', ?)
        ON CONFLICT(kind, keyword) DO NOTHING
        """,
        ((keyword,) for keyword in DEFAULT_INSTITUTION_KEYWORDS),
    )
    connection.executemany(
        """
        INSERT INTO filter_keywords(kind, keyword)
        VALUES ('role', ?)
        ON CONFLICT(kind, keyword) DO NOTHING
        """,
        ((keyword,) for keyword in DEFAULT_ROLE_KEYWORDS),
    )


def _migrate_json_once(db_path: Path, json_path: Path) -> None:
    with connect(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM app_meta WHERE key = ?", (_MIGRATION_KEY,)
        ).fetchone():
            return

        if not json_path.exists():
            return

        source = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(source, list):
            raise ValueError("Legacy job posts JSON must contain a list")

        now = datetime.now(timezone.utc).isoformat()
        with connection:
            for legacy_post in source:
                values = _migration_values(legacy_post, now)
                if values is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO job_posts(
                        link, category, original_title, institution, employment, career,
                        roles_json, deadline_raw, deadline_date, deadline_kind, status,
                        discovered_at, last_seen_at, status_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(link) DO NOTHING
                    """,
                    values,
                )
        connection.execute(
            "INSERT INTO app_meta(key, value) VALUES (?, 'complete')",
            (_MIGRATION_KEY,),
        )
        connection.commit()


def _migration_values(legacy_post: object, now: str) -> tuple[object, ...] | None:
    if not isinstance(legacy_post, dict):
        return None

    deadline_raw = legacy_post.get("deadline")
    if not isinstance(deadline_raw, str) or _STRICT_DATE.fullmatch(deadline_raw) is None:
        return None
    try:
        strict_deadline = datetime.strptime(deadline_raw, "%Y.%m.%d").date()
    except ValueError:
        return None

    category = legacy_post.get("category")
    title = legacy_post.get("title")
    link = legacy_post.get("link")
    if not all(isinstance(value, str) and value for value in (category, title, link)):
        return None

    parsed_title = parse_title(title)
    institution = parsed_title.institution if parsed_title else ""
    employment = parsed_title.employment if parsed_title else ""
    career = parsed_title.career if parsed_title else ""
    roles = parsed_title.roles if parsed_title else ()
    parsed_deadline = parse_deadline(deadline_raw, strict_deadline)
    status = _LEGACY_STATUS_MAP.get(
        legacy_post.get("state"), PostStatus.REVIEW_PENDING.value
    )
    return (
        link,
        category,
        title,
        institution,
        employment,
        career,
        json.dumps(roles, ensure_ascii=False),
        deadline_raw,
        parsed_deadline.value.isoformat(),
        DeadlineKind.DATED.value,
        status,
        now,
        now,
        now,
    )
