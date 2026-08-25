import asyncio
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.database import initialize_database
from src.news.domain import CrawledNewsItem, NewsItemType, NewsPeriod, SourceResult
from src.news.repository import NewsRepository


SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def repository(tmp_path: Path) -> NewsRepository:
    db_path = tmp_path / "news.db"
    initialize_database(db_path, tmp_path / "missing-job-posts.json")
    return NewsRepository(db_path)


def item(source: str, url: str) -> CrawledNewsItem:
    return CrawledNewsItem(
        item_type=NewsItemType.NEWSPAPER,
        source=source,
        source_name=source,
        category="IT",
        source_category=None,
        title=f"{source} article",
        url=url,
        published_at=datetime(2026, 8, 25, 12, tzinfo=SEOUL),
    )


@pytest.mark.asyncio
async def test_partial_failure_saves_peers_and_rejects_duplicate_start(
    repository: NewsRepository,
) -> None:
    from src.news.manager import NewsCrawlManager

    crawlers = {
        "hankyung": lambda: SourceResult("hankyung", (item("hankyung", "https://x.test/a"),)),
        "mk": lambda: SourceResult("mk", error="selector missing"),
    }
    manager = NewsCrawlManager(repository, crawlers=crawlers)

    assert manager.start("manual") is True
    assert manager.start("manual") is False
    await manager.wait()

    state = manager.snapshot()
    assert (state.completed_sources, state.new_count) == (2, 1)
    assert state.source_errors == {"mk": "selector missing"}
    assert [record.url for record in repository.list_items(NewsPeriod.TODAY)] == [
        "https://x.test/a"
    ]


@pytest.mark.asyncio
async def test_source_runs_off_event_loop(repository: NewsRepository) -> None:
    from src.news.manager import NewsCrawlManager

    owner = threading.get_ident()
    worker: list[int] = []
    manager = NewsCrawlManager(
        repository,
        crawlers={
            "reb": lambda: worker.append(threading.get_ident()) or SourceResult("reb")
        },
    )

    assert manager.start("startup") is True
    await manager.wait()

    assert worker[0] != owner


@pytest.mark.asyncio
async def test_cleanup_failure_stops_sources_and_exposes_run_error(
    repository: NewsRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.news.manager import NewsCrawlManager

    requested = False

    def fail_cleanup() -> None:
        raise RuntimeError("cleanup unavailable")

    def crawl() -> SourceResult:
        nonlocal requested
        requested = True
        return SourceResult("hankyung")

    monkeypatch.setattr(repository, "cleanup", fail_cleanup)
    manager = NewsCrawlManager(repository, crawlers={"hankyung": crawl})

    assert manager.start("startup") is True
    await manager.wait()

    state = manager.snapshot()
    assert requested is False
    assert state.running is False
    assert (state.completed_sources, state.total_sources) == (0, 1)
    assert state.run_error == "cleanup unavailable"


@pytest.mark.asyncio
async def test_save_failure_is_isolated_to_its_source(
    repository: NewsRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.news.manager import NewsCrawlManager

    original_upsert = repository.upsert_items

    def fail_mk_save(items: tuple[CrawledNewsItem, ...]):
        if items[0].source == "mk":
            raise RuntimeError("save failed")
        return original_upsert(items)

    monkeypatch.setattr(repository, "upsert_items", fail_mk_save)
    manager = NewsCrawlManager(
        repository,
        crawlers={
            "hankyung": lambda: SourceResult(
                "hankyung", (item("hankyung", "https://x.test/saved"),)
            ),
            "mk": lambda: SourceResult("mk", (item("mk", "https://x.test/failed"),)),
        },
    )

    assert manager.start("manual") is True
    await manager.wait()

    state = manager.snapshot()
    assert (state.completed_sources, state.new_count) == (2, 1)
    assert state.source_errors == {"mk": "save failed"}
    assert [record.url for record in repository.list_items(NewsPeriod.TODAY)] == [
        "https://x.test/saved"
    ]


@pytest.mark.asyncio
async def test_snapshot_totals_include_cleanup_and_save_statistics(
    repository: NewsRepository,
) -> None:
    from src.news.manager import NewsCrawlManager

    old_item = item("hankyung", "https://x.test/expired")
    repository.upsert_items(
        [replace(old_item, published_at=old_item.published_at - timedelta(days=8))]
    )
    duplicate_url = "https://x.test/duplicate"
    manager = NewsCrawlManager(
        repository,
        crawlers={
            "hankyung": lambda: SourceResult(
                "hankyung",
                (
                    item("hankyung", duplicate_url),
                    item("hankyung", duplicate_url),
                ),
                malformed_count=2,
            ),
        },
    )

    assert manager.start("manual") is True
    await manager.wait()

    state = manager.snapshot()
    assert (state.new_count, state.duplicate_count, state.expired_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_snapshot_is_copy_safe_and_wait_is_idempotent(repository: NewsRepository) -> None:
    from src.news.manager import NewsCrawlManager

    manager = NewsCrawlManager(
        repository,
        crawlers={"mk": lambda: SourceResult("mk", error="source error")},
    )

    assert manager.start("manual") is True
    await manager.wait()
    await manager.wait()

    snapshot = manager.snapshot()
    snapshot.source_errors["changed"] = "not shared"
    assert manager.snapshot().source_errors == {"mk": "source error"}
