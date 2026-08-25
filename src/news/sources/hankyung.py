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
_BASE_URL = "https://www.hankyung.com"
_SECTIONS = (
    ("정치", "정치", f"{_BASE_URL}/all-news/politics"),
    ("경제", "경제", f"{_BASE_URL}/all-news/economy"),
    ("사회", "사회", f"{_BASE_URL}/all-news/society"),
    ("IT", "IT·과학", f"{_BASE_URL}/all-news/it"),
    ("세계", "국제", f"{_BASE_URL}/all-news/international"),
    ("주요뉴스", "전체뉴스", f"{_BASE_URL}/all-news"),
)


def parse_hankyung_page(
    html: bytes, category: str
) -> tuple[tuple[CrawledNewsItem, ...], int]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("ul.allnews-list")
    if container is None:
        raise ValueError("Hankyung list container is missing")

    source_category = _source_category(category)
    items: list[CrawledNewsItem] = []
    malformed = 0
    for row in container.find_all("li", recursive=False):
        link = row.select_one("h2.news-tit > a")
        title = _collapsed_text(link)
        url = _article_url(link)
        if title is None or url is None:
            malformed += 1
            continue
        date = row.select_one("p.txt-date")
        items.append(
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="hankyung",
                source_name="한국경제",
                category=category,
                source_category=source_category,
                title=title,
                url=url,
                published_at=_parse_published_at(_collapsed_text(date)),
            )
        )
    return tuple(items), malformed


def crawl(fetcher: Callable[[str], bytes] = fetch_html) -> SourceResult:
    items: list[CrawledNewsItem] = []
    malformed = 0
    try:
        for category, _source_category_label, url in _SECTIONS:
            page_items, page_malformed = parse_hankyung_page(fetcher(url), category)
            items.extend(page_items)
            malformed += page_malformed
    except Exception as error:
        return SourceResult("hankyung", error=f"{type(error).__name__}: {error}")
    return SourceResult("hankyung", tuple(items), malformed)


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


def _parse_published_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})", value)
    if match is None:
        return None
    try:
        return datetime(*map(int, match.groups()), tzinfo=_SEOUL)
    except ValueError:
        return None
