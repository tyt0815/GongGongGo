import asyncio
import re

import pytest

from src.domain import CategoryResult, CrawledPost


class FakeElement:
    def __init__(self, title: str, link: str) -> None:
        self._title = title
        self._link = link

    async def get_attribute(self, name: str) -> str | None:
        assert name == "href"
        return self._link

    async def inner_text(self) -> str:
        return self._title


class FakePage:
    def __init__(self) -> None:
        self._pages: dict[int, list[tuple[str, str] | Exception]] = {}
        self._page_number = 0

    def add_page(self, page_number: int, rows: list[tuple[str, str] | Exception]) -> None:
        self._pages[page_number] = rows

    async def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "networkidle"
        self._page_number = int(re.search(r"[?&]page=(\d+)", url).group(1))

    async def query_selector(self, selector: str) -> FakeElement | None:
        row_number = int(re.search(r"tr:nth-child\((\d+)\)", selector).group(1))
        rows = self._pages.get(self._page_number, [])
        if row_number > len(rows):
            return None
        row = rows[row_number - 1]
        if isinstance(row, Exception):
            raise row
        return FakeElement(*row)


@pytest.fixture
def fake_page() -> FakePage:
    return FakePage()


@pytest.mark.asyncio
async def test_category_collects_rows_and_stops_at_known_link(fake_page: FakePage) -> None:
    from src.crawler import crawl_category

    fake_page.add_page(1, [
        ("[A 채용] 정규직 신입 (전산) (~8.10)", "https://example/new"),
        ("[Old 채용] 정규직 신입 (전산) (~8.11)", "https://example/known"),
    ])

    result = await crawl_category(
        fake_page, "중앙공기업", "https://example/menu",
        {"https://example/known"}, set(), early_stop=True,
    )

    assert [post.link for post in result.posts] == ["https://example/new"]
    assert result.posts[0].title == "[A 채용] 정규직 신입 (전산)"
    assert result.posts[0].deadline_raw == "~8.10"


@pytest.mark.asyncio
async def test_category_skips_blocked_links_without_stopping(fake_page: FakePage) -> None:
    from src.crawler import crawl_category

    fake_page.add_page(1, [
        ("[Blocked 채용] 정규직 신입 (전산) (~8.10)", "https://example/blocked"),
        ("[B 채용] 정규직 신입 (전산) (~8.12)", "https://example/b"),
    ])

    result = await crawl_category(
        fake_page, "중앙공기업", "https://example/menu", set(),
        {"https://example/blocked"},
    )

    assert [post.link for post in result.posts] == ["https://example/b"]


@pytest.mark.asyncio
async def test_bad_row_does_not_abort_remaining_rows(fake_page: FakePage) -> None:
    from src.crawler import crawl_category

    fake_page.add_page(1, [
        RuntimeError("bad row"),
        ("[B 채용] 정규직 신입 (전산) (~8.12)", "https://example/b"),
    ])

    result = await crawl_category(
        fake_page, "중앙공기업", "https://example/menu", set(), set(),
    )

    assert [post.link for post in result.posts] == ["https://example/b"]


class FakeBrowserPage:
    async def close(self) -> None:
        return None


class FakeContext:
    async def new_page(self) -> FakeBrowserPage:
        return FakeBrowserPage()

    async def close(self) -> None:
        return None


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()

    async def new_context(self) -> FakeContext:
        return self.context

    async def close(self) -> None:
        return None


class FakeChromium:
    async def launch(self, *, headless: bool) -> FakeBrowser:
        assert headless is True
        return FakeBrowser()


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()


class FakePlaywrightManager:
    async def __aenter__(self) -> FakePlaywright:
        return FakePlaywright()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_categories_honor_concurrency_limit(monkeypatch: pytest.MonkeyPatch, concurrency: int) -> None:
    import src.crawler as crawler

    active = 0
    maximum_active = 0

    async def fake_worker(page: FakeBrowserPage, category: str, base_url: str, known_links: set[str], blocked_links: set[str]) -> CategoryResult:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return CategoryResult(category=category)

    monkeypatch.setattr(crawler, "async_playwright", lambda: FakePlaywrightManager())
    monkeypatch.setattr(crawler, "crawl_category", fake_worker)

    categories = {f"category-{number}": f"https://example/{number}" for number in range(6)}
    await crawler.crawl_categories(categories, concurrency, set(), set())

    assert maximum_active == concurrency


@pytest.mark.asyncio
async def test_category_error_does_not_cancel_peers_and_results_are_in_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.crawler as crawler

    completed: list[str] = []

    async def fake_worker(page: FakeBrowserPage, category: str, base_url: str, known_links: set[str], blocked_links: set[str]) -> CategoryResult:
        if category == "broken":
            raise RuntimeError("category failed")
        await asyncio.sleep(0.01 if category == "slow" else 0)
        return CategoryResult(category=category, posts=(
            CrawledPost(category, category, "", base_url),
        ))

    async def record_completion(result: CategoryResult) -> None:
        completed.append(result.category)

    monkeypatch.setattr(crawler, "async_playwright", lambda: FakePlaywrightManager())
    monkeypatch.setattr(crawler, "crawl_category", fake_worker)

    results = await crawler.crawl_categories(
        {"slow": "https://example/slow", "broken": "https://example/broken", "fast": "https://example/fast"},
        2,
        set(),
        set(),
        on_result=record_completion,
    )

    assert [result.category for result in results] == ["slow", "broken", "fast"]
    assert results[1].error == "category failed"
    assert [result.category for result in results if result.posts] == ["slow", "fast"]
    assert completed == ["broken", "fast", "slow"]
