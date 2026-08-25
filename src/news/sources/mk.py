from collections.abc import Callable
from datetime import datetime
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..domain import CrawledNewsItem, NewsItemType, SourceResult
from ..http import fetch_html
from ..url_normalization import normalize_url


_SEOUL = ZoneInfo("Asia/Seoul")
_BASE_URL = "https://www.mk.co.kr"
_SECTIONS = (
    ("정치", "정치", f"{_BASE_URL}/news/politics/"),
    ("경제", "경제", f"{_BASE_URL}/news/economy/"),
    ("사회", "사회", f"{_BASE_URL}/news/society/"),
    ("IT", "IT", f"{_BASE_URL}/news/it/"),
    ("세계", "국제", f"{_BASE_URL}/news/world/"),
)
_MAIN_URL = f"{_BASE_URL}/news/"


def parse_mk_page(html: bytes, category: str) -> tuple[tuple[CrawledNewsItem, ...], int]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("ul#list_area")
    if container is None:
        raise ValueError("MK list container is missing")

    source_category = _source_category(category)
    items: list[CrawledNewsItem] = []
    malformed = 0
    for row in container.find_all("li", class_="article_list", recursive=False):
        link = row.select_one("a.news_item")
        title = _collapsed_text(row.select_one(".art_area h4"))
        url = _article_url(link)
        if title is None or url is None:
            malformed += 1
            continue
        items.append(
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="mk",
                source_name="매일경제",
                category=category,
                source_category=source_category,
                title=title,
                url=url,
                published_at=_parse_published_at(row.select(".time_area span")),
            )
        )
    return tuple(items), malformed


def parse_mk_main_page(
    html: bytes, category: str
) -> tuple[tuple[CrawledNewsItem, ...], int]:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select(
        "a.news_item[data-section='headline'], a.news_item[data-section='main']"
    )
    if not links:
        raise ValueError("MK main container is missing")

    items: list[CrawledNewsItem] = []
    malformed = 0
    for link in links:
        section = link.get("data-section")
        title = _collapsed_text(link.select_one(".art_area h4"))
        url = _article_url(link)
        if title is None or url is None:
            malformed += 1
            continue
        items.append(
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="mk",
                source_name="매일경제",
                category=category,
                source_category="헤드라인" if section == "headline" else "주요뉴스",
                title=title,
                url=url,
                published_at=None,
            )
        )
    return tuple(items), malformed


def crawl(fetcher: Callable[[str], bytes] = fetch_html) -> SourceResult:
    items: list[CrawledNewsItem] = []
    malformed = 0
    try:
        for category, _source_category_label, url in _SECTIONS:
            page_items, page_malformed = parse_mk_page(fetcher(url), category)
            items.extend(page_items)
            malformed += page_malformed
        page_items, page_malformed = parse_mk_main_page(fetcher(_MAIN_URL), "주요뉴스")
        items.extend(page_items)
        malformed += page_malformed
    except Exception as error:
        return SourceResult("mk", error=f"{type(error).__name__}: {error}")
    return SourceResult("mk", tuple(items), malformed)


def _source_category(category: str) -> str:
    for internal, source_category, _url in _SECTIONS:
        if category == internal:
            return source_category
    return category


def _collapsed_text(element: object) -> str | None:
    if element is None:
        return None
    text = re.sub(r"\s+", " ", " ".join(element.stripped_strings)).strip()
    return text or None


def _article_url(link: object) -> str | None:
    if link is None:
        return None
    href = link.get("href")
    if not isinstance(href, str) or not href.strip():
        return None
    absolute_url = urljoin(_BASE_URL, href)
    parsed = urlsplit(absolute_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    return normalize_url(absolute_url)


def _parse_published_at(values: list[object]) -> datetime | None:
    for value in values:
        text = _collapsed_text(value)
        if text is None:
            continue
        match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
        if match is None:
            continue
        try:
            return datetime(*map(int, match.groups()), tzinfo=_SEOUL)
        except ValueError:
            return None
    return None
