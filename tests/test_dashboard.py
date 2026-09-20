from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.database import initialize_database
from src.domain import CrawlSnapshot, CrawledPost
from src.news.domain import NewsCrawlSnapshot
from src.repository import Repository
from src.web import create_app


class IdleManager:
    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        return True

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot()

    async def wait(self) -> None:
        return None


class IdleNewsManager:
    def start(self, trigger: str) -> bool:
        return True

    def snapshot(self) -> NewsCrawlSnapshot:
        return NewsCrawlSnapshot()

    async def wait(self) -> None:
        return None


@pytest.fixture
def dashboard_parts(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    app = create_app(
        db_path,
        json_path,
        lambda repository: IdleManager(),
        news_manager_factory=lambda _repository: IdleNewsManager(),
    )
    return app, db_path


@pytest.fixture
def client(dashboard_parts):
    app, _ = dashboard_parts
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_posts(dashboard_parts) -> tuple[CrawledPost, CrawledPost]:
    _, db_path = dashboard_parts
    initialize_database(db_path, db_path.with_name("job_posts.json"))
    posts = (
        CrawledPost(
            category="중앙공기업",
            title="[한국교육학술정보원 채용] 정규직 신입 (전산/행정)",
            deadline_raw="2099.08.10",
            link="https://example.test/structured",
        ),
        CrawledPost(
            category="대학/기타기관",
            title="[대구교육청 채용] 계약직 경력 (전산)",
            deadline_raw="채용시마감",
            link="https://example.test/open",
        ),
    )
    Repository(db_path).upsert_crawled_posts(list(posts))
    return posts


def test_dashboard_serves_external_assets_and_operational_controls(client) -> None:
    """Catches a page regression that removes the interactive dashboard shell."""
    html = client.get("/").text

    assert 'href="/static/app.css?v=20260825-1"' in html
    assert 'src="/static/app.js?v=20260825-3"' in html
    assert 'href="/static/news.css?v=20260825-1"' in html
    assert 'src="/static/news.js?v=20260825-1"' in html
    assert 'data-primary-tab="jobs"' in html
    assert 'data-primary-tab="news"' in html
    assert 'id="job-view"' in html
    assert 'id="news-view"' in html
    assert 'data-news-period="today"' in html
    assert 'data-news-period="30d"' in html
    assert 'data-news-type=""' in html
    assert 'data-news-type="newspaper"' in html
    assert 'data-news-type="institution"' in html
    assert 'name="news-source"' in html
    assert 'name="news-category"' in html
    assert 'id="news-search-input"' in html
    assert 'id="news-crawl-button"' in html
    assert 'id="news-count"' in html
    assert 'id="news-list"' in html
    assert 'data-view-group="active"' in html
    assert 'data-view-group="archive"' in html
    assert 'id="settings-drawer"' in html
    assert '<button id="crawl-button" class="primary-button" type="button" disabled>' in html
    assert 'id="crawl-status"' in html
    assert "검토 대기" in html
    assert "지원 예정" in html
    assert "지원 완료" in html
    assert "제외" in html
    assert 'id="institution-keywords"' in html
    assert 'id="role-keywords"' in html
    assert 'id="deleted-links"' in html

    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/news.css").status_code == 200
    assert client.get("/static/news.js").status_code == 200


def test_news_client_contract_keeps_news_actions_isolated_and_safe() -> None:
    """Catches a news Inbox that leaks job controls or unsafe external links."""
    script = (Path(__file__).parents[1] / "static" / "news.js").read_text(encoding="utf-8")

    assert 'target = "_blank"' in script
    assert 'rel = "noopener noreferrer"' in script
    assert 'method: "DELETE"' in script
    assert 'await request(`/api/news/${item.id}`, { method: "DELETE" });' in script
    assert script.index('await request(`/api/news/${item.id}`, { method: "DELETE" });') < script.index(
        "state.items = state.items.filter((current) => current.id !== item.id);"
    )
    assert "if (snapshot?.running === false) {" in script
    assert "await refreshNews();" in script
    assert '"/api/crawl/start"' not in script
    assert 'byId("crawl-button")' not in script


def test_news_dashboard_uses_exact_filter_categories_and_labeled_view_controls(client) -> None:
    """Catches UI filters drifting from the validated API vocabulary or unlabeled panels."""
    html = client.get("/").text
    assert html.count('name="news-source"') == 5
    assert html.count('name="news-category"') == 8
    for value in ("주요뉴스", "정치", "경제", "사회", "IT", "세계", "보도자료", "정기 통계"):
        assert f'name="news-category" value="{value}" checked' in html
    assert 'class="news-filter-row news-types" role="group" aria-label="자료 종류"' in html
    assert '<legend>자료 종류</legend>' not in html
    assert '<legend>출처</legend>' not in html
    assert '<legend>분류</legend>' not in html
    assert html.index('aria-label="자료 종류"') < html.index('aria-label="기간"')
    assert html.index('aria-label="기간"') < html.index('aria-label="출처"')
    assert html.index('aria-label="출처"') < html.index('aria-label="분류"')
    assert 'data-news-type="" aria-pressed="true"' in html
    assert 'id="jobs-primary-tab"' in html
    assert 'id="news-primary-tab"' in html
    assert 'aria-controls="job-view"' in html
    assert 'aria-controls="news-view"' in html
    assert 'id="job-view" aria-labelledby="jobs-primary-tab"' in html
    assert 'id="news-view" hidden aria-labelledby="news-primary-tab"' in html


def test_news_client_contract_preserves_crawl_diagnostics_and_lifecycle() -> None:
    """Catches list refreshes clearing crawl diagnostics or abandoning an active crawl."""
    script = (Path(__file__).parents[1] / "static" / "news.js").read_text(encoding="utf-8")

    assert 'const listError = byId("news-errors");' in script
    assert 'const crawlError = byId("news-crawl-errors");' in script
    assert "function clearListError()" in script
    assert "function showCrawlError(message)" in script
    assert "if (error.status !== 409) {" in script
    assert "state.crawlActive = true;" in script
    assert "scheduleNewsPolling();" in script
    assert "if (state.listError) {" in script
    assert script.index("if (state.listError) {") < script.index("if (!items.length) {")


def test_news_client_contract_invalidates_stale_results_and_keeps_date_metadata() -> None:
    """Catches an old list response restoring a dismissed item or dropping publication metadata."""
    script = (Path(__file__).parents[1] / "static" / "news.js").read_text(encoding="utf-8")

    assert "++state.requestVersion;" in script
    assert script.index("++state.requestVersion;") < script.index(
        'await request(`/api/news/${item.id}`, { method: "DELETE" });'
    )
    assert "await refreshNews();" in script
    assert 'time.dateTime = item.published_at || item.discovered_at || "";' in script
    assert "toLocaleString(\"ko-KR\"" in script
    assert "수집일 기준" in script
    assert 'createExternalLink("원문 보기"' not in script
    assert "localStorage.getItem(FILTER_STORAGE_KEY)" in script
    assert "localStorage.setItem(FILTER_STORAGE_KEY" in script
    assert 'newsCount.textContent = `총 ${items.length}건`;' in script


def test_news_client_contract_always_reports_failed_dismissals() -> None:
    """Catches a newer list refresh silencing a failed processing request."""
    script = (Path(__file__).parents[1] / "static" / "news.js").read_text(encoding="utf-8")
    dismiss = script.split("async function dismissNews", 1)[1].split("function newsQuery", 1)[0]

    assert "button.disabled = false;" in dismiss
    assert "showListError(error.message);" in dismiss
    assert "deleteRequestVersion === state.requestVersion" not in dismiss
    assert dismiss.index('await request(`/api/news/${item.id}`, { method: "DELETE" });') < dismiss.index(
        "state.items = state.items.filter((current) => current.id !== item.id);"
    )


def test_post_api_exposes_structured_fields_and_preserves_original_title(
    client, seeded_posts: tuple[CrawledPost, CrawledPost]
) -> None:
    """Catches loss of the original title needed for a client-side fallback card."""
    posts = client.get("/api/posts").json()["posts"]
    structured = next(post for post in posts if post["link"].endswith("structured"))

    assert structured["institution"] == "한국교육학술정보원"
    assert structured["display_roles"] == ["전산"]
    assert structured["original_title"] == "[한국교육학술정보원 채용] 정규직 신입 (전산/행정)"
    assert structured["is_institution_match"] is True


def test_post_api_exposes_parse_failed_target_as_a_fallback_card(client, dashboard_parts) -> None:
    """Catches the API dropping an unstructured target post before the UI can fall back."""
    _, db_path = dashboard_parts
    Repository(db_path).upsert_crawled_posts(
        [
            CrawledPost(
                category="중앙공기업",
                title="[한국교육학술정보원 채용] 정규직 신입",
                deadline_raw="채용시마감",
                link="https://example.test/raw-target",
            )
        ]
    )

    posts = client.get("/api/posts").json()["posts"]

    assert len(posts) == 1
    assert {
        key: posts[0][key]
        for key in (
            "link", "original_title", "institution", "display_roles", "deadline_kind", "status"
        )
    } == {
        "link": "https://example.test/raw-target",
        "original_title": "[한국교육학술정보원 채용] 정규직 신입",
        "institution": "한국교육학술정보원",
        "display_roles": [],
        "deadline_kind": "open",
        "status": "review_pending",
    }


def test_client_card_contract_handles_fallback_deletion_and_completed_crawls() -> None:
    """Catches regressions in card actions and fast-crawl refresh coordination."""
    script = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")

    assert 'post.status === "excluded" || post.deadline_kind !== "dated"' in script
    assert "deletePost(post)" in script
    assert "togglePin(post)" in script
    assert 'link.classList.add("institution-filtered")' in script
    assert '"기관 필터"' not in script and '"직무 필터"' not in script
    assert "plannedPriority(first)" in script
    assert "if (first.status !== second.status)" in script
    assert "if (post.is_institution_match) return post.is_pinned ? 0 : 1;" in script
    assert 'card.append(textElement("p", post.original_title, "job-meta"))' not in script
    assert 'roleElement.title = role' in script
    assert "최근 수집:" in script
    assert "if (snapshot?.running) startPolling();" in script
    assert "if (snapshot?.running === false) await refreshPosts();" in script


def test_client_status_failure_contract_keeps_initial_posts_load_safe() -> None:
    """Catches a temporary status failure dereferencing null before posts can load."""
    script = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    start_crawl = script.split("async function startCrawl()", 1)[1].split(
        "function renderKeywords", 1
    )[0]
    initialize = script.split("async function initialize()", 1)[1]

    assert "if (snapshot?.running !== false) startPolling();" in start_crawl
    assert "if (snapshot?.running === false) await refreshPosts();" in start_crawl
    assert "if (snapshot?.running) startPolling();" in initialize
    assert "await refreshPosts();" in initialize


def test_manual_crawl_contract_tracks_an_accepted_run_after_null_status() -> None:
    """Catches an accepted manual crawl losing its completion refresh after a status outage."""
    script = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    start_crawl = script.split("async function startCrawl()", 1)[1].split(
        "function renderKeywords", 1
    )[0]

    assert 'await request("/api/crawl/start", { method: "POST" });' in start_crawl
    assert "crawlButton.disabled = true;" in start_crawl
    assert "if (snapshot?.running !== false) startPolling();" in start_crawl
    assert "if (snapshot?.running === false) await refreshPosts();" in start_crawl
