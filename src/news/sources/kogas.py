from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
import re
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..domain import CrawledNewsItem, NewsItemType, SourceResult
from ..http import fetch_html
from ..url_normalization import normalize_url


logger = logging.getLogger(__name__)


_SEOUL = ZoneInfo("Asia/Seoul")
_BASE_URL = "https://www.kogas.or.kr"
_LIST_URL = f"{_BASE_URL}/site/koGas/goBoard.do?boardNo=41&Key=1010202000000"


def parse_kogas_page(html: bytes) -> tuple[tuple[CrawledNewsItem, ...], int]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".list_body.webzine")
    if container is None:
        raise ValueError("KOGAS list container is missing")

    items: list[CrawledNewsItem] = []
    malformed = 0
    for row in container.select(".list_item"):
        title = _collapsed_text(row.select_one("strong.title"))
        identifier = _identifier(row)
        if title is None or identifier is None:
            malformed += 1
            continue
        items.append(
            CrawledNewsItem(
                item_type=NewsItemType.INSTITUTION,
                source="kogas",
                source_name="한국가스공사",
                category="보도자료",
                source_category=None,
                title=title,
                url=normalize_url(
                    f"{_BASE_URL}/site/koGas/goBoard.do?Key=1010202000000&boardIdx={identifier}&cbIdx=41"
                ),
                published_at=_parse_published_at(
                    _collapsed_text(row.select_one(".wdate .value"))
                ),
            )
        )
    return tuple(items), malformed


def crawl(
    fetcher: Callable[[str], bytes] = fetch_html, today: date | None = None
) -> SourceResult:
    reference_date = today or datetime.now(_SEOUL).date()
    cutoff = reference_date - timedelta(days=29)
    items: list[CrawledNewsItem] = []
    malformed = 0
    try:
        for page in range(1, 11):
            url = _LIST_URL if page == 1 else f"{_LIST_URL}&pageIndex={page}&pageOffset={(page - 1) * 10}"
            page_items, page_malformed = parse_kogas_page(fetcher(url))
            malformed += page_malformed
            items.extend(
                item
                for item in page_items
                if item.published_at is None or item.published_at.date() >= cutoff
            )
            if any(
                item.published_at is not None and item.published_at.date() < cutoff
                for item in page_items
            ):
                break
    except Exception as error:
        logger.exception("News source crawl failed: source=kogas")
        return SourceResult("kogas", error=f"{type(error).__name__}: {error}")
    return SourceResult("kogas", tuple(items), malformed)


def _identifier(row: object) -> str | None:
    for element in (row, *row.find_all(True)):
        for value in element.attrs.values():
            if not isinstance(value, str):
                continue
            match = re.search(r"readPermissionChk\(\s*['\"]?(\d+)", value)
            if match is not None:
                return match.group(1)
    return None


def _collapsed_text(element: object) -> str | None:
    if element is None:
        return None
    text = re.sub(r"\s+", " ", " ".join(element.stripped_strings)).strip()
    return text or None


def _parse_published_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    match = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})\.?", value)
    if match is None:
        return None
    try:
        return datetime(*map(int, match.groups()), tzinfo=_SEOUL)
    except ValueError:
        return None
