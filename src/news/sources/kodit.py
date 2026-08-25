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
_BASE_URL = "https://www.kodit.or.kr"
_LIST_URL = f"{_BASE_URL}/kodit/na/ntt/selectNttList.do?mi=2639&bbsId=47"


def parse_kodit_page(html: bytes) -> tuple[tuple[CrawledNewsItem, ...], int]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("table:has(th.bbs_tit), table:has(td.bbs_tit)")
    if container is None:
        raise ValueError("KODIT list container is missing")
    links = container.select("td.bbs_tit a.nttInfoBtn")

    items: list[CrawledNewsItem] = []
    malformed = 0
    for link in links:
        title = _collapsed_text(link)
        identifier = link.get("data-id")
        row = link.find_parent("tr")
        if title is None or not isinstance(identifier, str) or not identifier.isdigit() or row is None:
            malformed += 1
            continue
        items.append(
            CrawledNewsItem(
                item_type=NewsItemType.INSTITUTION,
                source="kodit",
                source_name="신용보증기금",
                category="보도자료",
                source_category=None,
                title=title,
                url=normalize_url(
                    f"{_BASE_URL}/kodit/na/ntt/selectNttInfo.do?bbsId=47&mi=2639&nttSn={identifier}"
                ),
                published_at=_parse_published_at(_date_cell(link)),
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
            url = _LIST_URL if page == 1 else f"{_LIST_URL}&currPage={page}"
            page_items, page_malformed = parse_kodit_page(fetcher(url))
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
        logger.exception("News source crawl failed: source=kodit")
        return SourceResult("kodit", error=f"{type(error).__name__}: {error}")
    return SourceResult("kodit", tuple(items), malformed)


def _collapsed_text(element: object) -> str | None:
    if element is None:
        return None
    text = re.sub(r"\s+", " ", " ".join(element.stripped_strings)).strip()
    return text or None


def _date_cell(link: object) -> str | None:
    title_cell = link.find_parent("td")
    if title_cell is None:
        return None
    return _collapsed_text(title_cell.find_next_sibling("td"))


def _parse_published_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", value)
    if match is None:
        return None
    try:
        return datetime(*map(int, match.groups()), tzinfo=_SEOUL)
    except ValueError:
        return None
