from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.database import initialize_database
from src.domain import CrawlSnapshot, CrawledPost
from src.repository import Repository


class FakeManager:
    def __init__(self, repository: Repository, *, running: bool = False) -> None:
        self.repository = repository
        self.startup_started = False
        self.finished = False
        self._running = running
        self._errors: dict[str, str] = {}
        self.starts: list[tuple[str, tuple[str, ...] | None]] = []

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        self.starts.append((trigger, categories))
        if trigger == "startup":
            self.startup_started = True
            return True
        if self._running:
            return False
        self._running = True
        return True

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot(
            running=self._running,
            category_errors=self._errors,
        )

    async def wait(self) -> None:
        self.finished = True


@pytest.fixture
def app_parts(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    managers: list[FakeManager] = []

    def manager_factory(repository: Repository) -> FakeManager:
        manager = FakeManager(repository)
        managers.append(manager)
        return manager

    from src.web import create_app

    return create_app(
        db_path=db_path,
        json_path=json_path,
        manager_factory=manager_factory,
    ), db_path, managers


@pytest.fixture
def client(app_parts):
    app, _, _ = app_parts
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def repository(app_parts) -> Repository:
    _, db_path, _ = app_parts
    initialize_database(db_path, db_path.with_name("job_posts.json"))
    return Repository(db_path)


@pytest.fixture
def seeded_post(repository: Repository) -> CrawledPost:
    post = CrawledPost(
        category="중앙공기업",
        title="[한국교육학술정보원 채용] 정규직 신입 (전산)",
        deadline_raw="2026.08.10",
        link="https://example.test/jobs/1",
    )
    repository.upsert_crawled_posts([post])
    return post


def test_health_and_home_are_available_before_crawl_finishes(client, app_parts) -> None:
    _, _, managers = app_parts

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert managers[0].startup_started is True
    assert managers[0].finished is False


def test_status_update_uses_validated_enum(client, seeded_post: CrawledPost) -> None:
    response = client.post(
        "/api/posts/status", json={"link": seeded_post.link, "status": "planned"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "planned"
    assert client.post(
        "/api/posts/status",
        json={"link": seeded_post.link, "status": "not-a-status"},
    ).status_code == 422


def test_missing_post_actions_return_not_found(client) -> None:
    assert client.post(
        "/api/posts/status",
        json={"link": "https://example.test/missing", "status": "planned"},
    ).status_code == 404
    assert client.post(
        "/api/posts/delete", json={"link": "https://example.test/missing"}
    ).status_code == 404


def test_duplicate_manual_crawl_returns_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")

    def manager_factory(repository: Repository) -> FakeManager:
        return FakeManager(repository, running=True)

    from src.web import create_app

    with TestClient(create_app(db_path, json_path, manager_factory)) as client:
        assert client.post("/api/crawl/start").status_code == 409


def test_settings_validation_and_keyword_replacement(client, repository: Repository) -> None:
    assert client.put(
        "/api/settings",
        json={
            "concurrency": 3,
            "open_browser": True,
            "institution_keywords": ["대구"],
            "role_keywords": ["전산"],
        },
    ).status_code == 422

    response = client.put(
        "/api/settings",
        json={
            "concurrency": 4,
            "open_browser": True,
            "institution_keywords": [" Target ", "target", "Other"],
            "role_keywords": [" Software "],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "concurrency": 4,
        "open_browser": True,
        "institution_keywords": ["Target", "Other"],
        "role_keywords": ["Software"],
    }
    assert repository.get_keywords("institution") == ("Target", "Other")
    assert client.put(
        "/api/settings",
        json={
            "concurrency": 2,
            "open_browser": False,
            "institution_keywords": [" "],
            "role_keywords": ["전산"],
        },
    ).status_code == 422


def test_delete_and_unblock_link(client, seeded_post: CrawledPost) -> None:
    assert client.post("/api/posts/delete", json={"link": seeded_post.link}).json() == {
        "deleted": True
    }
    deleted_links = client.get("/api/deleted-links").json()["deleted_links"]

    assert deleted_links == [{"id": 1, "link": seeded_post.link}]
    assert client.delete(f"/api/deleted-links/{deleted_links[0]['id']}").json() == {
        "unblocked": True
    }
    assert client.delete("/api/deleted-links/999").status_code == 404


def test_retry_only_accepts_a_failed_category(client, app_parts) -> None:
    _, _, managers = app_parts
    managers[0]._errors = {"중앙공기업": "timeout"}

    assert client.post("/api/crawl/retry/지방공기업").status_code == 422
    response = client.post("/api/crawl/retry/중앙공기업")

    assert response.status_code == 200
    assert managers[0].starts[-1] == ("retry", ("중앙공기업",))


def test_home_escapes_korean_title_with_quotes(client, repository: Repository) -> None:
    title = '[한국교육학술정보원] "안전한" 제목 (전산)'
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="중앙공기업",
                title=title,
                deadline_raw="2026.08.10",
                link="https://example.test/quoted",
            )
        ]
    )

    html = client.get("/").text

    assert title not in html
    assert "한국교육학술정보원" in html
    assert "&#34;안전한&#34;" in html
