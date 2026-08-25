from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from src.news import http
from src.news.sources import hankyung, mk
from src.news.sources.hankyung import parse_hankyung_page
from src.news.sources.mk import parse_mk_main_page, parse_mk_page


@pytest.fixture
def fixtures() -> Callable[[str], bytes]:
    directory = Path(__file__).parent / "fixtures"
    return lambda name: (directory / name).read_bytes()


def test_fetch_html_uses_identified_bounded_request() -> None:
    calls: list[tuple[object, float]] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 5 * 1024 * 1024 + 1
            return b"<html></html>"

    def open_request(request: object, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    with patch("src.news.http.urlopen", open_request):
        assert http.fetch_html("https://example.test/news") == b"<html></html>"

    request, timeout = calls[0]
    assert request.full_url == "https://example.test/news"  # type: ignore[union-attr]
    assert request.get_header("User-agent") == "GongGongGo/1.0 personal-local-news-reader"  # type: ignore[union-attr]
    assert request.get_header("Accept-language") == "ko-KR,ko;q=0.9"  # type: ignore[union-attr]
    assert timeout == 15


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (500, b"failure", "unexpected HTTP status"),
        (200, b"x" * (5 * 1024 * 1024 + 1), "response body exceeds"),
    ],
    ids=["non-2xx", "oversize"],
)
def test_fetch_html_rejects_invalid_responses(
    status: int, body: bytes, message: str
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return body

    response = Response()
    response.status = status
    with patch("src.news.http.urlopen", return_value=response):
        with pytest.raises(ValueError, match=message):
            http.fetch_html("https://example.test/news")


def test_hankyung_list_parser(fixtures: Callable[[str], bytes]) -> None:
    items, malformed = parse_hankyung_page(fixtures("hankyung.html"), "IT")

    assert items[0].url == "https://www.hankyung.com/article/202608251361i"
    assert items[0].title == "공공부문 AI 전환"
    assert items[0].published_at is not None
    assert items[0].published_at.isoformat() == "2026-08-25T14:05:00+09:00"
    assert items[1].published_at is None
    assert malformed == 1


def test_mk_list_parser(fixtures: Callable[[str], bytes]) -> None:
    items, malformed = parse_mk_page(fixtures("mk.html"), "IT")

    assert items[0].url == "https://www.mk.co.kr/news/it/202608251361i"
    assert items[0].title == "공공부문 AI 전환"
    assert items[0].published_at is not None
    assert items[0].published_at.isoformat() == "2026-08-25T00:00:00+09:00"
    assert items[1].published_at is None
    assert malformed == 1


def test_mk_main_parser_keeps_curated_items_with_discovery_fallback(
    fixtures: Callable[[str], bytes],
) -> None:
    items, malformed = parse_mk_main_page(fixtures("mk.html"), "주요뉴스")

    assert [item.category for item in items] == ["주요뉴스", "주요뉴스"]
    assert [item.published_at for item in items] == [None, None]
    assert malformed == 0


def test_mk_main_parser_rejects_a_page_without_curated_sections() -> None:
    with pytest.raises(ValueError, match="main container"):
        parse_mk_main_page(
            b"<a class='news_item' data-section='sports' href='/news/sports/1'>"
            b"<div class='art_area'><h4>sports</h4></div></a>",
            "주요뉴스",
        )


@pytest.mark.parametrize(
    ("parser", "html"),
    [
        (parse_hankyung_page, b"<ul class='allnews-list'></ul>"),
        (parse_mk_page, b"<ul id='list_area'></ul>"),
    ],
)
def test_list_parsers_accept_empty_expected_containers(
    parser: Callable[[bytes, str], tuple[tuple[object, ...], int]], html: bytes
) -> None:
    assert parser(html, "IT") == ((), 0)


@pytest.mark.parametrize(
    ("crawl", "source"),
    [(hankyung.crawl, "hankyung"), (mk.crawl, "mk")],
)
def test_crawl_reports_missing_expected_container(
    crawl: Callable[..., object], source: str
) -> None:
    result = crawl(fetcher=lambda _url: b"<html></html>")

    assert result.source == source
    assert result.items == ()
    assert result.error is not None
    assert "container" in result.error


@pytest.mark.parametrize(
    ("crawl", "source"),
    [(hankyung.crawl, "hankyung"), (mk.crawl, "mk")],
)
def test_crawl_isolates_fetch_exceptions(crawl: Callable[..., object], source: str) -> None:
    result = crawl(fetcher=lambda _url: (_ for _ in ()).throw(RuntimeError("offline")))

    assert result.source == source
    assert result.items == ()
    assert result.error is not None
    assert "offline" in result.error


def test_specific_categories_are_fetched_before_main(
    fixtures: Callable[[str], bytes],
) -> None:
    calls: list[str] = []
    result = hankyung.crawl(
        fetcher=lambda url: calls.append(url) or fixtures("hankyung.html")
    )

    assert result.error is None
    assert calls == [
        "https://www.hankyung.com/all-news/politics",
        "https://www.hankyung.com/all-news/economy",
        "https://www.hankyung.com/all-news/society",
        "https://www.hankyung.com/all-news/it",
        "https://www.hankyung.com/all-news/international",
        "https://www.hankyung.com/all-news",
    ]


def test_mk_specific_categories_are_fetched_before_main(
    fixtures: Callable[[str], bytes],
) -> None:
    calls: list[str] = []
    result = mk.crawl(fetcher=lambda url: calls.append(url) or fixtures("mk.html"))

    assert result.error is None
    assert calls == [
        "https://www.mk.co.kr/news/politics/",
        "https://www.mk.co.kr/news/economy/",
        "https://www.mk.co.kr/news/society/",
        "https://www.mk.co.kr/news/it/",
        "https://www.mk.co.kr/news/world/",
        "https://www.mk.co.kr/news/",
    ]
