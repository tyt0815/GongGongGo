from collections.abc import Callable
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from src.news import http
from src.news.sources import hankyung, kodit, kogas, mk, reb
from src.news.sources.hankyung import parse_hankyung_page
from src.news.sources.mk import (
    parse_mk_article_published_at,
    parse_mk_main_page,
    parse_mk_page,
)


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
    assert items[0].published_at.isoformat() == "2026-08-25T14:05:00+09:00"
    assert items[1].published_at is None
    assert malformed == 1


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            b'<meta property="article:published_time" content="2026-08-25T17:43:33+09:00">',
            "2026-08-25T17:43:33+09:00",
        ),
        (
            b'<script type="application/ld+json">{"datePublished":"2026-08-25T08:43:33Z"}</script>',
            "2026-08-25T17:43:33+09:00",
        ),
        (b'<meta property="article:published_time" content="not-a-date">', None),
    ],
)
def test_mk_article_parser_reads_official_publication_metadata(
    html: bytes, expected: str | None
) -> None:
    published_at = parse_mk_article_published_at(html)

    assert (published_at.isoformat() if published_at is not None else None) == expected


def test_mk_main_parser_keeps_curated_items_with_discovery_fallback(
    fixtures: Callable[[str], bytes],
) -> None:
    items, malformed = parse_mk_main_page(fixtures("mk.html"), "주요뉴스")

    assert [item.category for item in items] == ["주요뉴스", "주요뉴스"]
    assert [item.published_at for item in items] == [None, None]
    assert malformed == 0


def test_mk_main_parser_accepts_current_headline_markup(
    fixtures: Callable[[str], bytes],
) -> None:
    items, malformed = parse_mk_main_page(
        fixtures("mk_main_current.html"), "주요뉴스"
    )

    assert [item.title for item in items] == [
        "연금 수령액 절반은 월 50만원 미만",
        "공공부문 AI 전환 가속",
    ]
    assert [item.url for item in items] == [
        "https://www.mk.co.kr/news/economy/12135757",
        "https://www.mk.co.kr/news/it/12135882",
    ]
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
    ("parser", "html"),
    [
        (
            parse_hankyung_page,
            b"<ul class='allnews-list'><li><h2 class='news-tit'>"
            b"<a href='https://www.hankyung.com.evil.test/article/1'>offsite</a>"
            b"</h2></li></ul>",
        ),
        (
            parse_mk_page,
            b"<ul id='list_area'><li class='article_list'>"
            b"<a class='news_item' href='https://evil.test/news/1'>"
            b"<div class='art_area'><h4>offsite</h4></div></a></li></ul>",
        ),
        (
            parse_hankyung_page,
            b"<ul class='allnews-list'><li><h2 class='news-tit'>"
            b"<a href='https://www.hankyung.com:not-a-port/article/1'>bad port</a>"
            b"</h2></li></ul>",
        ),
    ],
)
def test_newspaper_parsers_reject_unapproved_or_malformed_article_urls(
    parser: Callable[[bytes, str], tuple[tuple[object, ...], int]], html: bytes
) -> None:
    assert parser(html, "IT") == ((), 1)


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


@pytest.mark.parametrize(
    ("crawl", "source"),
    [
        (hankyung.crawl, "hankyung"),
        (mk.crawl, "mk"),
        (reb.crawl, "reb"),
        (kodit.crawl, "kodit"),
        (kogas.crawl, "kogas"),
    ],
)
def test_source_crawl_logs_traceback_and_returns_concise_error(
    crawl: Callable[..., object],
    source: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_fetch(_url: str) -> bytes:
        raise RuntimeError("list unavailable")

    with caplog.at_level(logging.ERROR, logger=crawl.__module__):
        result = crawl(fetcher=fail_fetch)

    assert result.source == source
    assert result.error == "RuntimeError: list unavailable"
    source_records = [
        record for record in caplog.records if record.name == crawl.__module__
    ]
    assert len(source_records) == 1
    assert source_records[0].exc_info is not None


def test_specific_categories_are_fetched_before_main(
    fixtures: Callable[[str], bytes],
) -> None:
    calls: list[str] = []
    result = hankyung.crawl(
        fetcher=lambda url: calls.append(url) or fixtures("hankyung.html")
    )

    assert result.error is None
    assert calls == [
        "https://www.hankyung.com/all-news-politics",
        "https://www.hankyung.com/all-news-economy",
        "https://www.hankyung.com/all-news-society",
        "https://www.hankyung.com/all-news-it",
        "https://www.hankyung.com/all-news-international",
        "https://www.hankyung.com/all-news",
    ]


def test_mk_specific_categories_are_fetched_before_main_and_missing_dates_are_enriched(
    fixtures: Callable[[str], bytes],
) -> None:
    calls: list[str] = []
    article_html = (
        b'<meta property="article:published_time" '
        b'content="2026-08-25T17:43:33+09:00">'
    )

    def fetch(url: str) -> bytes:
        calls.append(url)
        return fixtures("mk.html") if len(calls) <= 6 else article_html

    result = mk.crawl(fetcher=fetch)

    assert result.error is None
    assert calls[:6] == [
        "https://www.mk.co.kr/news/politics/",
        "https://www.mk.co.kr/news/economy/",
        "https://www.mk.co.kr/news/society/",
        "https://www.mk.co.kr/news/it/",
        "https://www.mk.co.kr/news/world/",
        "https://www.mk.co.kr/news/",
    ]
    assert calls[6:] == [
        "https://www.mk.co.kr/news/it/no-date",
        "https://www.mk.co.kr/news/headline/202608251",
        "https://www.mk.co.kr/news/main/202608252",
    ]
    assert all(item.published_at is not None for item in result.items)


def test_mk_crawl_enriches_every_missing_date_on_the_current_main_page() -> None:
    article_count = 25
    calls: list[str] = []
    empty_list = b"<ul id='list_area'></ul>"
    main_page = (
        "<section>"
        + "".join(
            f"<a class='news_item' data-section='main' href='/news/main/{index}'>"
            f"<div class='art_area'><h4>article {index}</h4></div></a>"
            for index in range(article_count)
        )
        + "</section>"
    ).encode()
    article_html = (
        b'<meta property="article:published_time" '
        b'content="2026-08-25T17:43:33+09:00">'
    )

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url == "https://www.mk.co.kr/news/":
            return main_page
        if url.endswith(("/politics/", "/economy/", "/society/", "/it/", "/world/")):
            return empty_list
        return article_html

    result = mk.crawl(fetcher=fetch)

    assert result.error is None
    assert len(result.items) == article_count
    assert all(item.published_at is not None for item in result.items)
    assert len(calls) == 6 + article_count


def test_mk_article_metadata_failure_keeps_list_results(
    fixtures: Callable[[str], bytes], caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def fetch(_url: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls <= 6:
            return fixtures("mk.html")
        raise RuntimeError("article unavailable")

    with caplog.at_level(logging.WARNING, logger=mk.__name__):
        result = mk.crawl(fetcher=fetch)

    assert result.error is None
    assert any(item.published_at is None for item in result.items)
    assert "article unavailable" in caplog.text
