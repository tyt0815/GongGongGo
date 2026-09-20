from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import json
import logging
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..domain import CrawledNewsItem, NewsItemType, SourceResult
from ..http import fetch_html
from ..url_normalization import normalize_url


logger = logging.getLogger(__name__)


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
_MAX_ARTICLE_METADATA_REQUESTS = 100


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
        "a[data-section='headline'], a.news_item[data-section='main']"
    )
    if not links:
        raise ValueError("MK main container is missing")

    items: list[CrawledNewsItem] = []
    malformed = 0
    for link in links:
        section = link.get("data-section")
        title_element = link.select_one(
            ".art_area h4, h3.headline_tit, h3.news_tit"
        )
        if title_element is not None:
            for badge in title_element.select(".t_badge"):
                badge.decompose()
        title = _collapsed_text(title_element)
        if title is None and link.find("img") is not None:
            continue
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
        items = _enrich_missing_published_at(items, fetcher)
    except Exception as error:
        logger.exception("News source crawl failed: source=mk")
        return SourceResult("mk", error=f"{type(error).__name__}: {error}")
    return SourceResult("mk", tuple(items), malformed)


def parse_mk_article_published_at(html: bytes) -> datetime | None:
    soup = BeautifulSoup(html, "html.parser")
    metadata = soup.select_one(
        'meta[property="article:published_time"], meta[name="article:published_time"]'
    )
    if metadata is not None:
        published_at = _parse_iso_datetime(metadata.get("content"))
        if published_at is not None:
            return published_at

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        value = _find_json_value(payload, "datePublished")
        published_at = _parse_iso_datetime(value)
        if published_at is not None:
            return published_at
    return None


def _enrich_missing_published_at(
    items: list[CrawledNewsItem], fetcher: Callable[[str], bytes]
) -> list[CrawledNewsItem]:
    known_dates = {
        item.url: item.published_at for item in items if item.published_at is not None
    }
    fetched_dates: dict[str, datetime | None] = {}
    enriched: list[CrawledNewsItem] = []
    request_count = 0
    for item in items:
        if item.published_at is not None:
            enriched.append(item)
            continue
        published_at = known_dates.get(item.url)
        if published_at is None and item.url not in fetched_dates:
            if request_count >= _MAX_ARTICLE_METADATA_REQUESTS:
                fetched_dates[item.url] = None
            else:
                request_count += 1
                try:
                    fetched_dates[item.url] = parse_mk_article_published_at(
                        fetcher(item.url)
                    )
                except Exception:
                    logger.warning(
                        "MK article publication metadata fetch failed: url=%s",
                        item.url,
                        exc_info=True,
                    )
                    fetched_dates[item.url] = None
        published_at = published_at or fetched_dates.get(item.url)
        if published_at is not None:
            known_dates[item.url] = published_at
            item = replace(item, published_at=published_at)
        enriched.append(item)
    return enriched


def _find_json_value(value: object, key: str) -> object:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_json_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_json_value(child, key)
            if found is not None:
                return found
    return None


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SEOUL)
    return parsed.astimezone(_SEOUL)


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
    try:
        parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or not _is_approved_host(parsed.hostname, "mk.co.kr")
    ):
        return None
    return normalize_url(absolute_url)


def _is_approved_host(hostname: str, domain: str) -> bool:
    host = hostname.casefold()
    return host == domain or host.endswith(f".{domain}")


def _parse_published_at(values: list[object]) -> datetime | None:
    text = " ".join(filter(None, (_collapsed_text(value) for value in values)))
    match = re.search(
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\D+(\d{1,2}):(\d{2}))?",
        text,
    )
    if match is None:
        return None
    parts = [int(value) for value in match.groups() if value is not None]
    try:
        return datetime(*parts, tzinfo=_SEOUL)
    except ValueError:
        return None
