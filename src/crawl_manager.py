import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
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
        crawl: CategoryCrawler | None = None,
        target_urls: Mapping[str, str] = TARGET_URLS,
        headless: bool = True,
    ) -> None:
        self._repository = repository
        self._crawl = crawl or partial(crawl_categories, headless=headless)
        self._target_urls = dict(target_urls)
        self._task: asyncio.Task[None] | None = None
        self._snapshot_lock = asyncio.Lock()
        self._snapshot = _snapshot()

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        if self._task is not None:
            logger.info("Crawl request rejected because a run is active: trigger=%s", trigger)
            return False

        selected_categories = categories or tuple(self._target_urls)
        selected_urls = {
            category: self._target_urls[category] for category in selected_categories
        }
        logger.info(
            "Crawl scheduled: trigger=%s categories=%s",
            trigger,
            ",".join(selected_categories),
        )
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
        run_error: str | None = None

        async def on_result(result: CategoryResult) -> None:
            try:
                logger.info(
                    "Category result received: category=%s posts=%d error=%s",
                    result.category,
                    len(result.posts),
                    result.error if result.error is not None else "none",
                )
                async with self._snapshot_lock:
                    errors = dict(self._snapshot.category_errors)
                    if result.error is not None:
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
            concurrency = self._repository.get_settings().concurrency
            known_links = self._repository.list_existing_links()
            blocked_links = {
                record.link for record in self._repository.list_deleted_links()
            }
            logger.info(
                "Crawl run started: id=%d trigger=%s categories=%d concurrency=%d known_links=%d blocked_links=%d",
                run_id,
                trigger,
                len(categories),
                concurrency,
                len(known_links),
                len(blocked_links),
            )
            try:
                results = await self._crawl(
                    categories,
                    concurrency,
                    known_links,
                    blocked_links,
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
            logger.info("Saving crawl posts: successful_posts=%d", len(successful_posts))
            new_count = self._repository.upsert_crawled_posts(successful_posts)
            run_status = _run_status(results)
            self._repository.finish_crawl_run(
                run_id,
                run_status,
                new_count,
                results,
            )
            logger.info(
                "Crawl run finished: id=%d status=%s new_count=%d",
                run_id,
                run_status.value,
                new_count,
            )
        except Exception as exc:
            run_error = str(exc) or type(exc).__name__
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
                    run_error=run_error,
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
    run_error: str | None = None,
) -> CrawlSnapshot:
    return CrawlSnapshot(
        running=running,
        completed_categories=completed_categories,
        total_categories=total_categories,
        new_count=new_count,
        category_errors=MappingProxyType(dict(category_errors or {})),
        run_error=run_error,
    )
