from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from src.news.classification import classify_institution_title
from src.news.domain import (
    CleanupStats,
    CrawledNewsItem,
    NewsCrawlSnapshot,
    NewsItemType,
    NewsPeriod,
    NewsRecord,
    SaveStats,
    SourceResult,
)
from src.news.registry import NEWS_CATEGORIES, SOURCES, SourceDefinition
from src.news.url_normalization import normalize_url


def test_registry_and_period_contract() -> None:
    assert tuple(SOURCES) == ("hankyung", "mk", "reb", "kodit", "kogas")
    assert SOURCES["hankyung"].ttl_days == 7
    assert SOURCES["reb"].ttl_days == 30
    assert SOURCES["hankyung"].item_type is NewsItemType.NEWSPAPER
    assert SOURCES["reb"].item_type is NewsItemType.INSTITUTION
    assert NEWS_CATEGORIES == ("주요뉴스", "정치", "경제", "사회", "IT", "세계")
    assert [value.value for value in NewsPeriod] == ["today", "yesterday", "3d", "7d", "30d"]


def test_url_normalization_is_conservative() -> None:
    value = "HTTP://Example.COM:80/a/?utm_source=x&boardIdx=41#fragment"
    assert normalize_url(value) == "http://example.com/a?boardIdx=41"
    assert normalize_url("https://x.test/v?bbsId=47&nttSn=9") == "https://x.test/v?bbsId=47&nttSn=9"
    assert normalize_url("https://EXAMPLE.com:443/?fbclid=x&gclid=y&n_cid=z") == "https://example.com/"
    assert normalize_url("https://example.test:0/a") == "https://example.test:0/a"
    assert normalize_url("https://example.com/a/") == "https://example.com/a"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:not-a-port/a",
        "https://example.com:65536/a",
    ],
)
def test_url_normalization_rejects_invalid_ports(url: str) -> None:
    with pytest.raises(ValueError, match="Port"):
        normalize_url(url)


def test_reb_classifier_is_narrow() -> None:
    assert classify_institution_title("주간아파트가격동향(20260817기준)") == "정기 통계"
    assert classify_institution_title("26.7월 전국주택가격동향") == "정기 통계"
    assert classify_institution_title("상업용부동산 임대동향조사 결과") == "정기 통계"
    assert classify_institution_title("부동산 거래가격 거짓신고 집중 운영") == "보도자료"
    assert classify_institution_title("상업용부동산 임대동향") == "정기 통계"
    assert classify_institution_title("  주간아파트가격동향\n(20260817기준)  ") == "정기 통계"


def test_domain_records_are_frozen_and_have_safe_defaults() -> None:
    published_at = datetime(2026, 8, 25, 12, tzinfo=UTC)
    item = CrawledNewsItem(
        NewsItemType.NEWSPAPER,
        "hankyung",
        "한국경제",
        "IT",
        "IT/과학",
        "제목",
        "https://example.test/a",
        published_at,
    )
    record = NewsRecord(1, *item.__dict__.values(), datetime(2026, 8, 25, tzinfo=UTC))
    assert record.item_type is NewsItemType.NEWSPAPER
    assert SourceResult("hankyung").items == ()
    assert SaveStats() == SaveStats(0, 0, 0)
    assert CleanupStats() == CleanupStats(0, 0)
    assert NewsCrawlSnapshot().source_errors == {}
    with pytest.raises(FrozenInstanceError):
        item.title = "변경"  # type: ignore[misc]


def test_source_definition_contains_registry_metadata() -> None:
    source = SourceDefinition("test", "테스트", NewsItemType.INSTITUTION, 30)
    assert source.source == "test"
    assert source.source_name == "테스트"
    assert source.item_type is NewsItemType.INSTITUTION
    assert source.ttl_days == 30
