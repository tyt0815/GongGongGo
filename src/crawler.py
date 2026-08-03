import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from playwright.async_api import async_playwright

from .domain import CategoryResult, CrawledPost


logger = logging.getLogger(__name__)
MAX_PAGES = 29
MAX_ROWS_PER_PAGE = 14
MISSING_ROW_LIMIT = 2


def _row_selector(row_index: int, first_page: bool) -> str:
    tbody = "tbody:nth-child(6)" if first_page else "tbody"
    return (
        "#cafe_content > div.article-board > table > "
        f"{tbody} > tr:nth-child({row_index}) > td:nth-child(2) > div > div > a"
    )


def _split_source_title_and_deadline(source_title: str) -> tuple[str, str]:
    deadline_start = source_title.rfind("(")
    deadline_end = source_title.rfind(")")
    if deadline_start == -1 or deadline_end <= deadline_start:
        return source_title.strip(), ""
    return source_title[:deadline_start].strip(), source_title[deadline_start + 1 : deadline_end].strip()


async def _extract_row(element: Any, category: str) -> CrawledPost:
    link = await element.get_attribute("href")
    if not link:
        raise ValueError("post link is missing")

    source_title = (await element.inner_text()).strip()
    if not source_title:
        raise ValueError("post title is missing")

    title, deadline_raw = _split_source_title_and_deadline(source_title)
    return CrawledPost(
        category=category,
        title=title,
        deadline_raw=deadline_raw,
        link=link,
    )

async def crawl_category(
    page: Any,
    category: str,
    base_url: str,
    known_links: set[str],
    blocked_links: set[str],
    early_stop: bool = True,
) -> CategoryResult:
    posts: list[CrawledPost] = []

    # 쿼리 문자열을 제거한 링크 집합을 미리 생성
    normalized_known_links = {
        link.partition("?")[0]
        for link in known_links
    }

    logger.info("Category crawl started: category=%s", category)

    for page_number in range(1, MAX_PAGES + 1):
        logger.info(
            "Page load started: category=%s page=%d",
            category,
            page_number,
        )

        load_started = time.monotonic()

        await page.goto(
            f"{base_url}?viewType=L&page={page_number}",
            wait_until="networkidle",
        )

        logger.info(
            "Page load completed: category=%s page=%d elapsed=%.1fs",
            category,
            page_number,
            time.monotonic() - load_started,
        )

        missing_rows = 0

        # 첫 번째 공고가 나타날 때까지 최대 5초간 확인
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            element = await page.query_selector(
                _row_selector(1, page_number == 1)
            )

            if element is not None:
                break

            await asyncio.sleep(0.1)

        for row_index in range(1, MAX_ROWS_PER_PAGE + 1):
            try:
                element = await page.query_selector(
                    _row_selector(row_index, page_number == 1)
                )
            except Exception:
                logger.exception(
                    "Skipping unreadable crawler row in %s",
                    category,
                )
                continue

            if element is None:
                missing_rows += 1

                if missing_rows >= MISSING_ROW_LIMIT:
                    break

                continue

            missing_rows = 0

            try:
                post = await _extract_row(element, category)
            except Exception:
                logger.exception(
                    "Skipping malformed crawler row in %s",
                    category,
                )
                continue

            if post.link in blocked_links:
                continue

            # 비교할 때만 ? 뒤의 쿼리 문자열을 제거
            normalized_post_link = post.link.partition("?")[0]
            
            if early_stop and normalized_post_link in normalized_known_links:
                logger.info(
                    "Known post reached: category=%s page=%d link=%s collected=%d",
                    category,
                    page_number,
                    normalized_post_link,
                    len(posts),
                )
                return CategoryResult(
                    category=category,
                    posts=tuple(posts),
                )

            posts.append(post)

        logger.info(
            "Page scan completed: category=%s page=%d collected=%d",
            category,
            page_number,
            len(posts),
        )

    logger.info(
        "Category crawl completed: category=%s collected=%d",
        category,
        len(posts),
    )

    return CategoryResult(
        category=category,
        posts=tuple(posts),
    )


async def crawl_categories(
    categories: Mapping[str, str],
    concurrency: int,
    known_links: set[str],
    blocked_links: set[str],
    on_result: Callable[[CategoryResult], Awaitable[None]] | None = None,
    *,
    headless: bool = True,
) -> tuple[CategoryResult, ...]:
    semaphore = asyncio.Semaphore(concurrency)
    logger.info(
        "Crawler starting: categories=%d concurrency=%d known_links=%d blocked_links=%d",
        len(categories),
        concurrency,
        len(known_links),
        len(blocked_links),
    )

    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            logger.info("Chromium launch started: headless=%s", headless)
            browser = await playwright.chromium.launch(headless=headless)
            logger.info("Chromium launch completed")
            context = await browser.new_context()
            logger.info("Browser context created")

            async def run_one(category: str, url: str) -> CategoryResult:
                logger.info("Category waiting for worker: category=%s", category)
                async with semaphore:
                    logger.info("Category worker started: category=%s", category)
                    page = None
                    try:
                        page = await context.new_page()
                        result = await crawl_category(
                            page, category, url, known_links, blocked_links
                        )
                        logger.info(
                            "Category worker completed: category=%s posts=%d",
                            category,
                            len(result.posts),
                        )
                        return result
                    except Exception as exc:
                        logger.exception("Crawler category failed: %s", category)
                        return CategoryResult(category=category, error=str(exc))
                    finally:
                        if page is not None:
                            try:
                                await page.close()
                            except Exception:
                                logger.exception("Could not close crawler page for %s", category)

            tasks = [
                asyncio.create_task(run_one(category, url))
                for category, url in categories.items()
            ]
            completed: list[CategoryResult] = []
            try:
                for task in asyncio.as_completed(tasks):
                    result = await task
                    completed.append(result)
                    if on_result is not None:
                        await on_result(result)
            finally:
                unfinished = [task for task in tasks if not task.done()]
                for task in unfinished:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            result_by_category = {result.category: result for result in completed}
            logger.info("All category workers completed: categories=%d", len(completed))
            return tuple(result_by_category[category] for category in categories)
        finally:
            try:
                if context is not None:
                    logger.info("Browser context close started")
                    await context.close()
                    logger.info("Browser context close completed")
            finally:
                if browser is not None:
                    logger.info("Chromium close started")
                    await browser.close()
                    logger.info("Chromium close completed")
