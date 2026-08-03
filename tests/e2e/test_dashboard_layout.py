from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from src.domain import CrawlSnapshot
from src.web import create_app


LONG_ROLE = (
    "정보보안 및 클라우드 기반 시스템 운영과 데이터 플랫폼 고도화 및 "
    "서비스 아키텍처 설계 및 운영 자동화 전환 프로젝트 관리 및 "
    "대규모 공공 데이터 분석 플랫폼 품질 관리와 재해 복구 체계 수립"
)


class BlockedFakeManager:
    """No-network manager that keeps the startup crawl visibly in progress."""

    def __init__(self) -> None:
        self.running = False

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        if self.running:
            return False
        self.running = True
        return True

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot(
            running=self.running,
            completed_categories=1,
            total_categories=4,
            new_count=2,
        )

    async def wait(self) -> None:
        return None


class LiveServer:
    def __init__(self, db_path: Path, json_path: Path) -> None:
        self.db_path = db_path
        self.json_path = json_path
        self.base_url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        port = _free_port()
        app = create_app(
            db_path=self.db_path,
            json_path=self.json_path,
            manager_factory=lambda _repository: BlockedFakeManager(),
        )

        @app.middleware("http")
        async def delay_settings_read(request, call_next):
            if request.method == "GET" and request.url.path == "/api/settings":
                await asyncio.sleep(0.25)
            return await call_next(request)

        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                log_level="warning",
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{port}"
        _wait_for_health(f"{self.base_url}/health", self._thread)
        return self.base_url

    def stop(self) -> None:
        if self._server is None or self._thread is None:
            return
        self._server.should_exit = True
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError("E2E Uvicorn server did not stop")
        self._server = None
        self._thread = None

    def restart(self) -> str:
        self.stop()
        return self.start()


@pytest.fixture(scope="module")
def browser() -> Browser:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser) -> Page:
    page = browser.new_page()
    yield page
    page.close()


@pytest.fixture
def live_server(tmp_path: Path) -> LiveServer:
    json_path = tmp_path / "job_posts.json"
    json_path.write_text(json.dumps(_legacy_posts(), ensure_ascii=False), encoding="utf-8")
    server = LiveServer(tmp_path / "gonggonggo.db", json_path)
    try:
        server.start()
        yield server
    finally:
        server.stop()


def test_wide_and_narrow_status_layouts(
    page: Page, live_server: LiveServer, tmp_path: Path
) -> None:
    """Catches the QHD view collapsing or the narrow view showing stacked panels."""
    page.set_viewport_size({"width": 2560, "height": 1440})
    page.goto(live_server.base_url)
    expect(page.locator("[data-lane]:visible")).to_have_count(2)
    expect(page.locator(".dashboard-shell")).to_have_css("max-width", "1600px")

    long_role = page.get_by_title(LONG_ROLE).first
    expect(long_role).to_be_visible()
    role_metrics = long_role.evaluate(
        """element => ({
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
        })"""
    )
    assert role_metrics["scrollWidth"] > role_metrics["clientWidth"], role_metrics
    expect(
        long_role.locator(
            "xpath=ancestor::article[1]//div[contains(@class, 'card-actions')]"
        )
    ).to_be_visible()
    page.screenshot(
        path=_artifact_path(tmp_path, "dashboard-wide-2560x1440.png"),
        full_page=True,
    )

    page.get_by_role("button", name="보관함", exact=True).click()
    expect(
        page.get_by_role(
            "link", name="[한국교육학술정보원 채용] 형식이 다른 원본 공고명", exact=True
        )
    ).to_be_visible()
    page.screenshot(
        path=_artifact_path(tmp_path, "dashboard-wide-archive.png"), full_page=True
    )
    page.get_by_role("button", name="진행 중", exact=True).click()

    page.set_viewport_size({"width": 1280, "height": 1440})
    expect(page.locator("[data-status-panel]:visible")).to_have_count(1)
    expect(page.locator("[data-lane]:visible")).to_have_count(0)
    page.screenshot(
        path=_artifact_path(tmp_path, "dashboard-narrow-1280x1440.png"),
        full_page=True,
    )

    page.get_by_role("button", name="설정", exact=True).click()
    expect(page.locator("#settings-drawer")).to_be_visible()
    expect(page.get_by_text("대상 기관 키워드", exact=True)).to_be_visible()
    page.screenshot(
        path=_artifact_path(tmp_path, "dashboard-settings.png"), full_page=True
    )


def test_status_transition_and_exclusion_undo_during_blocked_crawl(
    page: Page, live_server: LiveServer
) -> None:
    """Catches user status writes being blocked or lost while a crawl is running."""
    page.set_viewport_size({"width": 2560, "height": 1440})
    page.goto(live_server.base_url)
    expect(page.locator("#crawl-status")).to_contain_text("수집 중: 1/4")
    expect(page.locator("#crawl-button")).to_be_disabled()

    review_lane = page.locator('[data-lane="review_pending"]:visible')
    planned_lane = page.locator('[data-lane="planned"]:visible')
    post = review_lane.locator("article").filter(
        has=page.get_by_role("link", name="일반연구원", exact=True)
    )
    post.get_by_role("button", name="지원 예정", exact=True).click()
    expect(
        planned_lane.get_by_role("link", name="일반연구원", exact=True)
    ).to_be_visible()

    moved = planned_lane.locator("article").filter(
        has=page.get_by_role("link", name="일반연구원", exact=True)
    )
    moved.get_by_role("button", name="제외", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("공고를 제외했습니다")
    page.locator("#toast").get_by_role("button", name="실행 취소", exact=True).click()
    expect(
        planned_lane.get_by_role("link", name="일반연구원", exact=True)
    ).to_be_visible()
    expect(page.locator("#crawl-status")).to_contain_text("수집 중: 1/4")


def test_keyword_changes_apply_immediately_and_persist_after_restart(
    browser: Browser, live_server: LiveServer
) -> None:
    """Catches settings that require a recrawl or disappear after app restart."""
    page = browser.new_page(viewport={"width": 2560, "height": 1440})
    page.goto(live_server.base_url)
    general_card = page.locator('[data-lane="review_pending"]:visible article').filter(
        has=page.get_by_role("link", name="일반연구원", exact=True)
    )
    expect(general_card.get_by_text("전산", exact=True)).to_be_visible()
    expect(general_card.get_by_text("기획", exact=True)).to_have_count(0)

    page.get_by_role("button", name="설정", exact=True).click()
    expect(page.locator("#settings-drawer")).to_be_visible()
    page.locator("#role-keyword-input").fill("기획")
    expect(page.locator("#role-keyword-input")).to_have_value("기획")
    page.locator("#role-keyword-input").press("Enter")
    expect(page.locator("#role-keyword-input")).to_have_value("")
    expect(page.locator('input[name="concurrency"][value="2"]')).to_be_checked()
    expect(
        page.locator("#role-keywords .keyword-chip").filter(has_text="기획")
    ).to_have_count(1)
    page.locator('input[name="concurrency"][value="4"]').check()
    page.locator("#open-browser").check()
    page.locator("#settings-form").get_by_role("button", name="설정 저장").click()
    expect(page.locator("#toast")).to_contain_text("설정을 저장했습니다")
    expect(general_card.get_by_text("기획", exact=True)).to_be_visible()
    page.close()

    live_server.restart()
    restarted_page = browser.new_page(viewport={"width": 2560, "height": 1440})
    try:
        restarted_page.goto(live_server.base_url)
        restarted_page.get_by_role("button", name="설정", exact=True).click()
        expect(
            restarted_page.locator('input[name="concurrency"][value="4"]')
        ).to_be_checked()
        expect(restarted_page.locator("#open-browser")).to_be_checked()
        expect(
            restarted_page.locator("#role-keywords .keyword-chip").filter(has_text="기획")
        ).to_have_count(1)
        restarted_card = restarted_page.locator(
            '[data-lane="review_pending"]:visible article'
        ).filter(has=restarted_page.get_by_role("link", name="일반연구원", exact=True))
        expect(restarted_card.get_by_text("기획", exact=True)).to_be_visible()
    finally:
        restarted_page.close()


def _legacy_posts() -> list[dict[str, str]]:
    return [
        {
            "category": "대학/기타기관",
            "title": "[일반연구원 채용] 정규직 신입 (기획/전산)",
            "deadline": "2026.08.20",
            "link": "https://example.test/jobs/general",
            "state": "대기",
        },
        {
            "category": "중앙공기업",
            "title": f"[한국교육학술정보원 채용] 계약직 경력 ({LONG_ROLE})",
            "deadline": "2026.08.24",
            "link": "https://example.test/jobs/long-role",
            "state": "대기",
        },
        {
            "category": "중앙공기업",
            "title": "[한국교육학술정보원 채용] 형식이 다른 원본 공고명",
            "deadline": "2026.08.30",
            "link": "https://example.test/jobs/fallback",
            "state": "완료",
        },
    ]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_health(url: str, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("E2E Uvicorn server exited before becoming healthy")
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.05)
    raise RuntimeError(f"E2E Uvicorn server did not become healthy: {url}")


def _artifact_path(tmp_path: Path, filename: str) -> str:
    configured = os.environ.get("GGG_E2E_ARTIFACT_DIR")
    directory = Path(configured) if configured else tmp_path / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / filename)
