import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType

from .config import TARGET_URLS
from .crawler import crawl_categories
from .domain import CategoryResult, CrawlRunStatus, CrawlSnapshot
from .repository import Repository


logger = logging.getLogger(__name__)

ResultCallback = Callable[[CategoryResult], Awaitable[None]]
CategoryCrawler = Callable[
    [Mapping[str, str], int, set[str], set[str], ResultCallback | None],
    Awaitable[tuple[CategoryResult, ...]],
]


class CrawlManager:
    def __init__(
        self,
        repository: Repository,
        *,
        crawl: CategoryCrawler = crawl_categories,
        target_urls: Mapping[str, str] = TARGET_URLS,
    ) -> None:
        self._repository = repository
        self._crawl = crawl
        self._target_urls = dict(target_urls)
        self._task: asyncio.Task[None] | None = None
        self._snapshot_lock = asyncio.Lock()
        self._snapshot = _snapshot()

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        if self._task is not None:
            return False

        selected_categories = categories or tuple(self._target_urls)
        selected_urls = {
            category: self._target_urls[category] for category in selected_categories
        }
        self._snapshot = _snapshot(running=True, total_categories=len(selected_urls))
        self._task = asyncio.create_task(self._run(trigger, selected_urls))
        return True

    def snapshot(self) -> CrawlSnapshot:
        return self._snapshot

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            await task

    async def _run(self, trigger: str, categories: Mapping[str, str]) -> None:
        run_id: int | None = None
        results: tuple[CategoryResult, ...] = ()
        new_count = 0

        async def on_result(result: CategoryResult) -> None:
            try:
                async with self._snapshot_lock:
                    errors = dict(self._snapshot.category_errors)
                    if result.error:
                        errors[result.category] = result.error
                    self._snapshot = _snapshot(
                        running=True,
                        completed_categories=self._snapshot.completed_categories + 1,
                        total_categories=self._snapshot.total_categories,
                        new_count=self._snapshot.new_count,
                        category_errors=errors,
                    )
            except Exception:
                logger.exception("Could not update crawl progress for %s", result.category)

        try:
            run_id = self._repository.create_crawl_run(trigger)
            try:
                results = await self._crawl(
                    categories,
                    self._repository.get_settings().concurrency,
                    set(),
                    {record.link for record in self._repository.list_deleted_links()},
                    on_result,
                )
            except Exception as exc:
                logger.exception("Crawl run failed before category results returned")
                results = tuple(
                    CategoryResult(category=category, error=str(exc))
                    for category in categories
                )

            successful_posts = [
                post for result in results if result.error is None for post in result.posts
            ]
            new_count = self._repository.upsert_crawled_posts(successful_posts)
            self._repository.finish_crawl_run(
                run_id,
                _run_status(results),
                new_count,
                results,
            )
        except Exception:
            logger.exception("Could not finish crawl run")
            if run_id is not None:
                try:
                    self._repository.finish_crawl_run(
                        run_id, CrawlRunStatus.FAILED, 0, results
                    )
                except Exception:
                    logger.exception("Could not record failed crawl run")
        finally:
            errors = {
                result.category: result.error
                for result in results
                if result.error is not None
            }
            async with self._snapshot_lock:
                self._snapshot = _snapshot(
                    completed_categories=len(results),
                    total_categories=len(categories),
                    new_count=new_count,
                    category_errors=errors,
                )
            self._task = None


def _run_status(results: tuple[CategoryResult, ...]) -> CrawlRunStatus:
    failures = sum(result.error is not None for result in results)
    if failures == 0:
        return CrawlRunStatus.SUCCEEDED
    if failures == len(results):
        return CrawlRunStatus.FAILED
    return CrawlRunStatus.PARTIAL


def _snapshot(
    *,
    running: bool = False,
    completed_categories: int = 0,
    total_categories: int = 0,
    new_count: int = 0,
    category_errors: Mapping[str, str] | None = None,
) -> CrawlSnapshot:
    return CrawlSnapshot(
        running=running,
        completed_categories=completed_categories,
        total_categories=total_categories,
        new_count=new_count,
        category_errors=MappingProxyType(dict(category_errors or {})),
    )
