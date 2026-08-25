# News and Institution Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a short-lived, independently crawled news and institution press-release Inbox without changing the existing job-post domain or workflows.

**Architecture:** Add a focused `src/news/` package containing source-specific static-HTML crawlers, an independent repository and manager, and small shared domain/HTTP utilities. Integrate it only at database initialization, FastAPI lifespan/routes, and a separate progressive-enhancement UI so the current job crawler, repository, API, and dashboard JavaScript remain intact.

**Tech Stack:** Python 3.12, FastAPI, SQLite WAL, standard-library `urllib`, BeautifulSoup 4.15.0, vanilla HTML/CSS/JavaScript, pytest, pytest-asyncio, Playwright E2E

**Spec:** `docs/superpowers/specs/2026-08-25-news-inbox-design.md`

## Global Constraints

- Bind only to `127.0.0.1`; do not add authentication, external deployment, notifications, or a repeating scheduler.
- The server must accept requests before either the job crawl or news crawl finishes.
- Keep `job_posts`, `CrawledPost`, `Repository`, `CrawlManager`, and `src/crawler.py` job-specific.
- Preserve all existing job APIs, filters, statuses, deletion behavior, settings, crawl history, and operating data.
- Store no body, attachment, AI result, bookmark, or read state.
- Crawl only the five approved official list sources; never crawl Naver News or article bodies.
- Newspaper retention is 7 Seoul calendar days; institution retention is 30 Seoul calendar days.
- Processing deletes an item immediately and suppresses it only until its original TTL boundary.
- Use short SQLite transactions, separate connections, WAL, and the existing 5000 ms busy timeout.
- Automated tests must not access the network or `data/gonggonggo.db`.
- Keep Korean text and fixtures UTF-8.
- Do not commit runtime DBs, logs, `.superpowers/`, or the user's untracked `new_project.md`.
- Add only `beautifulsoup4==4.15.0` as a new runtime dependency.

## File Map

Create `src/news/{domain,registry,url_normalization,classification,schema,repository,http,manager}.py`, `src/news/sources/{hankyung,mk,reb,kodit,kogas}.py`, `static/news.js`, and `static/news.css`. Create focused tests under `tests/news/` plus five minimal HTML fixtures. Modify only `requirements.txt`, `src/database.py`, `src/web.py`, `templates/index.html`, affected test fixtures/contracts, `README.md`, and `docs/HANDOFF.md`.

---

### Task 1: Domain contract and pure helpers

**Files:**
- Create: `src/news/__init__.py`
- Create: `src/news/domain.py`
- Create: `src/news/registry.py`
- Create: `src/news/url_normalization.py`
- Create: `src/news/classification.py`
- Test: `tests/news/test_domain.py`

**Interfaces:**
- Consumes: Python 3.12 dataclasses, `StrEnum`, aware `datetime`.
- Produces: `NewsItemType`, `NewsPeriod`, `CrawledNewsItem`, `NewsRecord`, `SourceResult`, `SaveStats`, `CleanupStats`, `NewsCrawlSnapshot`, `SourceDefinition`, `SOURCES`, `NEWS_CATEGORIES`, `normalize_url()`, `classify_institution_title()`.

- [ ] **Step 1: Run the untouched baseline**

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Expected: the existing 112 tests pass without network access or warnings.

- [ ] **Step 2: Write failing pure-contract tests**

```python
def test_registry_and_period_contract() -> None:
    assert tuple(SOURCES) == ("hankyung", "mk", "reb", "kodit", "kogas")
    assert SOURCES["hankyung"].ttl_days == 7
    assert SOURCES["reb"].ttl_days == 30
    assert NEWS_CATEGORIES == ("주요뉴스", "정치", "경제", "사회", "IT", "세계")
    assert [value.value for value in NewsPeriod] == ["today", "yesterday", "3d", "7d", "30d"]


def test_url_normalization_is_conservative() -> None:
    value = "HTTP://Example.COM:80/a/?utm_source=x&boardIdx=41#fragment"
    assert normalize_url(value) == "http://example.com/a?boardIdx=41"
    assert normalize_url("https://x.test/v?bbsId=47&nttSn=9") == "https://x.test/v?bbsId=47&nttSn=9"


def test_reb_classifier_is_narrow() -> None:
    assert classify_institution_title("주간아파트가격동향(20260817기준)") == "정기 통계"
    assert classify_institution_title("26.7월 전국주택가격동향") == "정기 통계"
    assert classify_institution_title("상업용부동산 임대동향조사 결과") == "정기 통계"
    assert classify_institution_title("부동산 거래가격 거짓신고 집중 운영") == "보도자료"
```

- [ ] **Step 3: Verify the tests fail for the missing package**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_domain.py -v
```

Expected: collection fails on `src.news` import.

- [ ] **Step 4: Implement exact shared types**

Define frozen records with these fields:

```python
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
```

Also define these frozen records exactly:

```python
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
```

- [ ] **Step 5: Implement registry and helper behavior**

`SOURCES` is insertion ordered with IDs/names/types/TTLs for 한국경제, 매일경제, 한국부동산원, 신용보증기금, 한국가스공사. `normalize_url()` lowercases scheme/host, removes default ports, fragment, trailing non-root slash, `utm_*`, `fbclid`, `gclid`, and `n_cid`, but preserves identity queries. The classifier collapses whitespace and matches only the three approved phrases.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_domain.py -v
git add src/news tests/news/test_domain.py
git commit -m "feat: define news inbox domain"
```

Expected: focused tests pass.

---

### Task 2: Additive news schema

**Files:**
- Create: `src/news/schema.py`
- Modify: `src/database.py` in `initialize_database()`
- Create: `tests/news/test_schema.py`
- Modify: `tests/test_database.py`

**Interfaces:**
- Consumes: existing `connect()` and `initialize_database()`.
- Produces: `initialize_news_schema(connection: sqlite3.Connection) -> None`.

- [ ] **Step 1: Write an upgrade regression test**

```python
def test_news_schema_is_added_without_changing_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    initialize_database(db_path, json_path)
    Repository(db_path).upsert_crawled_posts([
        CrawledPost("중앙공기업", "[기관 채용] 정규직 신입 (전산)", "2099.12.31", "https://x.test/job")
    ])
    initialize_database(db_path, json_path)
    with connect(db_path) as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"news_items", "news_dismissals"} <= names
        assert connection.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 1
```

Also assert repeated initialization, news column names, URL uniqueness, and unchanged job constraints.

- [ ] **Step 2: Verify the new test fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_schema.py tests/test_database.py -v
```

Expected: only the missing news tables fail.

- [ ] **Step 3: Implement exact schema**

```sql
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY,
    item_type TEXT NOT NULL CHECK (item_type IN ('newspaper', 'institution')),
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category TEXT NOT NULL,
    source_category TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    published_at TEXT,
    discovered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS news_items_filter_index ON news_items(item_type, source, category);
CREATE TABLE IF NOT EXISTS news_dismissals (
    url TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS news_dismissals_expiry_index ON news_dismissals(expires_at);
```

Call the helper after the existing schema `executescript()` and before settings seeding. Do not alter old tables or migration metadata.

- [ ] **Step 4: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_schema.py tests/test_database.py -v
git add src/news/schema.py src/database.py tests/news/test_schema.py tests/test_database.py
git commit -m "feat: add news inbox schema"
```

Expected: all selected tests pass.

---

### Task 3: Repository, TTL, dismissal, and filters

**Files:**
- Create: `src/news/repository.py`
- Test: `tests/news/test_repository.py`

**Interfaces:**
- Consumes: Task 1 domain/registry/normalizer and Task 2 tables.
- Produces: `NewsRepository.cleanup(now=None)`, `upsert_items(items, now=None)`, `list_items(period, item_type=None, source=None, category=None, query="", today=None)`, `dismiss(item_id, now=None)`.

- [ ] **Step 1: Write failing fixed-clock tests**

Use `datetime(2026, 8, 25, 12, tzinfo=ZoneInfo("Asia/Seoul"))` and cover:

```python
def test_upsert_normalizes_deduplicates_and_prefers_specific_category(repository, now) -> None:
    stats = repository.upsert_items([
        item("https://x.test/a?utm_source=n", category="주요뉴스"),
        item("https://x.test/a", category="IT"),
    ], now=now)
    rows = repository.list_items(NewsPeriod.TODAY, today=now.date())
    assert stats == SaveStats(new_count=1, duplicate_count=1, suppressed_count=0)
    assert (rows[0].url, rows[0].category) == ("https://x.test/a", "IT")


def test_cleanup_respects_inclusive_ttls(repository, now) -> None:
    repository.upsert_items([
        item("https://x.test/n-old", published_at=now - timedelta(days=7)),
        item("https://x.test/n-keep", published_at=now - timedelta(days=6)),
        item("https://x.test/i-old", item_type=NewsItemType.INSTITUTION,
             source="reb", source_name="한국부동산원", published_at=now - timedelta(days=30)),
    ], now=now)
    assert repository.cleanup(now=now).item_count == 2


def test_dismissal_expires_at_original_ttl_boundary(repository, now) -> None:
    published = now - timedelta(days=5)
    repository.upsert_items([item("https://x.test/a", published_at=published)], now=now)
    row = repository.list_items(NewsPeriod.SEVEN_DAYS, today=now.date())[0]
    assert repository.dismiss(row.id, now=now)
    assert repository.upsert_items([item(row.url, published_at=published)], now=now).suppressed_count == 1
    repository.cleanup(now=now + timedelta(days=2))
    assert repository.upsert_items([item(row.url, published_at=now)], now=now + timedelta(days=2)).new_count == 1
```

Parameterize the five periods, type/source/category filters, literal `%`/`_` title searches, published-date fallback, newest-first ordering, and missing dismissal ID.

- [ ] **Step 2: Verify missing repository failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_repository.py -v
```

- [ ] **Step 3: Implement short connections and Seoul dates**

Mirror the current repository context manager. Require aware datetimes, convert to Seoul ISO, normalize all URLs, and use `substr(COALESCE(published_at, discovered_at), 1, 10)` as effective date. Cutoffs are today minus 6 and 29 days; delete strictly older rows.

- [ ] **Step 4: Implement atomic persistence**

Load existing and active-dismissal URLs once per transaction. Deduplicate the input while allowing a specific category to replace `주요뉴스`. Preserve `discovered_at` on conflicts; update title, fill missing publication time, and never downgrade category. Return exact new/duplicate/suppressed counts.

- [ ] **Step 5: Implement queries and dismissal**

Map periods to inclusive offsets `(0,0)`, `(1,1)`, `(2,0)`, `(6,0)`, `(29,0)`. Bind all SQL values. Escape backslash, `%`, and `_` for `LIKE ... ESCAPE '\'`. In one transaction, dismissal reads the row, inserts `expires_at` at Seoul midnight `effective_date + ttl_days`, then deletes the item.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_repository.py tests/news/test_schema.py -v
git add src/news/repository.py tests/news/test_repository.py
git commit -m "feat: persist short-lived news items"
```

Expected: all selected tests pass.

---

### Task 4: HTTP boundary and newspaper sources

**Files:**
- Modify: `requirements.txt`
- Create: `src/news/http.py`
- Create: `src/news/sources/__init__.py`
- Create: `src/news/sources/hankyung.py`
- Create: `src/news/sources/mk.py`
- Test: `tests/news/test_newspaper_sources.py`
- Create: `tests/news/fixtures/hankyung.html`
- Create: `tests/news/fixtures/mk.html`

**Interfaces:**
- Consumes: Task 1 types/helpers.
- Produces: `fetch_html(url) -> bytes`, `parse_hankyung_page()`, `parse_mk_page()`, `parse_mk_main_page()`, and each module's synchronous `crawl(fetcher=fetch_html) -> SourceResult`.

- [ ] **Step 1: Add the sole dependency**

Add `beautifulsoup4==4.15.0` to `requirements.txt`, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

- [ ] **Step 2: Create minimal observed fixtures and failing tests**

Hankyung fixture: `ul.allnews-list > li`, `h2.news-tit > a`, `p.txt-date`. MK fixture: one section page with `ul#list_area > li.article_list`, `a.news_item`, `.art_area h4`, `.time_area span`, plus one main-page block with `a.news_item[data-section='headline']` and `a.news_item[data-section='main']`. Include one link/title-malformed row in each. A missing or invalid date is a valid item with `published_at=None`, not a link/title-malformed item.

```python
def test_hankyung_list_parser(fixtures) -> None:
    items, malformed = parse_hankyung_page(fixtures("hankyung.html"), "IT")
    assert items[0].url == "https://www.hankyung.com/article/202608251361i"
    assert items[0].published_at.isoformat() == "2026-08-25T14:05:00+09:00"
    assert malformed == 1


def test_mk_list_parser(fixtures) -> None:
    items, malformed = parse_mk_page(fixtures("mk.html"), "IT")
    assert items[0].published_at.isoformat() == "2026-08-25T00:00:00+09:00"
    assert malformed == 1


def test_mk_main_parser_keeps_curated_items_with_discovery_fallback(fixtures) -> None:
    items, malformed = parse_mk_main_page(fixtures("mk.html"), "주요뉴스")
    assert items[0].category == "주요뉴스"
    assert items[0].published_at is None
    assert malformed == 0


def test_specific_categories_are_fetched_before_main(fixtures) -> None:
    calls: list[str] = []
    result = hankyung.crawl(fetcher=lambda url: calls.append(url) or fixtures("hankyung.html"))
    assert result.error is None
    assert len(calls) == 6
    assert calls[-1] == "https://www.hankyung.com/all-news"
```

Also test empty valid container, missing container failure, and fetch exception isolation.

- [ ] **Step 3: Verify parser imports fail**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_newspaper_sources.py -v
```

- [ ] **Step 4: Implement bounded HTTP**

Use `urllib.request.Request` with User-Agent `GongGongGo/1.0 personal-local-news-reader`, Korean Accept-Language, 15-second timeout, 2xx requirement, and 5 MiB maximum body. Return bytes and do not retry.

- [ ] **Step 5: Implement exact selectors and category order**

Use BeautifulSoup `html.parser`, whitespace collapse, `urljoin`, conservative normalization, and aware Seoul dates. Set `source_category` to the source's displayed section label and `category` to the approved internal label. Hankyung parses the three observed selectors and full timestamp. MK section pages parse `ul#list_area` rows and the calendar date, ignoring relative `time_info`; the MK main page accepts only `headline` and `main` data sections and uses `published_at=None`. Skip rows missing a usable title or article link; retain date-missing rows for discovery fallback. A missing expected container fails the source. Fetch `정치, 경제, 사회, IT, 세계, 주요뉴스` in that order, one page each, using official `/all-news-*`, `/news/{section}/`, and `/news/` URLs.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_newspaper_sources.py -v
git add requirements.txt src/news/http.py src/news/sources tests/news/test_newspaper_sources.py tests/news/fixtures/hankyung.html tests/news/fixtures/mk.html
git commit -m "feat: crawl newspaper lists"
```

Expected: focused tests pass without network.

---

### Task 5: Institution source isolation and bounded pagination

**Files:**
- Create: `src/news/sources/reb.py`
- Create: `src/news/sources/kodit.py`
- Create: `src/news/sources/kogas.py`
- Modify: `src/news/sources/__init__.py`
- Test: `tests/news/test_institution_sources.py`
- Create: `tests/news/fixtures/reb.html`
- Create: `tests/news/fixtures/kodit.html`
- Create: `tests/news/fixtures/kogas.html`

**Interfaces:**
- Consumes: Task 4 HTTP and Task 1 domain/classification.
- Produces: three `parse_*_page()` functions, three `crawl(fetcher=fetch_html, today=None)` functions, and `SOURCE_CRAWLERS`.

- [ ] **Step 1: Create minimal observed fixtures and failing tests**

REB fixture uses `td.al.mBlock a.nttInfoBtn[data-id]` and `YYYY.MM.DD.`. KODIT uses `td.bbs_tit a.nttInfoBtn[data-id]` and `YYYY.MM.DD`. KOGAS uses `.list_body.webzine .list_item`, `readPermissionChk(id)`, `strong.title`, and `.wdate .value`.

```python
def test_reb_direct_url_and_classification(fixtures) -> None:
    items, malformed = parse_reb_page(fixtures("reb.html"))
    assert items[0].category == "정기 통계"
    assert items[0].url.endswith("selectNttInfo.do?bbsId=1154&mi=9565&nttSn=116262")
    assert malformed == 1


def test_kodit_and_kogas_direct_urls(fixtures) -> None:
    kodit_items, _ = parse_kodit_page(fixtures("kodit.html"))
    kogas_items, _ = parse_kogas_page(fixtures("kogas.html"))
    assert "bbsId=47&mi=2639&nttSn=5094548" in kodit_items[0].url
    assert "Key=1010202000000&boardIdx=47656&cbIdx=41" in kogas_items[0].url
```

Parameterize pagination to assert REB/KODIT `currPage=2`, KOGAS `pageIndex=2&pageOffset=10`, cutoff stop, 10-page cap, missing container failure, and malformed item isolation.

- [ ] **Step 2: Verify source imports fail**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_institution_sources.py -v
```

- [ ] **Step 3: Implement each source independently**

Build direct view URLs from `data-id` or `readPermissionChk()` integer; never store `javascript:`. Parse valid dates as Seoul midnight. Loop pages 1–10, retain rows at least `today - 29 days`, and stop after a parsed page contains an older valid row. Retain malformed-date items with `published_at=None` so discovery time is the safe fallback.

- [ ] **Step 4: Register all sources**

```python
SOURCE_CRAWLERS = {
    "hankyung": hankyung.crawl,
    "mk": mk.crawl,
    "reb": reb.crawl,
    "kodit": kodit.crawl,
    "kogas": kogas.crawl,
}
assert tuple(SOURCE_CRAWLERS) == tuple(SOURCES)
```

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_newspaper_sources.py tests/news/test_institution_sources.py -v
git add src/news/sources tests/news/test_institution_sources.py tests/news/fixtures/reb.html tests/news/fixtures/kodit.html tests/news/fixtures/kogas.html
git commit -m "feat: crawl institution press releases"
```

Expected: all source tests pass offline.

---

### Task 6: Independent news manager and partial failure

**Files:**
- Create: `src/news/manager.py`
- Test: `tests/news/test_manager.py`

**Interfaces:**
- Consumes: `NewsRepository`, `SOURCE_CRAWLERS`, source/snapshot records.
- Produces: `NewsCrawlManager.start(trigger) -> bool`, `snapshot() -> NewsCrawlSnapshot`, `wait() -> None`.

- [ ] **Step 1: Write failing async lifecycle tests**

```python
@pytest.mark.asyncio
async def test_partial_failure_saves_peers(repository) -> None:
    crawlers = {
        "hankyung": lambda: SourceResult("hankyung", (item("hankyung"),)),
        "mk": lambda: SourceResult("mk", error="selector missing"),
    }
    manager = NewsCrawlManager(repository, crawlers=crawlers)
    assert manager.start("manual")
    assert manager.start("manual") is False
    await manager.wait()
    state = manager.snapshot()
    assert (state.completed_sources, state.new_count) == (2, 1)
    assert state.source_errors == {"mk": "selector missing"}


@pytest.mark.asyncio
async def test_source_runs_off_event_loop(repository) -> None:
    owner = threading.get_ident()
    worker: list[int] = []
    manager = NewsCrawlManager(repository, crawlers={
        "reb": lambda: worker.append(threading.get_ident()) or SourceResult("reb")
    })
    manager.start("startup")
    await manager.wait()
    assert worker[0] != owner
```

Also test cleanup failure as `run_error`, per-source DB save failure, totals, snapshot copy safety, and wait after completion.

- [ ] **Step 2: Verify missing manager failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_manager.py -v
```

- [ ] **Step 3: Implement lifecycle and isolation**

Follow the existing create-task pattern without inheritance. Reject only an active news task. Run each synchronous source through `asyncio.to_thread`, consume with `asyncio.as_completed`, save successful source results immediately in another thread, and advance completion in `finally`. Cleanup once before sources; if it fails, set `run_error` and make no requests. Use a lock for immutable snapshot replacement/copy.

Log run/source start, success, malformed/new/duplicate/suppressed/expired totals, failures, and final state. Do not log each item at INFO.

- [ ] **Step 4: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_manager.py tests/news/test_repository.py -v
git add src/news/manager.py tests/news/test_manager.py
git commit -m "feat: manage independent news crawls"
```

Expected: selected tests pass.

---

### Task 7: FastAPI lifecycle and validated news APIs

**Files:**
- Modify: `src/web.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_dashboard.py` fixtures only
- Modify: `tests/e2e/test_dashboard_layout.py` fixtures only
- Test: `tests/news/test_web.py`

**Interfaces:**
- Consumes: news repository/manager and validation types.
- Produces: `GET /api/news`, `DELETE /api/news/{id}`, `POST /api/news/crawl/start`, `GET /api/news/crawl/status`.

- [ ] **Step 1: Inject idle news managers in every existing app fixture**

Add a fake implementing `start(trigger)`, `snapshot()`, and `wait()`. Keep the third positional `create_app()` parameter as the existing job manager factory so old callers retain meaning.

- [ ] **Step 2: Write failing API/lifespan tests**

```python
def test_both_startup_managers_begin_before_health(client, managers) -> None:
    assert client.get("/health").status_code == 200
    assert managers.job.starts == [("startup", None)]
    assert managers.news.starts == ["startup"]
    assert managers.job.finished is False
    assert managers.news.finished is False


def test_news_query_and_dismissal(client, seeded_news) -> None:
    response = client.get("/api/news", params={
        "period": "7d", "item_type": "newspaper", "source": "hankyung",
        "category": "IT", "q": "AI",
    })
    item_id = response.json()["items"][0]["id"]
    assert client.delete(f"/api/news/{item_id}").json() == {"dismissed": True}
    assert client.delete(f"/api/news/{item_id}").status_code == 404


def test_news_validation(client) -> None:
    assert client.get("/api/news?period=90d").status_code == 422
    assert client.get("/api/news?source=unknown").status_code == 422
    assert client.get("/api/news?category=연예").status_code == 422
```

Also test status JSON, independent 409 behavior, logged DB 503, and shutdown awaiting both managers when one wait raises.

- [ ] **Step 3: Verify routes/factory are absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_web.py tests/test_web.py -v
```

- [ ] **Step 4: Extend `create_app()` compatibly**

Add the exact web-layer contract before `create_app()`:

```python
class NewsCoordinator(Protocol):
    def start(self, trigger: str) -> bool: ...
    def snapshot(self) -> NewsCrawlSnapshot: ...
    async def wait(self) -> None: ...


NewsManagerFactory = Callable[[NewsRepository], NewsCoordinator]
```

The protocol ellipses are Python protocol method bodies, not unfinished implementation work.

```python
def create_app(
    db_path: Path = DB_PATH,
    json_path: Path = JSON_PATH,
    manager_factory: ManagerFactory | None = None,
    crawler_headless: bool = True,
    news_manager_factory: NewsManagerFactory | None = None,
) -> FastAPI:
```

Keep `app.state.repository` and `.manager`; add `.news_repository` and `.news_manager`. Start both without awaiting. At shutdown use `asyncio.gather(..., return_exceptions=True)` and log each exception so both waits occur.

- [ ] **Step 5: Implement routes and serialization**

Use enum parsing for period/type, `Query(max_length=200)` for search, and registry validation for source/category. Serialize aware datetimes and `used_discovered_date`. Missing dismissal is 404, duplicate news start is 409, and news SQLite failures reuse `_database_unavailable()`.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/news/test_web.py tests/test_web.py tests/test_dashboard.py -v
git add src/web.py tests/test_web.py tests/test_dashboard.py tests/e2e/test_dashboard_layout.py tests/news/test_web.py
git commit -m "feat: expose news inbox APIs"
```

Expected: all web tests pass with no live requests.

---

### Task 8: Dense responsive news UI

**Files:**
- Modify: `templates/index.html`
- Create: `static/news.js`
- Create: `static/news.css`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: Task 7 APIs.
- Produces: top-level view tabs, filters, dense list, new-tab links, processing, and news-only polling.

- [ ] **Step 1: Write failing HTML/static contracts**

Keep all job assertions and add:

```python
assert 'href="/static/news.css"' in html
assert 'src="/static/news.js"' in html
assert 'data-primary-tab="jobs"' in html
assert 'data-primary-tab="news"' in html
assert 'id="job-view"' in html
assert 'id="news-view"' in html
assert 'data-news-period="today"' in html
assert 'data-news-period="30d"' in html
assert 'id="news-type-filter"' in html
assert 'id="news-source-filter"' in html
assert 'id="news-category-filter"' in html
assert 'id="news-search-input"' in html
assert 'id="news-crawl-button"' in html
assert 'id="news-list"' in html
```

Assert `news.js` contains safe new-tab attributes, DELETE request, removal only after success, completion refresh, and no job crawl endpoint mutation.

- [ ] **Step 2: Verify contracts fail**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -v
```

- [ ] **Step 3: Add isolated semantic markup**

Add `채용공고 | 뉴스/기관소식` buttons before the current header. Wrap existing job content in `#job-view` without renaming any ID/data attribute. Add hidden `#news-view` with five period buttons, labeled type/source/category selects, title search, news crawl button/status/errors, and `#news-list`. Load news CSS after app CSS and news JS after app JS.

- [ ] **Step 4: Implement safe client behavior**

Use an IIFE, `textContent`, DOM constructors, a 250 ms search debounce, and request-version protection. Lazy-load on first news-tab selection. Render `.news-row` metadata, title link, identical 원문 보기 link, and 처리 완료 button. Both links use `_blank` and `noopener noreferrer`. Disable only the clicked button; remove row only after successful DELETE; on failure keep it and show error.

- [ ] **Step 5: Implement news-only polling**

POST the news start endpoint and poll its status every second while running. Show completed/total, new/duplicate/TTL counts and source errors. On first terminal state, stop polling, re-enable only the news button, and refresh the current query once. Never touch the job crawl button or endpoint.

- [ ] **Step 6: Add dense responsive CSS**

Desktop uses a compact grid for metadata/title/actions. At 700 px switch to one column with 44 px action targets. Use `min-width:0`, `overflow-wrap:anywhere`, and no horizontal overflow. Reuse existing colors/button vocabulary.

- [ ] **Step 7: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/news/test_web.py tests/test_web.py -v
git add templates/index.html static/news.js static/news.css tests/test_dashboard.py
git commit -m "feat: add news inbox dashboard"
```

Expected: selected tests pass.

---

### Task 9: E2E, live selector smoke, docs, and final verification

**Files:**
- Modify: `tests/e2e/test_dashboard_layout.py`
- Modify: `README.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: browser regression evidence, five-source selector evidence, synchronized docs, and final verification.

- [ ] **Step 1: Add deterministic news E2E state and scenarios**

Seed temporary news records and inject a fake news manager. Preserve all job E2E checks. Add:

```python
def test_news_filters_opens_and_dismisses(page: Page, live_server: LiveServer) -> None:
    page.goto(live_server.url)
    page.get_by_role("button", name="뉴스/기관소식").click()
    page.get_by_role("button", name="최근 7일").click()
    page.get_by_label("자료 종류").select_option("newspaper")
    page.get_by_label("출처").select_option("hankyung")
    page.get_by_label("분류").select_option("IT")
    page.get_by_label("제목 검색").fill("AI")
    row = page.locator(".news-row", has_text="공공부문 AI 전환")
    expect(row).to_be_visible()
    expect(row.locator("a.news-title")).to_have_attribute("target", "_blank")
    row.get_by_role("button", name="처리 완료").click()
    expect(row).to_have_count(0)
```

At 390 px assert the row stays inside viewport and switching back restores the current job status panel. Add a blocked news crawl proving the job crawl button remains enabled.

- [ ] **Step 2: Run E2E**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_dashboard_layout.py -v
```

Expected: all old and new scenarios pass; failure screenshots stay under pytest temporary artifacts.

- [ ] **Step 3: Run a read-only five-source smoke without a DB**

```powershell
.\.venv\Scripts\python.exe -c "from src.news.sources import SOURCE_CRAWLERS; [print(name, len((result := crawl()).items), result.error) for name, crawl in SOURCE_CRAWLERS.items()]"
```

Expected: five lines, every count above zero, every error `None`. On a repeated selector failure, change only that source parser and minimal fixture, rerun its focused test, then repeat this smoke.

- [ ] **Step 4: Update implemented-state docs**

README records the five sources, title/link-only collection, TTL/filter/dismissal semantics, independent startup/manual collection, and dependency installation. HANDOFF records exact module/table ownership, temporary suppression invariant, category mapping, manager independence, tests, live-smoke result, final pass count, and unchanged job/operating-data warnings.

- [ ] **Step 5: Run full verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -v
git diff --check
git status --short
git diff -- data/job_posts.json data/gonggonggo.db
```

Expected: every test passes without warnings; diff check is silent; operating data has no diff; `new_project.md` remains untouched and untracked.

- [ ] **Step 6: Commit verification and documentation**

```powershell
git add tests/e2e/test_dashboard_layout.py README.md docs/HANDOFF.md
git commit -m "test: verify news inbox workflow"
git log -10 --oneline
git status --short
```

Expected: task commits are ordered, no task-owned changes remain, and only the user's `new_project.md` may remain untracked.
