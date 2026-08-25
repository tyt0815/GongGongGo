from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.database import initialize_database
from src.news.domain import (
    CrawledNewsItem,
    NewsItemType,
    NewsPeriod,
    SaveStats,
)
from src.news.repository import NewsRepository


SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 25, 12, tzinfo=SEOUL)


@pytest.fixture
def repository(tmp_path: Path) -> NewsRepository:
    db_path = tmp_path / "news.db"
    initialize_database(db_path, tmp_path / "missing-job-posts.json")
    return NewsRepository(db_path)


def item(
    url: str,
    *,
    item_type: NewsItemType = NewsItemType.NEWSPAPER,
    source: str = "hankyung",
    source_name: str = "한국경제",
    category: str = "주요뉴스",
    source_category: str | None = None,
    title: str = "뉴스 제목",
    published_at: datetime | None = None,
) -> CrawledNewsItem:
    return CrawledNewsItem(
        item_type=item_type,
        source=source,
        source_name=source_name,
        category=category,
        source_category=source_category,
        title=title,
        url=url,
        published_at=published_at,
    )


def test_upsert_normalizes_deduplicates_and_prefers_specific_category(
    repository: NewsRepository, now: datetime
) -> None:
    stats = repository.upsert_items(
        [
            item("https://x.test/a?utm_source=n", category="주요뉴스", published_at=now),
            item("https://x.test/a", category="IT", published_at=now),
        ],
        now=now,
    )

    rows = repository.list_items(NewsPeriod.TODAY, today=now.date())

    assert stats == SaveStats(new_count=1, duplicate_count=1, suppressed_count=0)
    assert [(row.url, row.category) for row in rows] == [("https://x.test/a", "IT")]


def test_cleanup_respects_inclusive_ttls(
    repository: NewsRepository, now: datetime
) -> None:
    repository.upsert_items(
        [
            item("https://x.test/n-old", published_at=now - timedelta(days=7)),
            item("https://x.test/n-keep", published_at=now - timedelta(days=6)),
            item(
                "https://x.test/i-old",
                item_type=NewsItemType.INSTITUTION,
                source="reb",
                source_name="한국부동산원",
                published_at=now - timedelta(days=30),
            ),
        ],
        now=now,
    )

    assert repository.cleanup(now=now).item_count == 2


def test_dismissal_expires_at_original_ttl_boundary(
    repository: NewsRepository, now: datetime
) -> None:
    published = now - timedelta(days=5)
    repository.upsert_items([item("https://x.test/a", published_at=published)], now=now)
    row = repository.list_items(NewsPeriod.SEVEN_DAYS, today=now.date())[0]

    assert repository.dismiss(row.id, now=now)
    assert (
        repository.upsert_items([item(row.url, published_at=published)], now=now)
        .suppressed_count
        == 1
    )

    later = now + timedelta(days=2)
    repository.cleanup(now=later)

    assert repository.upsert_items([item(row.url, published_at=later)], now=later).new_count == 1


def test_upsert_preserves_discovery_fills_publication_and_never_downgrades_category(
    repository: NewsRepository, now: datetime
) -> None:
    discovered = now - timedelta(days=1)
    repository.upsert_items(
        [
            item(
                "https://x.test/preserve",
                category="주요뉴스",
                title="old title",
            )
        ],
        now=discovered,
    )
    repository.upsert_items(
        [
            item(
                "https://x.test/preserve",
                category="IT",
                title="new title",
                published_at=now,
            )
        ],
        now=now,
    )
    repository.upsert_items(
        [
            item(
                "https://x.test/preserve",
                category="주요뉴스",
                title="latest title",
                published_at=now - timedelta(days=2),
            )
        ],
        now=now,
    )

    row = repository.list_items(NewsPeriod.TODAY, today=now.date())[0]

    assert (row.title, row.category, row.published_at, row.discovered_at) == (
        "latest title",
        "IT",
        now,
        discovered,
    )


@pytest.mark.parametrize(
    ("period", "expected_urls"),
    [
        (NewsPeriod.TODAY, ["https://x.test/day-0"]),
        (NewsPeriod.YESTERDAY, ["https://x.test/day-1"]),
        (NewsPeriod.THREE_DAYS, ["https://x.test/day-0", "https://x.test/day-1", "https://x.test/day-2"]),
        (
            NewsPeriod.SEVEN_DAYS,
            ["https://x.test/day-0", "https://x.test/day-1", "https://x.test/day-2", "https://x.test/day-6"],
        ),
        (
            NewsPeriod.THIRTY_DAYS,
            [
                "https://x.test/day-0",
                "https://x.test/day-1",
                "https://x.test/day-2",
                "https://x.test/day-6",
                "https://x.test/day-29",
            ],
        ),
    ],
)
def test_list_items_uses_inclusive_seoul_calendar_periods(
    repository: NewsRepository, now: datetime, period: NewsPeriod, expected_urls: list[str]
) -> None:
    repository.upsert_items(
        [
            item(f"https://x.test/day-{days}", published_at=now - timedelta(days=days))
            for days in (0, 1, 2, 6, 29)
        ],
        now=now,
    )

    rows = repository.list_items(period, today=now.date())

    assert [row.url for row in rows] == expected_urls


@pytest.mark.parametrize(
    ("filters", "expected_urls"),
    [
        ({"item_type": NewsItemType.NEWSPAPER}, ["https://x.test/newspaper"]),
        ({"source": "reb"}, ["https://x.test/institution"]),
        ({"category": "IT"}, ["https://x.test/newspaper"]),
    ],
)
def test_list_items_applies_type_source_and_category_filters(
    repository: NewsRepository,
    now: datetime,
    filters: dict[str, NewsItemType | str],
    expected_urls: list[str],
) -> None:
    repository.upsert_items(
        [
            item("https://x.test/newspaper", category="IT", published_at=now),
            item(
                "https://x.test/institution",
                item_type=NewsItemType.INSTITUTION,
                source="reb",
                source_name="한국부동산원",
                category="정책",
                published_at=now,
            ),
        ],
        now=now,
    )

    rows = repository.list_items(NewsPeriod.TODAY, today=now.date(), **filters)

    assert [row.url for row in rows] == expected_urls


@pytest.mark.parametrize(
    ("query", "expected_urls"),
    [
        ("100%", ["https://x.test/percent"]),
        ("safe_name", ["https://x.test/underscore"]),
        ("%_", ["https://x.test/percent"]),
    ],
)
def test_list_items_treats_like_wildcards_as_literal_title_searches(
    repository: NewsRepository, now: datetime, query: str, expected_urls: list[str]
) -> None:
    repository.upsert_items(
        [
            item("https://x.test/percent", title="100%_match", published_at=now),
            item("https://x.test/underscore", title="safe_name", published_at=now),
            item("https://x.test/plain", title="plain title", published_at=now),
        ],
        now=now,
    )

    rows = repository.list_items(NewsPeriod.TODAY, query=query, today=now.date())

    assert [row.url for row in rows] == expected_urls


def test_list_items_uses_discovered_date_when_publication_is_missing(
    repository: NewsRepository, now: datetime
) -> None:
    repository.upsert_items([item("https://x.test/fallback")], now=now)

    rows = repository.list_items(NewsPeriod.TODAY, today=now.date())

    assert [row.url for row in rows] == ["https://x.test/fallback"]
    assert rows[0].published_at is None
    assert rows[0].discovered_at == now


def test_list_items_orders_by_newest_effective_timestamp_first(
    repository: NewsRepository, now: datetime
) -> None:
    repository.upsert_items(
        [
            item("https://x.test/older", published_at=now - timedelta(hours=2)),
            item("https://x.test/newer", published_at=now - timedelta(hours=1)),
        ],
        now=now,
    )

    rows = repository.list_items(NewsPeriod.TODAY, today=now.date())

    assert [row.url for row in rows] == ["https://x.test/newer", "https://x.test/older"]


def test_dismiss_returns_false_for_a_missing_item(
    repository: NewsRepository, now: datetime
) -> None:
    assert repository.dismiss(999, now=now) is False


def test_repository_rejects_naive_datetimes(repository: NewsRepository) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.cleanup(now=datetime(2026, 8, 25, 12))
