import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from src.database import connect
from src.domain import (
    CategoryResult,
    CrawledPost,
    CrawlRunRecord,
    CrawlRunStatus,
    DeadlineKind,
    DeletedLinkRecord,
    ParsedDeadline,
    ParsedTitle,
    PostRecord,
    PostStatus,
    Settings,
)
from src.parsing import filter_roles, parse_deadline, parse_title


KeywordKind = Literal["institution", "role"]


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            yield connection
        finally:
            connection.close()

    def get_post(self, link: str) -> PostRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT link, original_title, status, deadline_raw "
                "FROM job_posts WHERE link = ?",
                (link,),
            ).fetchone()
        if row is None:
            return None
        return PostRecord(
            link=row["link"],
            original_title=row["original_title"],
            status=PostStatus(row["status"]),
            deadline_raw=row["deadline_raw"],
        )

    def list_visible_posts(
        self, statuses: tuple[PostStatus, ...] | None = None
    ) -> list[dict[str, object]]:
        institution_keywords = self.get_keywords("institution")
        role_keywords = self.get_keywords("role")
        query = "SELECT * FROM job_posts"
        parameters: tuple[str, ...] = ()
        if statuses is not None:
            if not statuses:
                return []
            placeholders = ", ".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            parameters = tuple(status.value for status in statuses)
        query += " ORDER BY id"

        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        visible: list[dict[str, object]] = []
        for row in rows:
            parsed = ParsedTitle(
                institution=row["institution"],
                employment=row["employment"],
                career=row["career"],
                roles=tuple(json.loads(row["roles_json"])),
            )
            should_show, roles = filter_roles(
                parsed, institution_keywords, role_keywords
            )
            if should_show:
                visible.append(
                    {
                        "id": row["id"],
                        "link": row["link"],
                        "category": row["category"],
                        "original_title": row["original_title"],
                        "institution": row["institution"],
                        "employment": row["employment"],
                        "career": row["career"],
                        "roles": roles,
                        "display_roles": roles,
                        "deadline_raw": row["deadline_raw"],
                        "deadline_date": row["deadline_date"],
                        "deadline_kind": DeadlineKind(row["deadline_kind"]),
                        "status": PostStatus(row["status"]),
                        "discovered_at": row["discovered_at"],
                        "last_seen_at": row["last_seen_at"],
                        "status_updated_at": row["status_updated_at"],
                    }
                )
        return visible

    def update_status(self, link: str, status: PostStatus) -> bool:
        with self._connection() as connection, connection:
            result = connection.execute(
                "UPDATE job_posts SET status = ?, status_updated_at = ? WHERE link = ?",
                (status.value, _now(), link),
            )
            return result.rowcount == 1

    def upsert_crawled_posts(self, posts: list[CrawledPost]) -> int:
        new_count = 0
        with self._connection() as connection, connection:
            for post in posts:
                existed = connection.execute(
                    "SELECT 1 FROM job_posts WHERE link = ?", (post.link,)
                ).fetchone()
                parsed_title = parse_title(post.title)
                parsed_deadline = parse_deadline(post.deadline_raw, date.today())
                now = _now()
                connection.execute(
                    """
                    INSERT INTO job_posts(
                        link, category, original_title, institution, employment, career,
                        roles_json, deadline_raw, deadline_date, deadline_kind, status,
                        discovered_at, last_seen_at, status_updated_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'review_pending', ?, ?, ?
                    WHERE NOT EXISTS(
                        SELECT 1 FROM deleted_links WHERE link = ?
                    )
                    ON CONFLICT(link) DO UPDATE SET
                        category = excluded.category,
                        original_title = excluded.original_title,
                        institution = excluded.institution,
                        employment = excluded.employment,
                        career = excluded.career,
                        roles_json = excluded.roles_json,
                        deadline_raw = excluded.deadline_raw,
                        deadline_date = excluded.deadline_date,
                        deadline_kind = excluded.deadline_kind,
                        last_seen_at = excluded.last_seen_at
                    """,
                    _crawled_values(post, parsed_title, parsed_deadline, now),
                )
                if existed is None and connection.execute("SELECT changes()").fetchone()[0]:
                    new_count += 1
        return new_count

    def delete_permanently(self, link: str) -> bool:
        with self._connection() as connection, connection:
            connection.execute(
                "INSERT INTO deleted_links(link, deleted_at) VALUES (?, ?) "
                "ON CONFLICT(link) DO NOTHING",
                (link, _now()),
            )
            result = connection.execute("DELETE FROM job_posts WHERE link = ?", (link,))
            return result.rowcount == 1

    def list_deleted_links(self) -> tuple[DeletedLinkRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, link FROM deleted_links ORDER BY id"
            ).fetchall()
        return tuple(DeletedLinkRecord(id=row["id"], link=row["link"]) for row in rows)

    def unblock_link(self, id: int) -> bool:
        with self._connection() as connection, connection:
            result = connection.execute("DELETE FROM deleted_links WHERE id = ?", (id,))
            return result.rowcount == 1

    def get_settings(self) -> Settings:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT concurrency, open_browser FROM app_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Application settings are not initialized")
        return Settings(
            concurrency=row["concurrency"], open_browser=bool(row["open_browser"])
        )

    def update_settings(self, settings: Settings) -> None:
        with self._connection() as connection, connection:
            connection.execute(
                "UPDATE app_settings SET concurrency = ?, open_browser = ? WHERE id = 1",
                (settings.concurrency, int(settings.open_browser)),
            )

    def get_keywords(self, kind: KeywordKind) -> tuple[str, ...]:
        _validate_keyword_kind(kind)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT keyword FROM filter_keywords WHERE kind = ? ORDER BY id",
                (kind,),
            ).fetchall()
        return tuple(row["keyword"] for row in rows)

    def replace_keywords(self, kind: KeywordKind, values: list[str]) -> None:
        _validate_keyword_kind(kind)
        normalized = _normalize_keywords(values)
        if not normalized:
            raise ValueError("At least one non-empty keyword is required")

        with self._connection() as connection, connection:
            connection.execute("DELETE FROM filter_keywords WHERE kind = ?", (kind,))
            connection.executemany(
                "INSERT INTO filter_keywords(kind, keyword) VALUES (?, ?)",
                ((kind, keyword) for keyword in normalized),
            )

    def create_crawl_run(self, trigger: str) -> int:
        with self._connection() as connection, connection:
            result = connection.execute(
                "INSERT INTO crawl_runs(trigger, started_at, status) VALUES (?, ?, 'running')",
                (trigger, _now()),
            )
            return int(result.lastrowid)

    def finish_crawl_run(
        self,
        run_id: int,
        status: CrawlRunStatus,
        new_count: int,
        results: tuple[CategoryResult, ...],
    ) -> None:
        with self._connection() as connection, connection:
            connection.execute(
                "UPDATE crawl_runs SET finished_at = ?, status = ?, new_count = ? WHERE id = ?",
                (_now(), status.value, new_count, run_id),
            )
            connection.executemany(
                """
                INSERT INTO crawl_category_results(
                    run_id, category, status, new_count, error_summary
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, category) DO UPDATE SET
                    status = excluded.status,
                    new_count = excluded.new_count,
                    error_summary = excluded.error_summary
                """,
                (
                    (
                        run_id,
                        result.category,
                        "failed" if result.error else "succeeded",
                        len(result.posts),
                        result.error,
                    )
                    for result in results
                ),
            )

    def latest_crawl_run(self) -> CrawlRunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, status, new_count FROM crawl_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return CrawlRunRecord(
            id=row["id"], status=CrawlRunStatus(row["status"]), new_count=row["new_count"]
        )


def _crawled_values(
    post: CrawledPost,
    parsed_title: ParsedTitle | None,
    parsed_deadline: ParsedDeadline,
    now: str,
) -> tuple[object, ...]:
    title = parsed_title or ParsedTitle("", "", "", ())
    return (
        post.link,
        post.category,
        post.title,
        title.institution,
        title.employment,
        title.career,
        json.dumps(title.roles, ensure_ascii=False),
        post.deadline_raw,
        parsed_deadline.value.isoformat() if parsed_deadline.value else None,
        parsed_deadline.kind.value,
        now,
        now,
        now,
        post.link,
    )


def _normalize_keywords(values: list[str]) -> tuple[str, ...]:
    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = value.strip()
        normalized = keyword.casefold()
        if keyword and normalized not in seen:
            keywords.append(keyword)
            seen.add(normalized)
    return tuple(keywords)


def _validate_keyword_kind(kind: str) -> None:
    if kind not in ("institution", "role"):
        raise ValueError("Unknown keyword kind")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
