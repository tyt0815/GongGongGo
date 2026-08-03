import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import pytest

from src.database import connect, initialize_database
from src.domain import CategoryResult, CrawledPost, CrawlRunStatus, Settings
from src.repository import Repository


ResultCallback = Callable[[CategoryResult], Awaitable[None]]


def make_post(link: str) -> CrawledPost:
    return CrawledPost(
        category="central",
        title="[Agency] full-time (software)",
        deadline_raw="2026.08.10",
        link=link,
    )


class FakeCrawler:
    def __init__(self) -> None:
        self.results: tuple[CategoryResult, ...] = ()
        self.calls: list[tuple[dict[str, str], int, set[str], set[str]]] = []

    async def __call__(
        self,
        categories: Mapping[str, str],
        concurrency: int,
        known_links: set[str],
        blocked_links: set[str],
        on_result: ResultCallback | None = None,
    ) -> tuple[CategoryResult, ...]:
        self.calls.append((dict(categories), concurrency, known_links, blocked_links))
        for result in self.results:
            if on_result is not None:
                await on_result(result)
        return self.results


class BlockingCrawler(FakeCrawler):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self,
        categories: Mapping[str, str],
        concurrency: int,
        known_links: set[str],
        blocked_links: set[str],
        on_result: ResultCallback | None = None,
    ) -> tuple[CategoryResult, ...]:
        self.calls.append((dict(categories), concurrency, known_links, blocked_links))
        self.started.set()
        await self.release.wait()
        return self.results


class ReturnMarkingCrawler(FakeCrawler):
    def __init__(self) -> None:
        super().__init__()
        self.returned = False

    async def __call__(
        self,
        categories: Mapping[str, str],
        concurrency: int,
        known_links: set[str],
        blocked_links: set[str],
        on_result: ResultCallback | None = None,
    ) -> tuple[CategoryResult, ...]:
        results = await super().__call__(
            categories, concurrency, known_links, blocked_links, on_result
        )
        self.returned = True
        return results


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    initialize_database(db_path, json_path)
    return Repository(db_path)


@pytest.fixture
def target_urls() -> dict[str, str]:
    return {"central": "https://example.test/central", "local": "https://example.test/local"}


@pytest.fixture
def fake_crawler() -> FakeCrawler:
    return FakeCrawler()


@pytest.fixture
def manager(repository: Repository, fake_crawler: FakeCrawler, target_urls: dict[str, str]):
    from src.crawl_manager import CrawlManager

    return CrawlManager(repository, crawl=fake_crawler, target_urls=target_urls)


@pytest.mark.asyncio
async def test_start_rejects_a_duplicate_while_a_run_is_active(
    repository: Repository, target_urls: dict[str, str]
) -> None:
    from src.crawl_manager import CrawlManager

    blocking_crawler = BlockingCrawler()
    manager = CrawlManager(repository, crawl=blocking_crawler, target_urls=target_urls)

    assert manager.start("manual") is True
    await blocking_crawler.started.wait()
    assert manager.start("manual") is False

    blocking_crawler.release.set()
    await manager.wait()


@pytest.mark.asyncio
async def test_partial_failure_saves_successes_and_exposes_category_error(
    manager, repository: Repository, fake_crawler: FakeCrawler
) -> None:
    fake_crawler.results = (
        CategoryResult("central", posts=(make_post("https://example.test/a"),)),
        CategoryResult("local", error="timeout"),
    )

    assert manager.start("manual") is True
    await manager.wait()

    assert repository.get_post("https://example.test/a") is not None
    assert manager.snapshot().category_errors == {"local": "timeout"}
    assert repository.latest_crawl_run().status is CrawlRunStatus.PARTIAL


@pytest.mark.asyncio
async def test_uses_concurrency_persisted_in_repository(
    manager, repository: Repository, fake_crawler: FakeCrawler
) -> None:
    repository.update_settings(Settings(concurrency=4))

    assert manager.start("manual", ("central",)) is True
    await manager.wait()

    assert fake_crawler.calls[0][1] == 4


@pytest.mark.asyncio
async def test_startup_trigger_is_persisted_in_run_history(
    manager, repository: Repository
) -> None:
    assert manager.start("startup", ("central",)) is True
    await manager.wait()

    with connect(repository.db_path) as connection:
        row = connection.execute(
            "SELECT trigger, status FROM crawl_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert tuple(row) == ("startup", "succeeded")


@pytest.mark.asyncio
async def test_retry_crawls_exactly_the_requested_category(
    manager, fake_crawler: FakeCrawler
) -> None:
    assert manager.start("retry", ("local",)) is True
    await manager.wait()

    assert fake_crawler.calls[0][0] == {"local": "https://example.test/local"}


@pytest.mark.asyncio
async def test_upserts_once_only_after_all_category_results_return(
    repository: Repository, target_urls: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.crawl_manager import CrawlManager

    crawler = ReturnMarkingCrawler()
    crawler.results = (
        CategoryResult("central", posts=(make_post("https://example.test/a"),)),
        CategoryResult("local", posts=(make_post("https://example.test/b"),)),
    )
    manager = CrawlManager(repository, crawl=crawler, target_urls=target_urls)

    upsert_calls: list[list[CrawledPost]] = []
    original_upsert = repository.upsert_crawled_posts

    def record_upsert(posts: list[CrawledPost]) -> int:
        assert crawler.returned is True
        upsert_calls.append(posts)
        return original_upsert(posts)

    monkeypatch.setattr(repository, "upsert_crawled_posts", record_upsert)

    assert manager.start("manual") is True
    await manager.wait()

    assert [[post.link for post in posts] for posts in upsert_calls] == [
        ["https://example.test/a", "https://example.test/b"]
    ]


@pytest.mark.asyncio
async def test_history_creation_failure_does_not_leave_manager_active(
    manager, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_create = repository.create_crawl_run
    attempts = 0

    def fail_to_create_history(trigger: str) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        return original_create(trigger)

    monkeypatch.setattr(repository, "create_crawl_run", fail_to_create_history)

    assert manager.start("manual") is True
    await manager.wait()
    assert manager.snapshot().running is False
    assert manager.start("manual") is True
    await manager.wait()
