from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class PostStatus(StrEnum):
    REVIEW_PENDING = "review_pending"
    PLANNED = "planned"
    APPLIED = "applied"
    EXCLUDED = "excluded"


class DeadlineKind(StrEnum):
    DATED = "dated"
    OPEN = "open"
    UNKNOWN = "unknown"


class CrawlRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class Settings(BaseModel):
    concurrency: Literal[1, 2, 4] = 2
    open_browser: bool = False


@dataclass(frozen=True)
class ParsedTitle:
    institution: str
    employment: str
    career: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class ParsedDeadline:
    raw: str
    kind: DeadlineKind
    value: date | None


@dataclass(frozen=True)
class CrawledPost:
    category: str
    title: str
    deadline_raw: str
    link: str


@dataclass(frozen=True)
class PostRecord:
    link: str
    original_title: str
    status: PostStatus
    deadline_raw: str


@dataclass(frozen=True)
class DeletedLinkRecord:
    id: int
    link: str


@dataclass(frozen=True)
class CrawlRunRecord:
    id: int
    status: CrawlRunStatus
    new_count: int


@dataclass(frozen=True)
class CategoryResult:
    category: str
    posts: tuple[CrawledPost, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class CrawlSnapshot:
    running: bool = False
    completed_categories: int = 0
    total_categories: int = 0
    new_count: int = 0
    category_errors: dict[str, str] = field(default_factory=dict)
    run_error: str | None = None
