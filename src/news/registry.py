from dataclasses import dataclass

from .domain import NewsItemType

NEWS_CATEGORIES = ("주요뉴스", "정치", "경제", "사회", "IT", "세계")
NEWS_FILTER_CATEGORIES = NEWS_CATEGORIES + ("보도자료", "정기 통계")


@dataclass(frozen=True)
class SourceDefinition:
    source: str
    source_name: str
    item_type: NewsItemType
    ttl_days: int


SOURCES = {
    "hankyung": SourceDefinition("hankyung", "한국경제", NewsItemType.NEWSPAPER, 7),
    "mk": SourceDefinition("mk", "매일경제", NewsItemType.NEWSPAPER, 7),
    "reb": SourceDefinition("reb", "한국부동산원", NewsItemType.INSTITUTION, 30),
    "kodit": SourceDefinition("kodit", "신용보증기금", NewsItemType.INSTITUTION, 30),
    "kogas": SourceDefinition("kogas", "한국가스공사", NewsItemType.INSTITUTION, 30),
}
