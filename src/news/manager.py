import asyncio
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace

from .domain import NewsCrawlSnapshot, SaveStats, SourceResult
from .repository import NewsRepository
from .sources import SOURCE_CRAWLERS


logger = logging.getLogger(__name__)

SourceCrawler = Callable[[], SourceResult]


class NewsCrawlManager:
    def __init__(
        self,
        repository: NewsRepository,
        *,
        crawlers: Mapping[str, SourceCrawler] = SOURCE_CRAWLERS,
    ) -> None:
        self._repository = repository
        self._crawlers = dict(crawlers)
        self._task: asyncio.Task[None] | None = None
        self._snapshot_lock = threading.Lock()
        self._snapshot = NewsCrawlSnapshot()

    def start(self, trigger: str) -> bool:
        with self._snapshot_lock:
            if self._task is not None:
                logger.info("News crawl request rejected because a run is active: trigger=%s", trigger)
                return False
            self._snapshot = NewsCrawlSnapshot(
                running=True,
                total_sources=len(self._crawlers),
            )
            self._task = asyncio.create_task(self._run(trigger, self._crawlers.copy()))

        logger.info(
            "News crawl scheduled: trigger=%s sources=%s",
            trigger,
            ",".join(self._crawlers),
        )
        return True

    def snapshot(self) -> NewsCrawlSnapshot:
        with self._snapshot_lock:
            return replace(self._snapshot, source_errors=dict(self._snapshot.source_errors))

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            await task

    async def _run(self, trigger: str, crawlers: Mapping[str, SourceCrawler]) -> None:
        run_error: str | None = None
        try:
            cleanup = await asyncio.to_thread(self._repository.cleanup)
            self._replace_snapshot(expired_count=cleanup.item_count)
            logger.info(
                "News crawl started: trigger=%s sources=%d expired=%d dismissed=%d",
                trigger,
                len(crawlers),
                cleanup.item_count,
                cleanup.dismissal_count,
            )

            tasks = [
                asyncio.create_task(self._crawl_source(source, crawler))
                for source, crawler in crawlers.items()
            ]
            for completed in asyncio.as_completed(tasks):
                source, result = await completed
                await self._save_source_result(source, result)
        except Exception as error:
            run_error = _error_message(error)
            logger.exception("News crawl failed before all source results completed")
        finally:
            self._replace_snapshot(running=False, run_error=run_error)
            state = self.snapshot()
            logger.info(
                "News crawl finished: running=%s completed=%d/%d new=%d duplicate=%d expired=%d errors=%d run_error=%s",
                state.running,
                state.completed_sources,
                state.total_sources,
                state.new_count,
                state.duplicate_count,
                state.expired_count,
                len(state.source_errors),
                state.run_error if state.run_error is not None else "none",
            )
            with self._snapshot_lock:
                self._task = None

    async def _crawl_source(
        self, source: str, crawler: SourceCrawler
    ) -> tuple[str, SourceResult]:
        logger.info("News source started: source=%s", source)
        try:
            return source, await asyncio.to_thread(crawler)
        except Exception as error:
            message = _error_message(error)
            logger.exception("News source failed: source=%s", source)
            return source, SourceResult(source, error=message)

    async def _save_source_result(self, source: str, result: SourceResult) -> None:
        error = _normalize_source_error(result.error)
        stats = SaveStats()
        try:
            if error is None:
                stats = await asyncio.to_thread(self._repository.upsert_items, result.items)
                logger.info(
                    "News source saved: source=%s items=%d malformed=%d new=%d duplicate=%d suppressed=%d",
                    source,
                    len(result.items),
                    result.malformed_count,
                    stats.new_count,
                    stats.duplicate_count,
                    stats.suppressed_count,
                )
            else:
                logger.warning(
                    "News source failed: source=%s malformed=%d error=%s",
                    source,
                    result.malformed_count,
                    error,
                )
        except Exception as save_error:
            error = _error_message(save_error)
            logger.exception("Could not save news source result: source=%s", source)
        finally:
            self._complete_source(source, stats, error)

    def _complete_source(self, source: str, stats: SaveStats, error: str | None) -> None:
        with self._snapshot_lock:
            errors = dict(self._snapshot.source_errors)
            if error is not None:
                errors[source] = error
            self._snapshot = replace(
                self._snapshot,
                completed_sources=self._snapshot.completed_sources + 1,
                new_count=self._snapshot.new_count + stats.new_count,
                duplicate_count=self._snapshot.duplicate_count + stats.duplicate_count,
                source_errors=errors,
            )

    def _replace_snapshot(self, **changes: object) -> None:
        with self._snapshot_lock:
            self._snapshot = replace(
                self._snapshot,
                source_errors=dict(self._snapshot.source_errors),
                **changes,
            )


def _error_message(error: Exception) -> str:
    return str(error) or type(error).__name__


def _normalize_source_error(error: str | None) -> str | None:
    if error is None:
        return None
    return error.strip() or "source failed without an error message"
