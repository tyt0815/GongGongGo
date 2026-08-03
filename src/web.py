import logging
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from .config import DB_PATH, JSON_PATH, PROJECT_ROOT
from .crawl_manager import CrawlManager
from .database import initialize_database
from .domain import CrawlSnapshot, PostStatus, Settings
from .repository import Repository


class CrawlCoordinator(Protocol):
    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool: ...

    def snapshot(self) -> CrawlSnapshot: ...

    async def wait(self) -> None: ...


ManagerFactory = Callable[[Repository], CrawlCoordinator]
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
logger = logging.getLogger(__name__)


class LinkRequest(BaseModel):
    link: str = Field(min_length=1)

    @field_validator("link")
    @classmethod
    def link_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("link must not be blank")
        return value


class StatusRequest(LinkRequest):
    status: PostStatus


class SettingsRequest(Settings):
    institution_keywords: list[str]
    role_keywords: list[str]


def create_app(
    db_path: Path = DB_PATH,
    json_path: Path = JSON_PATH,
    manager_factory: ManagerFactory | None = None,
    crawler_headless: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_database(db_path, json_path)
        repository = Repository(db_path)
        manager = (
            manager_factory(repository)
            if manager_factory is not None
            else CrawlManager(repository, headless=crawler_headless)
        )
        app.state.repository = repository
        app.state.manager = manager
        manager.start("startup")
        try:
            yield
        finally:
            await manager.wait()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        try:
            posts = _repository(request).list_visible_posts()
        except (sqlite3.Error, RuntimeError) as exc:
            raise _database_unavailable(exc) from exc
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"posts": posts},
        )

    @app.get("/api/posts")
    def list_posts(request: Request) -> dict[str, object]:
        try:
            return {"posts": _json_posts(_repository(request).list_visible_posts())}
        except (sqlite3.Error, RuntimeError) as exc:
            raise _database_unavailable(exc) from exc

    @app.post("/api/posts/status")
    def update_post_status(request: Request, data: StatusRequest) -> dict[str, str]:
        repository = _repository(request)
        try:
            if not repository.update_status(data.link, data.status):
                raise HTTPException(status_code=404, detail="Post not found")
            return {"link": data.link, "status": data.status.value}
        except sqlite3.Error as exc:
            raise _database_unavailable(exc) from exc

    @app.post("/api/posts/delete")
    def delete_post(request: Request, data: LinkRequest) -> dict[str, bool]:
        repository = _repository(request)
        try:
            if repository.get_post(data.link) is None:
                raise HTTPException(status_code=404, detail="Post not found")
            if not repository.delete_permanently(data.link):
                raise HTTPException(status_code=404, detail="Post not found")
            return {"deleted": True}
        except sqlite3.Error as exc:
            raise _database_unavailable(exc) from exc

    @app.post("/api/crawl/start")
    async def start_crawl(request: Request) -> dict[str, bool]:
        if not _manager(request).start("manual"):
            raise HTTPException(status_code=409, detail="A crawl is already running")
        return {"started": True}

    @app.post("/api/crawl/retry/{category:path}")
    async def retry_failed_category(request: Request, category: str) -> dict[str, object]:
        if category not in _manager(request).snapshot().category_errors:
            raise HTTPException(status_code=422, detail="Category is not eligible for retry")
        if not _manager(request).start("retry", (category,)):
            raise HTTPException(status_code=409, detail="A crawl is already running")
        return {"started": True, "category": category}

    @app.get("/api/crawl/status")
    def crawl_status(request: Request) -> dict[str, object]:
        snapshot = _manager(request).snapshot()
        return {
            "running": snapshot.running,
            "completed_categories": snapshot.completed_categories,
            "total_categories": snapshot.total_categories,
            "new_count": snapshot.new_count,
            "category_errors": dict(snapshot.category_errors),
            "run_error": snapshot.run_error,
        }

    @app.get("/api/settings")
    def get_settings(request: Request) -> dict[str, object]:
        try:
            return _settings_response(_repository(request))
        except (sqlite3.Error, RuntimeError) as exc:
            raise _database_unavailable(exc) from exc

    @app.put("/api/settings")
    def update_settings(request: Request, data: SettingsRequest) -> dict[str, object]:
        repository = _repository(request)
        try:
            repository.update_preferences(
                Settings(concurrency=data.concurrency, open_browser=data.open_browser),
                data.institution_keywords,
                data.role_keywords,
            )
            return _settings_response(repository)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (sqlite3.Error, RuntimeError) as exc:
            raise _database_unavailable(exc) from exc

    @app.get("/api/deleted-links")
    def list_deleted_links(request: Request) -> dict[str, object]:
        try:
            return {
                "deleted_links": [
                    {"id": record.id, "link": record.link}
                    for record in _repository(request).list_deleted_links()
                ]
            }
        except sqlite3.Error as exc:
            raise _database_unavailable(exc) from exc

    @app.delete("/api/deleted-links/{id}")
    def unblock_deleted_link(request: Request, id: int) -> dict[str, bool]:
        try:
            if not _repository(request).unblock_link(id):
                raise HTTPException(status_code=404, detail="Deleted link not found")
            return {"unblocked": True}
        except sqlite3.Error as exc:
            raise _database_unavailable(exc) from exc

    return app


def _repository(request: Request) -> Repository:
    return request.app.state.repository


def _manager(request: Request) -> CrawlCoordinator:
    return request.app.state.manager


def _database_unavailable(exc: Exception) -> HTTPException:
    logger.error(
        "Database operation failed",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return HTTPException(status_code=503, detail="Database is temporarily unavailable")


def _settings_response(repository: Repository) -> dict[str, object]:
    settings = repository.get_settings()
    return {
        "concurrency": settings.concurrency,
        "open_browser": settings.open_browser,
        "institution_keywords": list(repository.get_keywords("institution")),
        "role_keywords": list(repository.get_keywords("role")),
    }


def _json_posts(posts: list[dict[str, object]]) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for post in posts:
        serialized.append(
            {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in post.items()
            }
        )
    return serialized
