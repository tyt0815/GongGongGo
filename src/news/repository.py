import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from src.database import connect

from .domain import (
    CleanupStats,
    CrawledNewsItem,
    NewsItemType,
    NewsPeriod,
    NewsRecord,
    SaveStats,
)
from .registry import NEWS_CATEGORIES, SOURCES
from .url_normalization import normalize_url


SEOUL = ZoneInfo("Asia/Seoul")
_PERIOD_OFFSETS = {
    NewsPeriod.TODAY: (0, 0),
    NewsPeriod.YESTERDAY: (1, 1),
    NewsPeriod.THREE_DAYS: (2, 0),
    NewsPeriod.SEVEN_DAYS: (6, 0),
    NewsPeriod.THIRTY_DAYS: (29, 0),
}
_GENERAL_NEWS_CATEGORY = NEWS_CATEGORIES[0]


class NewsRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            yield connection
        finally:
            connection.close()

    def cleanup(self, now: datetime | None = None) -> CleanupStats:
        now_in_seoul = _seoul_datetime(now)
        now_iso = now_in_seoul.isoformat()
        today = now_in_seoul.date()
        item_count = 0

        with self._connection() as connection, connection:
            dismissal_count = connection.execute(
                "DELETE FROM news_dismissals WHERE expires_at <= ?", (now_iso,)
            ).rowcount
            for source in SOURCES.values():
                cutoff = (today - timedelta(days=source.ttl_days - 1)).isoformat()
                item_count += connection.execute(
                    "DELETE FROM news_items "
                    "WHERE source = ? "
                    "AND substr(COALESCE(published_at, discovered_at), 1, 10) < ?",
                    (source.source, cutoff),
                ).rowcount

        return CleanupStats(item_count=item_count, dismissal_count=dismissal_count)

    def upsert_items(
        self, items: list[CrawledNewsItem] | tuple[CrawledNewsItem, ...], now: datetime | None = None
    ) -> SaveStats:
        now_in_seoul = _seoul_datetime(now)
        now_iso = now_in_seoul.isoformat()
        deduplicated, duplicate_count = _deduplicate(items)
        if not deduplicated:
            return SaveStats(duplicate_count=duplicate_count)

        new_count = 0
        suppressed_count = 0
        with self._connection() as connection, connection:
            existing_urls = {
                row["url"] for row in connection.execute("SELECT url FROM news_items")
            }
            active_dismissals = {
                row["url"]
                for row in connection.execute(
                    "SELECT url FROM news_dismissals WHERE expires_at > ?", (now_iso,)
                )
            }
            values: list[tuple[object, ...]] = []
            for normalized_url, news_item in deduplicated.items():
                if normalized_url in active_dismissals:
                    suppressed_count += 1
                    continue
                if normalized_url in existing_urls:
                    duplicate_count += 1
                else:
                    new_count += 1
                values.append(
                    (
                        news_item.item_type.value,
                        news_item.source,
                        news_item.source_name,
                        news_item.category,
                        news_item.source_category,
                        news_item.title,
                        normalized_url,
                        _datetime_to_seoul_iso(news_item.published_at),
                        now_iso,
                    )
                )
            connection.executemany(
                """
                INSERT INTO news_items(
                    item_type, source, source_name, category, source_category, title,
                    url, published_at, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    item_type = excluded.item_type,
                    source = excluded.source,
                    source_name = excluded.source_name,
                    category = CASE
                        WHEN news_items.category = ? AND excluded.category <> ?
                            THEN excluded.category
                        ELSE news_items.category
                    END,
                    source_category = excluded.source_category,
                    title = excluded.title,
                    published_at = COALESCE(news_items.published_at, excluded.published_at)
                """,
                [
                    (*value, _GENERAL_NEWS_CATEGORY, _GENERAL_NEWS_CATEGORY)
                    for value in values
                ],
            )

        return SaveStats(
            new_count=new_count,
            duplicate_count=duplicate_count,
            suppressed_count=suppressed_count,
        )

    def list_items(
        self,
        period: NewsPeriod,
        item_type: NewsItemType | None = None,
        source: str | None = None,
        category: str | None = None,
        query: str = "",
        today: date | None = None,
    ) -> list[NewsRecord]:
        current_day = today or datetime.now(SEOUL).date()
        oldest_offset, newest_offset = _PERIOD_OFFSETS[period]
        oldest = (current_day - timedelta(days=oldest_offset)).isoformat()
        newest = (current_day - timedelta(days=newest_offset)).isoformat()
        clauses = [
            "substr(COALESCE(published_at, discovered_at), 1, 10) BETWEEN ? AND ?"
        ]
        parameters: list[object] = [oldest, newest]
        if item_type is not None:
            clauses.append("item_type = ?")
            parameters.append(item_type.value)
        if source is not None:
            clauses.append("source = ?")
            parameters.append(source)
        if category is not None:
            clauses.append("category = ?")
            parameters.append(category)
        if query:
            clauses.append("title LIKE ? ESCAPE '\\'")
            parameters.append(f"%{_escape_like(query)}%")
        sql = (
            "SELECT * FROM news_items WHERE "
            + " AND ".join(clauses)
            + " ORDER BY COALESCE(published_at, discovered_at) DESC, id DESC"
        )

        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [_news_record(row) for row in rows]

    def dismiss(self, item_id: int, now: datetime | None = None) -> bool:
        now_in_seoul = _seoul_datetime(now)
        now_iso = now_in_seoul.isoformat()

        with self._connection() as connection, connection:
            row = connection.execute(
                "SELECT url, source, "
                "substr(COALESCE(published_at, discovered_at), 1, 10) AS effective_date "
                "FROM news_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return False
            expires_at = datetime.combine(
                date.fromisoformat(row["effective_date"])
                + timedelta(days=SOURCES[row["source"]].ttl_days),
                time.min,
                tzinfo=SEOUL,
            ).isoformat()
            connection.execute(
                """
                INSERT INTO news_dismissals(url, dismissed_at, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    dismissed_at = excluded.dismissed_at,
                    expires_at = excluded.expires_at
                """,
                (row["url"], now_iso, expires_at),
            )
            connection.execute("DELETE FROM news_items WHERE id = ?", (item_id,))
        return True


def _deduplicate(
    items: list[CrawledNewsItem] | tuple[CrawledNewsItem, ...],
) -> tuple[dict[str, CrawledNewsItem], int]:
    deduplicated: dict[str, CrawledNewsItem] = {}
    duplicate_count = 0
    for news_item in items:
        normalized_url = normalize_url(news_item.url)
        existing = deduplicated.get(normalized_url)
        if existing is None:
            deduplicated[normalized_url] = news_item
            continue
        duplicate_count += 1
        if existing.category == _GENERAL_NEWS_CATEGORY and news_item.category != _GENERAL_NEWS_CATEGORY:
            deduplicated[normalized_url] = news_item
    return deduplicated, duplicate_count


def _seoul_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(SEOUL)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(SEOUL)


def _datetime_to_seoul_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _seoul_datetime(value).isoformat()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _news_record(row: sqlite3.Row) -> NewsRecord:
    return NewsRecord(
        id=row["id"],
        item_type=NewsItemType(row["item_type"]),
        source=row["source"],
        source_name=row["source_name"],
        category=row["category"],
        source_category=row["source_category"],
        title=row["title"],
        url=row["url"],
        published_at=_datetime_from_iso(row["published_at"]),
        discovered_at=_datetime_from_iso(row["discovered_at"]),
    )


def _datetime_from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None
