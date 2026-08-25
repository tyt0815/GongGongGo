from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class NewsItemType(StrEnum):
    NEWSPAPER = "newspaper"
    INSTITUTION = "institution"


class NewsPeriod(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THREE_DAYS = "3d"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"


@dataclass(frozen=True)
class CrawledNewsItem:
    item_type: NewsItemType
    source: str
    source_name: str
    category: str
    source_category: str | None
    title: str
    url: str
    published_at: datetime | None


@dataclass(frozen=True)
class NewsRecord:
    id: int
    item_type: NewsItemType
    source: str
    source_name: str
    category: str
    source_category: str | None
    title: str
    url: str
    published_at: datetime | None
    discovered_at: datetime


@dataclass(frozen=True)
class SourceResult:
    source: str
    items: tuple[CrawledNewsItem, ...] = ()
    malformed_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class SaveStats:
    new_count: int = 0
    duplicate_count: int = 0
    suppressed_count: int = 0


@dataclass(frozen=True)
class CleanupStats:
    item_count: int = 0
    dismissal_count: int = 0


@dataclass(frozen=True)
class NewsCrawlSnapshot:
    running: bool = False
    completed_sources: int = 0
    total_sources: int = 0
    new_count: int = 0
    duplicate_count: int = 0
    expired_count: int = 0
    source_errors: dict[str, str] = field(default_factory=dict)
    run_error: str | None = None
