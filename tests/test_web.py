import asyncio
import sqlite3
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.database import connect, initialize_database
from src.domain import CrawlSnapshot, CrawledPost, Settings
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
        deadline_raw="2099.08.10",
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


def test_manual_crawl_starts_on_the_application_event_loop(client, app_parts) -> None:
    _, _, managers = app_parts
    original_start = managers[0].start

    def loop_checking_start(
        trigger: str, categories: tuple[str, ...] | None = None
    ) -> bool:
        if trigger == "manual":
            asyncio.get_running_loop()
        return original_start(trigger, categories)

    managers[0].start = loop_checking_start

    response = client.post("/api/crawl/start")

    assert response.status_code == 200
    assert response.json() == {"started": True}


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
    empty_response = client.put(
        "/api/settings",
        json={
            "concurrency": 2,
            "open_browser": False,
            "institution_keywords": [" "],
            "role_keywords": [],
        },
    )
    assert empty_response.status_code == 200
    assert empty_response.json()["institution_keywords"] == []
    assert empty_response.json()["role_keywords"] == []


def test_settings_api_rolls_back_all_preferences_on_mid_save_failure(
    client, repository: Repository
) -> None:
    repository.update_settings(Settings(concurrency=1, open_browser=False))
    repository.replace_keywords("institution", ["Old Institution"])
    repository.replace_keywords("role", ["Old Role"])
    with connect(repository.db_path) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER fail_role_keyword_insert
            BEFORE INSERT ON filter_keywords
            WHEN NEW.kind = 'role'
            BEGIN
                SELECT RAISE(ABORT, 'forced role failure');
            END
            """
        )

    response = client.put(
        "/api/settings",
        json={
            "concurrency": 4,
            "open_browser": True,
            "institution_keywords": ["New Institution"],
            "role_keywords": ["New Role"],
        },
    )

    assert response.status_code == 503
    assert repository.get_settings() == Settings(concurrency=1, open_browser=False)
    assert repository.get_keywords("institution") == ("Old Institution",)
    assert repository.get_keywords("role") == ("Old Role",)


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


def test_retry_accepts_failed_category_with_slash_in_its_name(client, app_parts) -> None:
    _, _, managers = app_parts
    managers[0]._errors = {"인턴/계약직": "timeout"}

    response = client.post("/api/crawl/retry/인턴/계약직")

    assert response.status_code == 200
    assert managers[0].starts[-1] == ("retry", ("인턴/계약직",))


def test_crawl_status_exposes_run_level_failure(client, app_parts) -> None:
    _, _, managers = app_parts
    managers[0].snapshot = lambda: SimpleNamespace(
        running=False,
        completed_categories=1,
        total_categories=1,
        new_count=0,
        category_errors={},
        run_error="history finish failed",
    )

    response = client.get("/api/crawl/status")

    assert response.status_code == 200
    assert response.json()["run_error"] == "history finish failed"


@pytest.mark.parametrize("error", [sqlite3.OperationalError("locked"), RuntimeError("closed")])
def test_home_maps_repository_errors_to_service_unavailable(client, monkeypatch, error) -> None:
    def raise_error():
        raise error

    monkeypatch.setattr(client.app.state.repository, "list_visible_posts", raise_error)

    response = client.get("/")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is temporarily unavailable"}


def test_api_database_exception_is_logged_before_returning_503(
    client, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    def raise_error():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(client.app.state.repository, "list_visible_posts", raise_error)

    with caplog.at_level(logging.ERROR, logger="src.web"):
        response = client.get("/api/posts")

    assert response.status_code == 503
    assert "database is locked" in caplog.text


def test_home_escapes_korean_title_with_quotes(client, repository: Repository) -> None:
    title = '[한국교육학술정보원] "안전한" 제목 (전산)'
    repository.upsert_crawled_posts(
        [
            CrawledPost(
                category="중앙공기업",
                title=title,
                deadline_raw="2099.08.10",
                link="https://example.test/quoted",
            )
        ]
    )

    html = client.get("/").text

    assert title not in html
    assert "한국교육학술정보원" in html
    assert "&#34;안전한&#34;" in html
