import logging
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src.domain import CrawlSnapshot
from src.news.domain import CrawledNewsItem, NewsCrawlSnapshot, NewsItemType
from src.news.repository import NewsRepository
from src.repository import Repository
from src.web import create_app


SEOUL = ZoneInfo("Asia/Seoul")


class FakeJobManager:
    def __init__(self, repository: Repository) -> None:
        self.starts: list[tuple[str, tuple[str, ...] | None]] = []
        self.finished = False
        self.manual_allowed = True

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        self.starts.append((trigger, categories))
        return trigger == "startup" or self.manual_allowed

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot()

    async def wait(self) -> None:
        self.finished = True


class FakeNewsManager:
    def __init__(self, repository: NewsRepository) -> None:
        self.starts: list[str] = []
        self.finished = False
        self.manual_allowed = True
        self._snapshot = NewsCrawlSnapshot()

    def start(self, trigger: str) -> bool:
        self.starts.append(trigger)
        return trigger == "startup" or self.manual_allowed

    def snapshot(self) -> NewsCrawlSnapshot:
        return self._snapshot

    async def wait(self) -> None:
        self.finished = True


@pytest.fixture
def app_parts(tmp_path: Path):
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    managers = SimpleNamespace(job=None, news=None)

    def job_factory(repository: Repository) -> FakeJobManager:
        managers.job = FakeJobManager(repository)
        return managers.job

    def news_factory(repository: NewsRepository) -> FakeNewsManager:
        managers.news = FakeNewsManager(repository)
        return managers.news

    return (
        create_app(
            db_path=db_path,
            json_path=json_path,
            manager_factory=job_factory,
            news_manager_factory=news_factory,
        ),
        db_path,
        managers,
    )


@pytest.fixture
def client(app_parts):
    app, _, _ = app_parts
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def managers(app_parts, client):
    _, _, value = app_parts
    return value


@pytest.fixture
def seeded_news(app_parts, client) -> CrawledNewsItem:
    _, db_path, _ = app_parts
    item = CrawledNewsItem(
        item_type=NewsItemType.NEWSPAPER,
        source="hankyung",
        source_name="한국경제",
        category="IT",
        source_category="IT",
        title="AI news",
        url="https://example.test/news/ai",
        published_at=datetime.now(SEOUL),
    )
    NewsRepository(db_path).upsert_items([item])
    return item


def test_both_startup_managers_begin_before_health(client, managers) -> None:
    assert client.get("/health").status_code == 200
    assert managers.job.starts == [("startup", None)]
    assert managers.news.starts == ["startup"]
    assert managers.job.finished is False
    assert managers.news.finished is False


def test_news_query_serializes_records_and_dismissal(client, seeded_news) -> None:
    response = client.get(
        "/api/news",
        params={
            "period": "7d",
            "item_type": "newspaper",
            "source": "hankyung",
            "category": "IT",
            "q": "AI",
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["item_type"] == "newspaper"
    assert item["source"] == "hankyung"
    assert item["published_at"].endswith("+09:00")
    assert item["discovered_at"].endswith("+09:00")
    assert item["used_discovered_date"] is False
    item_id = item["id"]
    assert client.delete(f"/api/news/{item_id}").json() == {"dismissed": True}
    assert client.delete(f"/api/news/{item_id}").status_code == 404


def test_news_uses_discovered_date_when_published_at_is_missing(client, app_parts) -> None:
    _, db_path, _ = app_parts
    NewsRepository(db_path).upsert_items(
        [
            CrawledNewsItem(
                item_type=NewsItemType.INSTITUTION,
                source="reb",
                source_name="한국부동산원",
                category="보도자료",
                source_category=None,
                title="Notice",
                url="https://example.test/news/notice",
                published_at=None,
            )
        ]
    )

    item = client.get("/api/news").json()["items"][0]

    assert item["published_at"] is None
    assert item["used_discovered_date"] is True
    assert item["discovered_at"].endswith("+09:00")


def test_news_response_serializes_every_field_in_effective_date_order(client, app_parts) -> None:
    _, db_path, _ = app_parts
    discovered_at = datetime.combine(datetime.now(SEOUL).date(), time(14), tzinfo=SEOUL)
    published_at = discovered_at - timedelta(days=1, hours=5)
    NewsRepository(db_path).upsert_items(
        [
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="hankyung",
                source_name="한국경제",
                category="IT",
                source_category="IT·과학",
                title="Older AI article",
                url="https://example.test/news/older",
                published_at=published_at,
            ),
            CrawledNewsItem(
                item_type=NewsItemType.INSTITUTION,
                source="reb",
                source_name="한국부동산원",
                category="보도자료",
                source_category=None,
                title="Newest institution release",
                url="https://example.test/news/newest",
                published_at=None,
            ),
        ],
        now=discovered_at,
    )

    assert client.get("/api/news?period=7d").json() == {
        "items": [
            {
                "id": 2,
                "item_type": "institution",
                "source": "reb",
                "source_name": "한국부동산원",
                "category": "보도자료",
                "source_category": None,
                "title": "Newest institution release",
                "url": "https://example.test/news/newest",
                "published_at": None,
                "discovered_at": discovered_at.isoformat(),
                "used_discovered_date": True,
            },
            {
                "id": 1,
                "item_type": "newspaper",
                "source": "hankyung",
                "source_name": "한국경제",
                "category": "IT",
                "source_category": "IT·과학",
                "title": "Older AI article",
                "url": "https://example.test/news/older",
                "published_at": published_at.isoformat(),
                "discovered_at": discovered_at.isoformat(),
                "used_discovered_date": False,
            },
        ]
    }


def test_news_validation(client) -> None:
    assert client.get("/api/news?period=90d").status_code == 422
    assert client.get("/api/news?item_type=other").status_code == 422
    assert client.get("/api/news?source=unknown").status_code == 422
    assert client.get("/api/news?category=보도자료").status_code == 200
    assert client.get("/api/news?category=정기 통계").status_code == 200
    assert client.get("/api/news?category=연예").status_code == 422
    assert client.get("/api/news", params={"q": "x" * 201}).status_code == 422


def test_news_crawl_status_and_manual_start_are_independent(client, managers) -> None:
    managers.job.manual_allowed = False

    assert client.post("/api/crawl/start").status_code == 409
    assert client.post("/api/news/crawl/start").json() == {"started": True}
    assert client.get("/api/news/crawl/status").json() == {
        "running": False,
        "completed_sources": 0,
        "total_sources": 0,
        "new_count": 0,
        "duplicate_count": 0,
        "expired_count": 0,
        "source_errors": {},
        "run_error": None,
    }

    managers.news.manual_allowed = False
    assert client.post("/api/news/crawl/start").status_code == 409


def test_news_database_errors_are_logged_and_returned_as_503(client, caplog) -> None:
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("locked")

    client.app.state.news_repository.list_items = unavailable

    with caplog.at_level(logging.ERROR, logger="src.web"):
        response = client.get("/api/news")

    assert response.status_code == 503
    assert "Database operation failed" in caplog.text


def test_shutdown_waits_for_both_managers_when_one_wait_raises(tmp_path: Path, caplog) -> None:
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    waits: list[str] = []

    class FailingJobManager(FakeJobManager):
        async def wait(self) -> None:
            waits.append("job")
            raise RuntimeError("job wait failure")

    class RecordingNewsManager(FakeNewsManager):
        async def wait(self) -> None:
            waits.append("news")

    with caplog.at_level(logging.ERROR, logger="src.web"):
        with TestClient(
            create_app(
                db_path,
                json_path,
                lambda repository: FailingJobManager(repository),
                news_manager_factory=lambda repository: RecordingNewsManager(repository),
            )
        ):
            pass

    assert waits == ["job", "news"]
    assert "job wait failure" in caplog.text
