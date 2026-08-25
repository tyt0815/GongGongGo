"""Domain contracts and pure helpers for the news inbox."""

from .classification import classify_institution_title
from .domain import (
    CleanupStats,
    CrawledNewsItem,
    NewsCrawlSnapshot,
    NewsItemType,
    NewsPeriod,
    NewsRecord,
    SaveStats,
    SourceResult,
)
from .registry import NEWS_CATEGORIES, SOURCES, SourceDefinition
from .url_normalization import normalize_url

__all__ = [
    "CleanupStats",
    "CrawledNewsItem",
    "NEWS_CATEGORIES",
    "NewsCrawlSnapshot",
    "NewsItemType",
    "NewsPeriod",
    "NewsRecord",
    "SOURCES",
    "SaveStats",
    "SourceDefinition",
    "SourceResult",
    "classify_institution_title",
    "normalize_url",
]
