import asyncio
import logging
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

    for page_number in range(1, MAX_PAGES + 1):
        await page.goto(f"{base_url}?viewType=L&page={page_number}", wait_until="networkidle")
        missing_rows = 0

        for row_index in range(1, MAX_ROWS_PER_PAGE + 1):
            try:
                element = await page.query_selector(_row_selector(row_index, page_number == 1))
            except Exception:
                logger.exception("Skipping unreadable crawler row in %s", category)
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
                logger.exception("Skipping malformed crawler row in %s", category)
                continue

            if post.link in blocked_links:
                continue
            if early_stop and post.link in known_links:
                return CategoryResult(category=category, posts=tuple(posts))
            posts.append(post)

    return CategoryResult(category=category, posts=tuple(posts))


async def crawl_categories(
    categories: Mapping[str, str],
    concurrency: int,
    known_links: set[str],
    blocked_links: set[str],
    on_result: Callable[[CategoryResult], Awaitable[None]] | None = None,
) -> tuple[CategoryResult, ...]:
    semaphore = asyncio.Semaphore(concurrency)

    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            async def run_one(category: str, url: str) -> CategoryResult:
                async with semaphore:
                    page = None
                    try:
                        page = await context.new_page()
                        return await crawl_category(page, category, url, known_links, blocked_links)
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
            for task in asyncio.as_completed(tasks):
                result = await task
                completed.append(result)
                if on_result is not None:
                    await on_result(result)

            result_by_category = {result.category: result for result in completed}
            return tuple(result_by_category[category] for category in categories)
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                if browser is not None:
                    await browser.close()
