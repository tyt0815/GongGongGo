# GongGongGo Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert GongGongGo into a responsive local FastAPI dashboard that serves immediately, crawls concurrently in the background, and safely persists posts, settings, and user state in SQLite.

**Architecture:** A single FastAPI process owns a SQLite repository and an in-process crawl manager. Async Playwright uses one Chromium browser and one page per category, limited by a persisted `1/2/4` semaphore; crawler writes update only crawler-owned columns, while API writes update only user-owned status. The browser polls small JSON APIs and renders a two-lane QHD layout or a single status tab at narrow widths.

**Tech Stack:** Python 3.12, FastAPI 0.138, Uvicorn 0.49, Pydantic 2.13, Jinja2 3.1, Playwright 1.61, SQLite 3, vanilla HTML/CSS/JavaScript, pytest, pytest-asyncio, HTTPX.

## Global Constraints

- Bind only to `127.0.0.1`; do not add authentication or remote deployment.
- Run one crawl at startup and on explicit user request; do not add periodic scheduling or notifications.
- Persist crawl concurrency as exactly one of `1`, `2`, or `4`; default to `2`.
- Persist browser auto-open as a boolean; default to `false`.
- Preserve `data/job_posts.json` unchanged and migrate only strict `YYYY.MM.DD` rows.
- Map `대기 → 검토 대기`, `지원 예정 → 지원 예정`, and `완료 → 지원 완료`.
- Never let a crawler update overwrite a user's status.
- Preserve original title and deadline strings even when parsing succeeds.
- Match institution keywords only against parsed institutions and role keywords only against parsed roles.
- Always show matched institutions; show other institutions only when a parsed role keyword matches.
- Use case-insensitive substring matching inside the relevant parsed field.
- Store runtime SQLite files under `data/` and logs under `logs/`; neither is committed.
- Keep dated logs for 14 days.
- Do not modify or delete the user's existing JSON during tests.

---

## Planned File Structure

- `main.py`: production entry point, logging setup, optional browser opener, Uvicorn launch.
- `src/config.py`: project paths, category URLs, defaults, and allowed setting values.
- `src/domain.py`: enums and dataclasses shared across parser, repository, crawler, and API.
- `src/parsing.py`: pure title, deadline, and keyword filtering functions.
- `src/database.py`: SQLite connections, schema creation, default settings, one-time JSON migration.
- `src/repository.py`: field-safe post writes, status actions, settings, tombstones, crawl history.
- `src/crawler.py`: one-category Async Playwright crawler and browser-level orchestration.
- `src/crawl_manager.py`: single-run guard, progress snapshot, startup/manual/retry coordination.
- `src/web.py`: FastAPI application factory, lifespan, page route, and JSON APIs.
- `src/logging_config.py`: dated file/console logging and 14-day startup cleanup.
- `templates/index.html`: semantic dashboard shell and settings drawer.
- `static/app.css`: QHD two-lane and narrow single-tab layout.
- `static/app.js`: API client, rendering, polling, status actions, undo, and settings editor.
- `tests/`: pure unit, repository, crawler, manager, API, and browser-layout tests.
- `requirements.txt`, `requirements-dev.txt`: reproducible runtime and test dependencies.
- `ggg_startup.bat`, `ggg_startup.vbs`, `ggg_debug.bat`: hidden and visible Windows launch paths.

### Task 1: Test Foundation, Configuration, and Domain Types

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `src/config.py`
- Create: `src/domain.py`
- Create: `tests/test_domain.py`

**Interfaces:**
- Consumes: Python 3.12 and the existing four Naver Cafe category URLs.
- Produces: `PostStatus`, `DeadlineKind`, `CrawlRunStatus`, `ParsedTitle`, `ParsedDeadline`, `CrawledPost`, `PostRecord`, `DeletedLinkRecord`, `CrawlRunRecord`, `CategoryResult`, `CrawlSnapshot`, `Settings`, `PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `JSON_PATH`, `LOG_DIR`, `TARGET_URLS`, `DEFAULT_INSTITUTION_KEYWORDS`, and `DEFAULT_ROLE_KEYWORDS`.

- [ ] **Step 1: Add dependency manifests and pytest configuration**

```text
# requirements.txt
fastapi==0.138.1
uvicorn==0.49.0
Jinja2==3.1.6
playwright==1.61.0
pydantic==2.13.4

# requirements-dev.txt
-r requirements.txt
pytest>=8,<10
pytest-asyncio>=0.24,<2
httpx>=0.27,<1
```

```ini
# pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 2: Write failing enum and settings tests**

```python
# tests/test_domain.py
import pytest
from pydantic import ValidationError

from src.domain import PostStatus, Settings


def test_status_values_are_stable():
    assert [status.value for status in PostStatus] == [
        "review_pending", "planned", "applied", "excluded"
    ]


@pytest.mark.parametrize("value", [1, 2, 4])
def test_settings_accept_supported_concurrency(value):
    assert Settings(concurrency=value, open_browser=False).concurrency == value


def test_settings_reject_unsupported_concurrency():
    with pytest.raises(ValidationError):
        Settings(concurrency=3, open_browser=False)
```

- [ ] **Step 3: Install the declared development dependencies**

Run: `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`
Expected: successful installation of pytest, pytest-asyncio, and HTTPX without replacing the pinned runtime versions.

Run: `.\.venv\Scripts\python.exe -m playwright install chromium`
Expected: Chromium is installed or reported as already installed.

- [ ] **Step 4: Run the tests and verify the missing module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_domain.py -v`
Expected: FAIL because `src.domain` does not exist.

- [ ] **Step 5: Implement focused configuration and domain types**

```python
# src/domain.py
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
```

Create `src/config.py` with `Path(__file__).resolve().parents[1]`-based paths, the four current `TARGET_URL` entries, current institution and role keyword tuples, and no user-specific absolute paths.

- [ ] **Step 6: Run the focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_domain.py -v`
Expected: PASS.

- [ ] **Step 7: Commit the foundation**

```bash
git add requirements.txt requirements-dev.txt pytest.ini src/config.py src/domain.py tests/test_domain.py
git commit -m "chore: add typed application foundation"
```

### Task 2: Title, Deadline, and Keyword Parsing

**Files:**
- Create: `src/parsing.py`
- Create: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `ParsedTitle`, `ParsedDeadline`, and `DeadlineKind` from `src.domain`.
- Produces: `parse_title(title: str) -> ParsedTitle | None`, `parse_deadline(raw: str, today: date) -> ParsedDeadline`, and `filter_roles(parsed: ParsedTitle | None, institution_keywords: tuple[str, ...], role_keywords: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]`.

- [ ] **Step 1: Write failing parser tests using real title shapes**

```python
# tests/test_parsing.py
from datetime import date

from src.domain import DeadlineKind
from src.parsing import filter_roles, parse_deadline, parse_title


def test_parse_structured_title_hides_recruitment_prefix():
    parsed = parse_title("★총20명 [한국가스공사 채용] 정규직 신입 (기계/전산/데이터)")
    assert parsed is not None
    assert parsed.institution == "한국가스공사"
    assert parsed.employment == "정규직"
    assert parsed.career == "신입"
    assert parsed.roles == ("기계", "전산", "데이터")


def test_parse_title_returns_none_for_unstructured_text():
    assert parse_title("경기도 공공기관 통합채용 사전공고") is None


def test_parse_deadline_classifies_date_open_and_unknown():
    assert parse_deadline("8/5", date(2026, 8, 3)).value == date(2026, 8, 5)
    assert parse_deadline("채용시마감", date(2026, 8, 3)).kind is DeadlineKind.OPEN
    assert parse_deadline("7.24/7.31", date(2026, 8, 3)).kind is DeadlineKind.UNKNOWN


def test_institution_match_keeps_post_but_general_post_shows_only_matching_roles():
    target = parse_title("[한국가스공사 채용] 정규직 신입 (기계/화공)")
    assert filter_roles(target, ("한국가스공사",), ("전산",)) == (True, ("기계", "화공"))

    general = parse_title("[일반기관 채용] 정규직 신입 (행정/전산/회계)")
    assert filter_roles(general, ("한국가스공사",), ("전산",)) == (True, ("전산",))
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parsing.py -v`
Expected: FAIL because `src.parsing` does not exist.

- [ ] **Step 3: Implement conservative pure parsing functions**

Use one flexible outer regex for an optional prefix, bracketed institution with optional `채용`, remaining employment/career text, and a final role parenthesis. Split roles only on `/` and commas. Recognize explicit employment tokens and `신입`, `경력`, `신입/경력`; keep ambiguous leftover text in `employment` rather than discarding it. Treat `채용시마감`, `채용시 마감`, `채용시까지`, `상시채용`, `상시모집`, and `수시채용` as `open`. Accept strict `YYYY.MM.DD`, `M.D`, and `M/D`; do not guess multi-date strings.

```python
def filter_roles(parsed, institution_keywords, role_keywords):
    if parsed is None:
        return False, ()
    institution_match = _contains_any(parsed.institution, institution_keywords)
    matched = tuple(
        role for role in parsed.roles if _contains_any(role, role_keywords)
    )
    if institution_match:
        return True, matched or parsed.roles
    return bool(matched), matched
```

- [ ] **Step 4: Add regression cases from the 10 unstructured and multi-role examples**

Add parametrized tests for `[창원경상국립대학교병원]`, `[계명대학교채용]`, `★총133명! 경기도 공공기관 통합채용 (~8.14)`, an empty deadline, `채용시까지`, and a role containing uppercase `ICT`. Assert original strings are never mutated by the parser.

- [ ] **Step 5: Run the parser suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parsing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit the parser**

```bash
git add src/parsing.py tests/test_parsing.py
git commit -m "feat: parse and filter structured job fields"
```

### Task 3: SQLite Schema, Defaults, and Selective JSON Migration

**Files:**
- Create: `src/database.py`
- Create: `tests/test_database.py`

**Interfaces:**
- Consumes: paths/default keywords from `src.config`, enums from `src.domain`, and `parse_title`/`parse_deadline` from `src.parsing`.
- Produces: `connect(db_path: Path) -> sqlite3.Connection` and `initialize_database(db_path: Path, json_path: Path) -> None`.

- [ ] **Step 1: Write failing schema and migration tests with a temporary JSON file**

```python
# tests/test_database.py
import json
from pathlib import Path

from src.database import connect, initialize_database


def test_migration_imports_only_strict_dates_and_maps_status(tmp_path: Path):
    source = tmp_path / "job_posts.json"
    source.write_text(json.dumps([
        {"category": "중앙공기업", "title": "[A 채용] 정규직 신입 (전산)",
         "deadline": "2026.08.10", "link": "https://example/a", "state": "대기"},
        {"category": "중앙공기업", "title": "[B 채용] 정규직 신입 (전산)",
         "deadline": "채용시마감", "link": "https://example/b", "state": "완료"},
    ], ensure_ascii=False), encoding="utf-8")
    db_path = tmp_path / "gonggonggo.db"

    initialize_database(db_path, source)

    with connect(db_path) as connection:
        rows = connection.execute("SELECT link, status FROM job_posts").fetchall()
    assert [(row["link"], row["status"]) for row in rows] == [
        ("https://example/a", "review_pending")
    ]
    assert "채용시마감" in source.read_text(encoding="utf-8")
```

Also assert WAL mode, `busy_timeout >= 5000`, default settings `(2, false)`, default institution/role keywords, required CHECK constraints, and that calling `initialize_database` twice does not duplicate rows.

- [ ] **Step 2: Run the database tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database.py -v`
Expected: FAIL because `src.database` does not exist.

- [ ] **Step 3: Implement connections and the complete schema**

Create tables `app_meta`, `app_settings`, `filter_keywords`, `job_posts`, `crawl_runs`, `crawl_category_results`, and `deleted_links`. Use ISO date/time text, JSON text for role tuples, UNIQUE link constraints, and CHECK constraints for enums and concurrency. `connect` sets `row_factory`, `foreign_keys=ON`, `journal_mode=WAL`, and `busy_timeout=5000`.

```sql
INSERT INTO app_settings(id, concurrency, open_browser)
VALUES (1, 2, 0) ON CONFLICT(id) DO NOTHING;
```

- [ ] **Step 4: Implement the one-time migration without touching the source file**

Read the JSON, accept only `datetime.strptime(deadline, "%Y.%m.%d")`, map known states, parse titles for display fields, and insert in one transaction. Record `json_migration_v1=complete` in `app_meta` only after the transaction commits. Never open the JSON in write mode.

- [ ] **Step 5: Run the database suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database.py -v`
Expected: PASS.

- [ ] **Step 6: Commit database initialization**

```bash
git add src/database.py tests/test_database.py
git commit -m "feat: add SQLite schema and safe migration"
```

### Task 4: Repository with Field-Safe Writes, Settings, and Tombstones

**Files:**
- Create: `src/repository.py`
- Create: `tests/test_repository.py`

**Interfaces:**
- Consumes: `connect`, parser/filter functions, domain enums and dataclasses.
- Produces: `Repository.get_post(link: str) -> PostRecord | None`, `list_visible_posts(statuses: tuple[PostStatus, ...] | None = None) -> list[dict[str, object]]`, `update_status(link: str, status: PostStatus) -> bool`, `upsert_crawled_posts(posts: list[CrawledPost]) -> int`, `delete_permanently(link: str) -> bool`, `list_deleted_links() -> tuple[DeletedLinkRecord, ...]`, `unblock_link(id: int) -> bool`, `get_settings() -> Settings`, `update_settings(settings: Settings) -> None`, `get_keywords(kind: Literal["institution", "role"]) -> tuple[str, ...]`, `replace_keywords(kind: Literal["institution", "role"], values: list[str]) -> None`, `create_crawl_run(trigger: str) -> int`, `finish_crawl_run(run_id: int, status: CrawlRunStatus, new_count: int, results: tuple[CategoryResult, ...]) -> None`, and `latest_crawl_run() -> CrawlRunRecord | None`.

- [ ] **Step 1: Write failing tests for the no-lost-update invariant**

```python
# tests/test_repository.py
def test_crawler_upsert_preserves_user_status(repository, seeded_post):
    repository.update_status(seeded_post.link, PostStatus.PLANNED)
    changed = replace(seeded_post, title="Updated title", deadline_raw="2026.08.20")

    repository.upsert_crawled_posts([changed])

    row = repository.get_post(seeded_post.link)
    assert row.status is PostStatus.PLANNED
    assert row.original_title == "Updated title"


def test_permanent_delete_blocks_recollection(repository, seeded_post):
    repository.delete_permanently(seeded_post.link)
    repository.upsert_crawled_posts([seeded_post])
    assert repository.get_post(seeded_post.link) is None
    assert repository.list_deleted_links()[0].link == seeded_post.link
```

Add tests for state-only updates, keyword replacement normalization, invalid empty keywords, settings persistence, unblocking, visible role selection, and crawl history partial-failure records.

- [ ] **Step 2: Run the repository tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repository.py -v`
Expected: FAIL because `src.repository` does not exist.

- [ ] **Step 3: Implement explicit-column SQL**

Use `INSERT ... ON CONFLICT(link) DO UPDATE SET` only for crawler-owned columns: category, original title, parsed institution/employment/career/roles, raw/parsed deadline, deadline kind, and last-seen timestamp. Do not mention `status` or `status_updated_at` in the UPDATE clause. Insert new rows with `review_pending`.

- [ ] **Step 4: Implement user actions and settings in separate short transactions**

`update_status` updates only `status` and `status_updated_at`. Permanent deletion inserts the link into `deleted_links` and deletes the post in the same transaction. `replace_keywords` normalizes with `casefold()`, removes blanks and duplicates while preserving first display spelling, then replaces one keyword kind atomically.

- [ ] **Step 5: Run repository and database tests together**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_database.py tests/test_repository.py -v`
Expected: PASS.

- [ ] **Step 6: Commit the repository**

```bash
git add src/repository.py tests/test_repository.py
git commit -m "feat: add state-safe job repository"
```

### Task 5: Async Category Crawler and Browser Orchestration

**Files:**
- Create: `src/crawler.py`
- Create: `tests/test_crawler.py`
- Delete after integration: `src/gongjoonmo_crawler.py`

**Interfaces:**
- Consumes: `TARGET_URLS`, `CrawledPost`, `CategoryResult`, and repository-provided known/deleted links.
- Produces: `crawl_category(page, category, base_url, known_links, blocked_links, early_stop=True) -> CategoryResult` and `crawl_categories(categories, concurrency, known_links, blocked_links, on_result=None) -> tuple[CategoryResult, ...]`. `on_result`, when provided, is awaited once as each category finishes so the manager can publish progress.

- [ ] **Step 1: Write failing async crawler tests with a fake page**

```python
# tests/test_crawler.py
@pytest.mark.asyncio
async def test_category_collects_rows_and_stops_at_known_link(fake_page):
    fake_page.add_page(1, [
        ("[A 채용] 정규직 신입 (전산) (~8.10)", "https://example/new"),
        ("[Old 채용] 정규직 신입 (전산) (~8.11)", "https://example/known"),
    ])
    result = await crawl_category(
        fake_page, "중앙공기업", "https://example/menu",
        {"https://example/known"}, set(), early_stop=True,
    )
    assert [post.link for post in result.posts] == ["https://example/new"]


@pytest.mark.asyncio
async def test_bad_row_does_not_abort_remaining_rows(fake_page):
    fake_page.add_page(1, [RuntimeError("bad row"),
                           ("[B 채용] 정규직 신입 (전산) (~8.12)", "https://example/b")])
    result = await crawl_category(fake_page, "중앙공기업", "https://example/menu", set(), set())
    assert [post.link for post in result.posts] == ["https://example/b"]
```

Add a concurrency test whose fake worker records maximum simultaneous entries for settings `1`, `2`, and `4`, plus a test that one category error produces one errored `CategoryResult` without cancelling peers.

- [ ] **Step 2: Run crawler tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crawler.py -v`
Expected: FAIL because `src.crawler` does not exist.

- [ ] **Step 3: Implement page-level parsing with stable selectors**

Port the current selectors and early-stop behavior to the async API. Separate extracting one row from page iteration so a row exception can be logged and skipped. Treat consecutive missing rows as the end of a page rather than returning from the entire crawl. Keep source title/deadline extraction separate from display parsing.

- [ ] **Step 4: Implement one-browser orchestration with a semaphore**

```python
semaphore = asyncio.Semaphore(concurrency)

async def run_one(category, url):
    async with semaphore:
        page = await context.new_page()
        try:
            return await crawl_category(page, category, url, known_links, blocked_links)
        except Exception as exc:
            return CategoryResult(category=category, error=str(exc))
        finally:
            await page.close()
```

Use `async_playwright`, one headless Chromium, one context, `asyncio.as_completed` around the category tasks, and always close the context/browser in `finally`. Append each completed result, await `on_result(result)` immediately when configured, then return results in the original category order for deterministic tests and logs.

- [ ] **Step 5: Run crawler tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crawler.py -v`
Expected: PASS without network access.

- [ ] **Step 6: Remove the old synchronous crawler and commit**

Run: `git rm src/gongjoonmo_crawler.py`
Then:

```bash
git add src/crawler.py tests/test_crawler.py
git commit -m "feat: crawl categories concurrently with Playwright"
```

### Task 6: Crawl Manager, Progress, Partial Failure, and Retry

**Files:**
- Create: `src/crawl_manager.py`
- Create: `tests/test_crawl_manager.py`

**Interfaces:**
- Consumes: `Repository`, `crawl_categories`, `TARGET_URLS`, `CategoryResult`, and `CrawlSnapshot`.
- Produces: `CrawlManager.start(trigger: str, categories: tuple[str, ...] | None = None) -> bool`, `CrawlManager.snapshot() -> CrawlSnapshot`, and `CrawlManager.wait() -> None`.

- [ ] **Step 1: Write failing manager behavior tests**

```python
# tests/test_crawl_manager.py
@pytest.mark.asyncio
async def test_start_rejects_duplicate_run(manager, blocking_crawler):
    assert manager.start("manual") is True
    await blocking_crawler.started.wait()
    assert manager.start("manual") is False
    blocking_crawler.release.set()
    await manager.wait()


@pytest.mark.asyncio
async def test_partial_failure_saves_successes(manager, repository, fake_crawler):
    fake_crawler.results = (
        CategoryResult("중앙공기업", posts=(make_post("a"),)),
        CategoryResult("지방공기업", error="timeout"),
    )
    manager.start("manual")
    await manager.wait()
    assert repository.get_post("a") is not None
    assert manager.snapshot().category_errors == {"지방공기업": "timeout"}
```

Also test persisted concurrency use, startup trigger history, retry with exactly one requested category, and a final atomic repository call after all category tasks return.

- [ ] **Step 2: Run manager tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crawl_manager.py -v`
Expected: FAIL because `src.crawl_manager` does not exist.

- [ ] **Step 3: Implement the single-task guard and immutable snapshots**

Keep one private `asyncio.Task | None`. `start` returns false when the task is active; otherwise it creates a task and returns immediately. Pass an async result callback into `crawl_categories`; the callback increments completed categories, accumulates errors, and replaces the immutable snapshot under an `asyncio.Lock` so API readers never observe partially mutated dictionaries.

- [ ] **Step 4: Implement run history and partial completion**

Create the run record before invoking the crawler. After all category results return, pass only successful posts to one `upsert_crawled_posts` call, finish the history as succeeded/partial/failed, and retain per-category errors for retry. Always clear the active task in `finally`.

- [ ] **Step 5: Run manager and repository tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crawl_manager.py tests/test_repository.py -v`
Expected: PASS.

- [ ] **Step 6: Commit the manager**

```bash
git add src/crawl_manager.py tests/test_crawl_manager.py
git commit -m "feat: coordinate background crawl runs"
```

### Task 7: FastAPI Application, Lifespan, and JSON APIs

**Files:**
- Create: `src/web.py`
- Create: `tests/test_web.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: database initializer, `Repository`, `CrawlManager`, Pydantic `Settings`, and status enums.
- Produces: `create_app(db_path=DB_PATH, json_path=JSON_PATH, manager_factory=None) -> FastAPI`, `GET /health`, `GET /`, `GET /api/posts`, `POST /api/posts/status`, `POST /api/posts/delete`, `POST /api/crawl/start`, `POST /api/crawl/retry/{category}`, `GET /api/crawl/status`, `GET /api/settings`, `PUT /api/settings`, `GET /api/deleted-links`, and `DELETE /api/deleted-links/{id}`.

- [ ] **Step 1: Write failing API and startup tests**

```python
# tests/test_web.py
def test_health_and_home_are_available_before_crawl_finishes(client, blocking_manager):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert blocking_manager.startup_started is True
    assert blocking_manager.finished is False


def test_status_update_uses_validated_enum(client, seeded_post):
    response = client.post("/api/posts/status", json={
        "link": seeded_post.link, "status": "planned"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "planned"


def test_duplicate_manual_crawl_returns_conflict(client, running_manager):
    assert client.post("/api/crawl/start").status_code == 409
```

Add tests for settings validation, keyword replacement, delete/unblock, failed-category retry validation, and HTML escaping of Korean titles containing quotes.

- [ ] **Step 2: Run API tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: FAIL because `src.web` does not exist.

- [ ] **Step 3: Implement an application factory and lifespan**

Initialize the database and repository before yielding. Schedule `manager.start("startup")` without awaiting crawl completion. On shutdown, await an active manager task and close Playwright resources through the crawler's `finally` blocks. Store repository and manager on `app.state`; tests inject a fake manager factory.

- [ ] **Step 4: Implement thin validated routes**

Use Pydantic request models for links, statuses, settings, and keyword arrays. Routes call repository/manager methods and translate not-found, invalid category, duplicate crawl, and database errors into explicit 404/422/409/503 responses. Do not place SQL or crawling logic in route functions.

- [ ] **Step 5: Replace `main.py` with the minimal app export and launch guard**

```python
import uvicorn

from src.web import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Task 9 replaces only the launch guard with `src.runtime.run(app)` after that module exists, so every intermediate commit remains directly runnable.

- [ ] **Step 6: Run all non-browser tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_domain.py tests/test_parsing.py tests/test_database.py tests/test_repository.py tests/test_crawler.py tests/test_crawl_manager.py tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 7: Commit the web application**

```bash
git add main.py src/web.py tests/test_web.py
git commit -m "feat: expose background crawl APIs"
```

### Task 8: Responsive Dashboard and Editable Settings

**Files:**
- Rewrite: `templates/index.html`
- Create: `static/app.css`
- Create: `static/app.js`
- Create: `tests/test_dashboard.py`
- Modify: `src/web.py`

**Interfaces:**
- Consumes: JSON APIs from Task 7.
- Produces: wide `active/archive` group view, narrow one-status tab view, structured cards, search/filter/sort, crawl status polling, settings editor, exclusion undo, permanent deletion, and tombstone unblock UI.

- [ ] **Step 1: Write failing dashboard contract tests**

```python
# tests/test_dashboard.py
def test_dashboard_loads_external_static_assets(client):
    html = client.get("/").text
    assert '/static/app.css' in html
    assert '/static/app.js' in html
    assert 'data-view-group="active"' in html
    assert 'id="settings-drawer"' in html


def test_post_api_returns_structured_and_fallback_fields(client, seeded_posts):
    posts = client.get("/api/posts").json()["posts"]
    structured = next(post for post in posts if post["link"].endswith("structured"))
    assert structured["institution"] == "한국교육학술정보원"
    assert structured["display_roles"] == ["전산"]
```

Add contract assertions for four Korean status labels, crawl button/status region, keyword editors, deleted-link management, and original-title fallback.

- [ ] **Step 2: Run dashboard tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v`
Expected: FAIL against the existing inline template.

- [ ] **Step 3: Build the semantic HTML shell and responsive CSS**

Mount `static/` in `src.web.create_app` only after the directory exists. Use a centered container with `max-width: 1600px`. At `min-width: 1400px`, show two lanes for the selected `진행 중` or `보관함` group. Below 1400px, hide group lanes and show one of four status panels selected by tabs. Keep action buttons inside each card, directly below structured title fields; do not push them to the viewport edge.

- [ ] **Step 4: Implement safe client rendering and interactions**

Build DOM nodes with `textContent`, never interpolate titles or links into `innerHTML`. Poll `/api/crawl/status` once per second only while running, disable the run button, then refresh posts once on completion. Implement status changes without full-page reload, a timed undo toast for exclusion, confirmation before permanent deletion, and clear API error messages.

- [ ] **Step 5: Implement settings and keyword editing**

Render concurrency as `1/2/4`, browser auto-open as a toggle, and institution/role keywords as removable chips with an add field. Save one validated settings payload; on success re-fetch posts so changed filters apply immediately. Include a deleted-links list whose unblock action explains that the post may return on the next crawl.

- [ ] **Step 6: Run dashboard and API tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 7: Commit the dashboard**

```bash
git add src/web.py templates/index.html static/app.css static/app.js tests/test_dashboard.py
git commit -m "feat: add responsive job dashboard"
```

### Task 9: Logging, Browser Auto-Open, and Windows Launchers

**Files:**
- Create: `src/logging_config.py`
- Create: `src/runtime.py`
- Create: `tests/test_logging_config.py`
- Create: `tests/test_runtime.py`
- Modify: `main.py`
- Modify: `ggg_startup.bat`
- Modify: `ggg_startup.vbs`
- Create: `ggg_debug.bat`

**Interfaces:**
- Consumes: `LOG_DIR`, `Repository.get_settings()`, and the FastAPI `app`.
- Produces: `configure_logging(log_dir, today=None)`, `cleanup_old_logs(log_dir, today=None, retention_days=14)`, `default_health_check(url: str) -> bool`, `open_browser_when_healthy(url, health_url, opener=webbrowser.open, health_check=default_health_check)`, and `run(app)`.

- [ ] **Step 1: Write failing log retention tests**

```python
# tests/test_logging_config.py
def test_cleanup_removes_only_logs_older_than_14_days(tmp_path):
    create_log(tmp_path, "gonggonggo-2026-07-19.log")
    create_log(tmp_path, "gonggonggo-2026-07-20.log")
    cleanup_old_logs(tmp_path, today=date(2026, 8, 3), retention_days=14)
    assert not (tmp_path / "gonggonggo-2026-07-19.log").exists()
    assert (tmp_path / "gonggonggo-2026-07-20.log").exists()
```

Also assert that `configure_logging` creates `gonggonggo-YYYY-MM-DD.log`, logs UTF-8 Korean to file and console, and does not duplicate handlers when called twice.

- [ ] **Step 2: Write failing runtime browser tests**

```python
# tests/test_runtime.py
def test_browser_opens_only_after_health_succeeds(fake_health, opener):
    fake_health.responses = [ConnectionError(), 200]
    open_browser_when_healthy(
        "http://127.0.0.1:8000", "http://127.0.0.1:8000/health",
        opener=opener, health_check=fake_health,
    )
    opener.assert_called_once_with("http://127.0.0.1:8000")
```

Add a test that `run` does not create the opener thread when the persisted setting is false.

- [ ] **Step 3: Run logging/runtime tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_logging_config.py tests/test_runtime.py -v`
Expected: FAIL because both modules are missing.

- [ ] **Step 4: Implement logging and runtime launch**

Use a dated `FileHandler` at `logs/gonggonggo-YYYY-MM-DD.log` plus a console handler. Delete only filenames matching the app log pattern whose parsed date is more than 14 days before startup. `run` configures logging, idempotently calls `initialize_database(DB_PATH, JSON_PATH)`, reads settings through a repository, optionally starts one daemon health-check/opener thread, and calls `uvicorn.run(app, host="127.0.0.1", port=8000)`. Update `main.py` in this step so its launch guard imports and calls `run(app)`.

- [ ] **Step 5: Replace Windows launcher behavior**

`ggg_startup.bat` calls `wscript.exe "%~dp0ggg_startup.vbs"` and exits. `ggg_startup.vbs` resolves its own directory and directly launches `.venv\Scripts\python.exe main.py` with window style `0`; it must not call the BAT. `ggg_debug.bat` changes to `%~dp0`, runs `.venv\Scripts\python.exe -u main.py` in the current console, and pauses after exit. Remove shell redirection because Python logging owns both console and file output.

- [ ] **Step 6: Run automated and Windows syntax checks**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_logging_config.py tests/test_runtime.py -v`
Expected: PASS.

Run: `cscript.exe //NoLogo ggg_startup.vbs` only in a controlled manual check after confirming no server is already running; verify no persistent console window remains and the dated log appears. Stop that test server normally before continuing.

- [ ] **Step 7: Commit runtime launchers**

```bash
git add main.py src/logging_config.py src/runtime.py tests/test_logging_config.py tests/test_runtime.py ggg_startup.bat ggg_startup.vbs ggg_debug.bat
git commit -m "feat: add quiet startup and retained logs"
```

### Task 10: End-to-End Verification, Documentation, and Legacy Cleanup

**Files:**
- Create: `tests/e2e/test_dashboard_layout.py`
- Modify: `README.md`
- Modify: `docs/HANDOFF.md`
- Modify: `AGENTS.md` only if implementation changed an approved interface.
- Remove: `test.py`

**Interfaces:**
- Consumes: the completed app, all APIs, Playwright browser, temporary SQLite fixtures, and both responsive CSS modes.
- Produces: verified implementation and accurate operating documentation.

- [ ] **Step 1: Write an end-to-end fixture that starts the app on a free local port**

The fixture creates a temporary DB and JSON copy, calls `create_app` with the temporary paths and a no-network fake crawl manager, starts Uvicorn in a thread, waits for `/health`, yields the base URL, and shuts the server down in `finally`. It must never point at `data/job_posts.json` or the production DB.

- [ ] **Step 2: Write browser layout and interaction tests**

```python
# tests/e2e/test_dashboard_layout.py
def test_wide_and_narrow_status_layouts(page, live_server):
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 2560, "height": 1440})
    page.goto(live_server)
    expect(page.locator("[data-lane]:visible")).to_have_count(2)

    page.set_viewport_size({"width": 1280, "height": 1440})
    expect(page.locator("[data-status-panel]:visible")).to_have_count(1)
```

Also test a status transition during a blocked fake crawl, exclusion undo, settings persistence after app restart, and keyword changes immediately altering visible roles.

- [ ] **Step 3: Run the full automated suite**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: all unit, integration, API, and browser tests PASS without contacting Naver.

- [ ] **Step 4: Run a controlled real smoke test**

Start with `ggg_debug.bat` and verify `/health`, the migrated post count, background crawl progress, and final/partial result display. Do not change statuses or test permanent deletion against production data; the automated temporary-database tests cover those mutations.

- [ ] **Step 5: Verify responsive UI visually**

Capture screenshots at 2560×1440 and 1280×1440. Confirm two lanes versus one status panel, card-local action buttons, structured/fallback titles, long-role truncation, settings drawer, progress status, and Korean text rendering.

- [ ] **Step 6: Update documentation to implemented state**

Replace README's “설계 승인, 구현 전” notice with exact installation, `playwright install chromium`, debug start, scheduled hidden start, settings, data migration, backup, and test commands. Update `docs/HANDOFF.md` with actual file layout, verification output, remaining limitations, and the final commit list. Remove the obsolete manual `test.py` after its useful cases exist in pytest.

- [ ] **Step 7: Check the final diff and commit**

Run: `git diff --check`
Expected: no output.

Run: `git status --short`
Expected: only Task 10 files.

```bash
git add tests/e2e/test_dashboard_layout.py README.md docs/HANDOFF.md AGENTS.md test.py
git commit -m "test: verify modernized local workflow"
```

- [ ] **Step 8: Final verification before handoff**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS.

Run: `git status --short`
Expected: empty output.
