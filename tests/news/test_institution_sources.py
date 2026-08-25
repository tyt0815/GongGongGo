from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from src.news.sources import SOURCE_CRAWLERS, kodit, kogas, reb
from src.news.sources.kodit import parse_kodit_page
from src.news.sources.kogas import parse_kogas_page
from src.news.sources.reb import parse_reb_page
from src.news.registry import SOURCES


@pytest.fixture
def fixtures() -> Callable[[str], bytes]:
    directory = Path(__file__).parent / "fixtures"
    return lambda name: (directory / name).read_bytes()


def test_reb_direct_url_and_classification(fixtures: Callable[[str], bytes]) -> None:
    items, malformed = parse_reb_page(fixtures("reb.html"))

    assert items[0].category == "정기 통계"
    assert items[0].url.endswith("selectNttInfo.do?bbsId=1154&mi=9565&nttSn=116262")
    assert items[0].published_at is not None
    assert items[0].published_at.isoformat() == "2026-08-25T00:00:00+09:00"
    assert items[1].published_at is None
    assert malformed == 1


def test_kodit_and_kogas_direct_urls(fixtures: Callable[[str], bytes]) -> None:
    kodit_items, kodit_malformed = parse_kodit_page(fixtures("kodit.html"))
    kogas_items, kogas_malformed = parse_kogas_page(fixtures("kogas.html"))

    assert "bbsId=47&mi=2639&nttSn=5094548" in kodit_items[0].url
    assert kodit_items[1].published_at is None
    assert kodit_malformed == 1
    assert "Key=1010202000000&boardIdx=47656&cbIdx=41" in kogas_items[0].url
    assert kogas_items[1].published_at is None
    assert kogas_malformed == 1


@pytest.mark.parametrize(
    ("parser", "html"),
    [
        (parse_reb_page, b"<table></table>"),
        (parse_kodit_page, b"<table></table>"),
    ],
    ids=["reb", "kodit"],
)
def test_table_list_parsers_accept_empty_observed_containers(
    parser: Callable[[bytes], tuple[tuple[object, ...], int]], html: bytes
) -> None:
    assert parser(html) == ((), 0)


@pytest.mark.parametrize(
    ("parser", "html"),
    [
        (
            parse_reb_page,
            b"<table><tr><td class='al mBlock'><a class='nttInfoBtn' data-id='1'>"
            b"title 2026.08.25</a></td><td>date unavailable</td></tr></table>",
        ),
        (
            parse_kodit_page,
            b"<table><tr><td class='bbs_tit'><a class='nttInfoBtn' data-id='1'>"
            b"title 2026.08.25</a></td><td>date unavailable</td></tr></table>",
        ),
    ],
    ids=["reb", "kodit"],
)
def test_table_list_parsers_do_not_parse_dates_from_titles(
    parser: Callable[[bytes], tuple[tuple[object, ...], int]], html: bytes
) -> None:
    items, malformed = parser(html)

    assert items[0].published_at is None
    assert malformed == 0


@pytest.mark.parametrize(
    ("crawl", "page_marker"),
    [
        (reb.crawl, "currPage=2"),
        (kodit.crawl, "currPage=2"),
        (kogas.crawl, "pageIndex=2&pageOffset=10"),
    ],
    ids=["reb", "kodit", "kogas"],
)
def test_institution_crawlers_fetch_second_page(
    crawl: Callable[..., object],
    page_marker: str,
    fixtures: Callable[[str], bytes],
) -> None:
    calls: list[str] = []
    fixture_name = "kogas.html" if crawl is kogas.crawl else "reb.html"
    if crawl is kodit.crawl:
        fixture_name = "kodit.html"

    result = crawl(
        fetcher=lambda url: calls.append(url) or fixtures(fixture_name),
        today=date(2026, 8, 25),
    )

    assert result.error is None
    assert len(calls) == 10
    assert page_marker in calls[1]


@pytest.mark.parametrize(
    ("crawl", "fixture_name"),
    [(reb.crawl, "reb.html"), (kodit.crawl, "kodit.html"), (kogas.crawl, "kogas.html")],
    ids=["reb", "kodit", "kogas"],
)
def test_institution_crawlers_stop_after_page_with_old_valid_item(
    crawl: Callable[..., object], fixture_name: str, fixtures: Callable[[str], bytes]
) -> None:
    old_page = fixtures(fixture_name).replace(b"2026.08.25", b"2026.07.26")
    calls: list[str] = []

    result = crawl(
        fetcher=lambda url: calls.append(url) or old_page,
        today=date(2026, 8, 25),
    )

    assert result.error is None
    assert len(calls) == 1
    assert [item.published_at for item in result.items] == [None]


def test_reb_crawl_keeps_newer_and_malformed_dates_before_stopping_at_old_date() -> None:
    page = b"""
    <table>
      <tr><td class='al mBlock'><a class='nttInfoBtn' data-id='1'>new</a></td><td>2026.08.25.</td></tr>
      <tr><td class='al mBlock'><a class='nttInfoBtn' data-id='2'>old</a></td><td>2026.07.26.</td></tr>
      <tr><td class='al mBlock'><a class='nttInfoBtn' data-id='3'>undated 2026.08.25</a></td><td>date unavailable</td></tr>
    </table>
    """
    calls: list[str] = []

    result = reb.crawl(
        fetcher=lambda url: calls.append(url) or page,
        today=date(2026, 8, 25),
    )

    assert len(calls) == 1
    assert [item.title for item in result.items] == ["new", "undated 2026.08.25"]
    assert [item.published_at.date() if item.published_at is not None else None for item in result.items] == [
        date(2026, 8, 25),
        None,
    ]


@pytest.mark.parametrize(
    ("crawl", "fixture_name"),
    [(reb.crawl, "reb.html"), (kodit.crawl, "kodit.html"), (kogas.crawl, "kogas.html")],
    ids=["reb", "kodit", "kogas"],
)
def test_institution_crawlers_retain_the_inclusive_thirty_day_boundary(
    crawl: Callable[..., object], fixture_name: str, fixtures: Callable[[str], bytes]
) -> None:
    boundary_page = fixtures(fixture_name).replace(b"2026.08.25", b"2026.07.27")
    old_page = fixtures(fixture_name).replace(b"2026.08.25", b"2026.07.26")
    calls = 0

    def fetcher(_url: str) -> bytes:
        nonlocal calls
        calls += 1
        return boundary_page if calls == 1 else old_page

    result = crawl(fetcher=fetcher, today=date(2026, 8, 25))

    assert [item.published_at.date() if item.published_at is not None else None for item in result.items] == [
        date(2026, 7, 27),
        None,
        None,
    ]


@pytest.mark.parametrize(
    ("crawl", "source"),
    [(reb.crawl, "reb"), (kodit.crawl, "kodit"), (kogas.crawl, "kogas")],
    ids=["reb", "kodit", "kogas"],
)
def test_institution_crawlers_cap_pagination_at_ten_pages(
    crawl: Callable[..., object], source: str
) -> None:
    calls: list[str] = []
    html = _single_item_html(source, "2026.08.25")

    result = crawl(
        fetcher=lambda url: calls.append(url) or html,
        today=date(2026, 8, 25),
    )

    assert result.error is None
    assert len(calls) == 10


@pytest.mark.parametrize(
    ("crawl", "source"),
    [(reb.crawl, "reb"), (kodit.crawl, "kodit"), (kogas.crawl, "kogas")],
    ids=["reb", "kodit", "kogas"],
)
def test_institution_crawlers_report_missing_list_container(
    crawl: Callable[..., object], source: str
) -> None:
    result = crawl(fetcher=lambda _url: b"<html></html>")

    assert result.source == source
    assert result.items == ()
    assert result.error is not None
    assert "container" in result.error


def test_source_crawlers_follow_the_source_registry_order() -> None:
    assert tuple(SOURCE_CRAWLERS) == tuple(SOURCES)


def _single_item_html(source: str, published_on: str) -> bytes:
    if source == "reb":
        return (
            "<table><tr><td class='al mBlock'><a class='nttInfoBtn' data-id='1'>"
            "제목</a></td><td>"
            f"{published_on}.</td></tr></table>"
        ).encode()
    if source == "kodit":
        return (
            "<table><tr><td class='bbs_tit'><a class='nttInfoBtn' data-id='1'>"
            "제목</a></td><td>"
            f"{published_on}</td></tr></table>"
        ).encode()
    return (
        "<div class='list_body webzine'><div class='list_item' "
        "onclick='readPermissionChk(1)'><strong class='title'>제목</strong>"
        f"<span class='wdate'><span class='value'>{published_on}</span></span>"
        "</div></div>"
    ).encode()
