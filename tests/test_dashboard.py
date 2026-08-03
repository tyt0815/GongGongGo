from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.database import initialize_database
from src.domain import CrawlSnapshot, CrawledPost
from src.repository import Repository
from src.web import create_app


class IdleManager:
    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        return True

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot()

    async def wait(self) -> None:
        return None


@pytest.fixture
def dashboard_parts(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    app = create_app(db_path, json_path, lambda repository: IdleManager())
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
            deadline_raw="2026.08.10",
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

    assert 'href="/static/app.css"' in html
    assert 'src="/static/app.js"' in html
    assert 'data-view-group="active"' in html
    assert 'data-view-group="archive"' in html
    assert 'id="settings-drawer"' in html
    assert 'id="crawl-button"' in html
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


def test_post_api_exposes_structured_fields_and_preserves_original_title(
    client, seeded_posts: tuple[CrawledPost, CrawledPost]
) -> None:
    """Catches loss of the original title needed for a client-side fallback card."""
    posts = client.get("/api/posts").json()["posts"]
    structured = next(post for post in posts if post["link"].endswith("structured"))

    assert structured["institution"] == "한국교육학술정보원"
    assert structured["display_roles"] == ["전산"]
    assert structured["original_title"] == "[한국교육학술정보원 채용] 정규직 신입 (전산/행정)"
