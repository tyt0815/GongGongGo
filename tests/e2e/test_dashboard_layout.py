from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, Response, expect, sync_playwright

from src.domain import CrawledPost, CrawlSnapshot, PostStatus
from src.news.domain import CrawledNewsItem, NewsCrawlSnapshot, NewsItemType
from src.news.repository import NewsRepository
from src.repository import Repository
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


class IdleNewsManager:
    def start(self, trigger: str) -> bool:
        return True

    def snapshot(self) -> NewsCrawlSnapshot:
        return NewsCrawlSnapshot()

    async def wait(self) -> None:
        return None


class IdleJobManager:
    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        return True

    def snapshot(self) -> CrawlSnapshot:
        return CrawlSnapshot()

    async def wait(self) -> None:
        return None


class BlockedNewsManager:
    def __init__(self) -> None:
        self.running = False

    def start(self, trigger: str) -> bool:
        if self.running:
            return False
        self.running = True
        return True

    def snapshot(self) -> NewsCrawlSnapshot:
        return NewsCrawlSnapshot(
            running=self.running,
            completed_sources=2,
            total_sources=5,
            new_count=3,
        )

    async def wait(self) -> None:
        return None


class CompletingNewsManager:
    """Manual-only news run that completes on the second status read."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.running = False
        self.status_reads = 0
        self.terminal_snapshot = NewsCrawlSnapshot()

    def start(self, trigger: str) -> bool:
        if trigger == "startup":
            return True
        if self.running:
            return False
        self.running = True
        self.status_reads = 0
        return True

    def snapshot(self) -> NewsCrawlSnapshot:
        if not self.running:
            return self.terminal_snapshot
        self.status_reads += 1
        if self.status_reads == 1:
            return NewsCrawlSnapshot(
                running=True,
                completed_sources=1,
                total_sources=5,
            )

        now = datetime.now(ZoneInfo("Asia/Seoul"))
        NewsRepository(self.db_path).upsert_items(
            [
                CrawledNewsItem(
                    item_type=NewsItemType.NEWSPAPER,
                    source="hankyung",
                    source_name="한국경제",
                    category="IT",
                    source_category="IT·과학",
                    title="수동 수집 신규 기사",
                    url="https://example.test/news/manual-result",
                    published_at=now,
                )
            ],
            now=now,
        )
        self.running = False
        self.terminal_snapshot = NewsCrawlSnapshot(
            completed_sources=4,
            total_sources=5,
            new_count=1,
            source_errors={"mk": "목록 응답 지연"},
        )
        return self.terminal_snapshot

    async def wait(self) -> None:
        return None


class RetryFakeManager:
    """No-network manager that completes one accepted slash-category retry."""

    def __init__(self) -> None:
        self.running = False
        self.retry_status_reads = 0
        self.category_errors = {"인턴/계약직": "timeout"}
        self.starts: list[tuple[str, tuple[str, ...] | None]] = []

    def start(self, trigger: str, categories: tuple[str, ...] | None = None) -> bool:
        self.starts.append((trigger, categories))
        if trigger == "startup":
            return True
        if self.running:
            return False
        self.running = True
        self.retry_status_reads = 0
        return True

    def snapshot(self) -> CrawlSnapshot:
        if self.running:
            self.retry_status_reads += 1
            if self.retry_status_reads == 1:
                return CrawlSnapshot(running=True, total_categories=1)
            self.running = False
            self.category_errors = {}
            return CrawlSnapshot(completed_categories=1, total_categories=1)
        return CrawlSnapshot(category_errors=self.category_errors)

    async def wait(self) -> None:
        return None


class LiveServer:
    def __init__(
        self,
        db_path: Path,
        json_path: Path,
        manager_factory: Callable[[], object] | None = None,
        news_manager_factory: Callable[[], object] | None = None,
    ) -> None:
        self.db_path = db_path
        self.json_path = json_path
        self.manager_factory = manager_factory or BlockedFakeManager
        self.news_manager_factory = news_manager_factory or IdleNewsManager
        self.base_url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.delay_posts_after_read = False
        self.posts_read_started = threading.Event()
        self.release_posts_read = threading.Event()

    def start(self) -> str:
        port = _free_port()
        app = create_app(
            db_path=self.db_path,
            json_path=self.json_path,
            manager_factory=lambda _repository: self.manager_factory(),
            news_manager_factory=lambda _repository: self.news_manager_factory(),
        )

        @app.middleware("http")
        async def delay_settings_read(request, call_next):
            if request.method == "GET" and request.url.path == "/api/settings":
                await asyncio.sleep(0.25)
            response = await call_next(request)
            if (
                self.delay_posts_after_read
                and request.method == "GET"
                and request.url.path == "/api/posts"
            ):
                self.posts_read_started.set()
                await asyncio.to_thread(self.release_posts_read.wait, 5)
            return response

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
        self.release_posts_read.set()
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
        _seed_news(server.db_path)
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

    institution_card = page.locator("article").filter(
        has=page.get_by_role("link", name="한국교육학술정보원", exact=True)
    ).first
    institution_link = institution_card.get_by_role(
        "link", name="한국교육학술정보원", exact=True
    )
    expect(institution_link).to_have_class("institution-filtered")
    general_card = page.locator("article").filter(
        has=page.get_by_role("link", name="일반연구원", exact=True)
    ).first
    general_link = general_card.get_by_role("link", name="일반연구원", exact=True)
    expect(general_link).not_to_have_class("institution-filtered")
    assert institution_link.evaluate("element => getComputedStyle(element).color") != (
        general_link.evaluate("element => getComputedStyle(element).color")
    )
    expect(page.get_by_text(re.compile(r"^(기관|직무) 필터"))).to_have_count(0)

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


def test_new_posts_are_prioritized_and_clear_only_on_an_action(
    browser: Browser, tmp_path: Path
) -> None:
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    server = LiveServer(tmp_path / "gonggonggo.db", json_path)
    page = browser.new_page(viewport={"width": 2560, "height": 1440})
    try:
        server.start()
        repository = Repository(server.db_path)
        repository.replace_keywords("institution", ["기관"])
        repository.replace_keywords("role", [])
        posts = [
            CrawledPost(
                category="중앙공기업",
                title="[기존기관 채용] 정규직 신입 (전산)",
                deadline_raw="2099.08.10",
                link="https://example.test/jobs/old",
            ),
            CrawledPost(
                category="중앙공기업",
                title="[신규기관 채용] 정규직 신입 (전산)",
                deadline_raw="2099.08.11",
                link="https://example.test/jobs/new",
            ),
            CrawledPost(
                category="중앙공기업",
                title="[최신기관 채용] 정규직 신입 (전산)",
                deadline_raw="2099.08.12",
                link="https://example.test/jobs/newest",
            ),
        ]
        repository.upsert_crawled_posts([posts[0]])
        repository.acknowledge_post(posts[0].link)
        repository.upsert_crawled_posts(posts[1:])

        page.route(
            "https://example.test/**",
            lambda route: route.fulfill(content_type="text/html", body="<p>공고</p>"),
        )
        page.goto(server.base_url)
        review_lane = page.locator('[data-lane="review_pending"]:visible')
        cards = review_lane.locator("article")
        expect(cards).to_have_count(3)
        assert cards.locator("h3 a").all_inner_texts() == [
            "최신기관",
            "신규기관",
            "기존기관",
        ]
        page.locator("#sort-select").select_option("recent")
        assert cards.locator("h3 a").all_inner_texts()[:2] == ["최신기관", "신규기관"]
        expect(cards.locator(".new-badge")).to_have_count(2)
        expect(cards.nth(2).locator(".new-badge")).to_have_count(0)
        page.screenshot(
            path=_artifact_path(tmp_path, "dashboard-new-post-badges.png"),
            full_page=True,
        )

        newest = cards.filter(
            has=page.get_by_role("link", name="최신기관", exact=True)
        )
        server.delay_posts_after_read = True
        page.get_by_role("button", name="설정", exact=True).click()
        expect(page.locator("#settings-drawer")).to_be_visible()
        page.locator("#settings-form").get_by_role("button", name="설정 저장").click()
        assert server.posts_read_started.wait(3)
        repository.upsert_crawled_posts(
            [
                CrawledPost(
                    category="중앙공기업",
                    title="[뒤늦은기관 채용] 정규직 신입 (전산)",
                    deadline_raw="2099.08.13",
                    link="https://example.test/jobs/late",
                )
            ]
        )
        newest.get_by_role("button", name="확인", exact=True).click()
        expect(newest.locator(".new-badge")).to_have_count(0)
        server.release_posts_read.set()
        expect(page.locator("#toast")).to_contain_text("설정을 저장했습니다")
        expect(review_lane.get_by_role("link", name="최신기관", exact=True)).to_be_visible()
        expect(newest.locator(".new-badge")).to_have_count(0)
        expect(review_lane.get_by_role("link", name="뒤늦은기관", exact=True)).to_be_visible()

        new_card = cards.filter(
            has=page.get_by_role("link", name="신규기관", exact=True)
        )
        with page.expect_popup() as popup_info:
            new_card.get_by_role("link", name="신규기관", exact=True).click()
        popup_info.value.close()
        page.reload()
        new_card = review_lane.locator("article").filter(
            has=page.get_by_role("link", name="신규기관", exact=True)
        )
        expect(new_card.locator(".new-badge")).to_have_count(1)

        new_card.get_by_role("button", name="지원 예정", exact=True).click()
        planned = page.locator('[data-lane="planned"]:visible article').filter(
            has=page.get_by_role("link", name="신규기관", exact=True)
        )
        expect(planned).to_be_visible()
        expect(planned.locator(".new-badge")).to_have_count(0)
    finally:
        page.close()
        server.stop()


def test_planned_posts_prioritize_institution_matches_then_pins(
    browser: Browser, tmp_path: Path
) -> None:
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    server = LiveServer(tmp_path / "gonggonggo.db", json_path)
    page = browser.new_page(viewport={"width": 2560, "height": 1440})
    try:
        server.start()
        repository = Repository(server.db_path)
        repository.replace_keywords("institution", ["대상"])
        repository.replace_keywords("role", ["전산"])
        posts = [
            CrawledPost(
                category="중앙공기업",
                title=f"[{name} 채용] 정규직 신입 (전산)",
                deadline_raw=deadline,
                link=f"https://example.test/jobs/{index}",
            )
            for index, (name, deadline) in enumerate(
                (
                    ("대상빠른", "2099.08.10"),
                    ("보관하나", "2099.08.06"),
                    ("대상느린", "2099.08.12"),
                    ("보관둘", "2099.08.13"),
                    ("대상일반", "2099.08.09"),
                    ("보관셋", "2099.08.05"),
                    ("일반빠른", "2099.08.08"),
                    ("보관넷", "2099.08.15"),
                    ("일반느린", "2099.08.11"),
                    ("보관다섯", "2099.08.04"),
                    ("일반기본", "2099.08.07"),
                )
            )
        ]
        repository.upsert_crawled_posts(posts)
        for post in posts:
            status = (
                PostStatus.APPLIED
                if "보관" in post.title
                else PostStatus.PLANNED
            )
            repository.update_status(post.link, status)

        page.goto(server.base_url)
        cards = page.locator('[data-lane="planned"]:visible article')
        for name in ("대상느린", "일반느린", "대상빠른", "일반빠른"):
            card = cards.filter(
                has=page.get_by_role("link", name=name, exact=True)
            )
            card.get_by_role("button", name="상단 고정", exact=True).click()
            expect(
                card.get_by_role("button", name="상단 고정 해제", exact=True)
            ).to_be_visible()

        expected_order = [
            "대상빠른",
            "대상느린",
            "대상일반",
            "일반빠른",
            "일반느린",
            "일반기본",
        ]
        assert cards.locator("h3 a").all_inner_texts() == expected_order
        page.locator("#sort-select").select_option("recent")
        assert cards.locator("h3 a").all_inner_texts() == expected_order

        page.reload()
        expect(cards).to_have_count(6)
        assert cards.locator("h3 a").all_inner_texts() == expected_order
        expect(
            cards.get_by_role("button", name="상단 고정 해제", exact=True)
        ).to_have_count(4)
    finally:
        page.close()
        server.stop()


def test_run_level_failure_is_visible(
    page: Page, live_server: LiveServer
) -> None:
    """Catches a repository failure being rendered as a clean crawl completion."""
    page.route(
        "**/api/crawl/status",
        lambda route: route.fulfill(
            json={
                "running": False,
                "completed_categories": 1,
                "total_categories": 1,
                "new_count": 0,
                "category_errors": {},
                "run_error": "실행 이력 저장 실패",
            }
        ),
    )

    page.goto(live_server.base_url)

    expect(page.locator("#crawl-errors")).to_contain_text(
        "실행 오류: 실행 이력 저장 실패"
    )


def test_failed_slash_category_retry_uses_polling_lifecycle(
    browser: Browser, tmp_path: Path
) -> None:
    """Catches a missing retry control or an unescaped slash category request."""
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    manager = RetryFakeManager()
    server = LiveServer(
        tmp_path / "gonggonggo.db", json_path, manager_factory=lambda: manager
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        server.start()
        page.goto(server.base_url)
        retry = page.get_by_role("button", name="인턴/계약직 다시 시도", exact=True)
        expect(retry).to_be_visible()

        retry.click()

        expect(page.locator("#crawl-status")).to_contain_text("수집 중: 0/1")
        expect(page.locator("#crawl-button")).to_be_enabled(timeout=3000)
        expect(page.locator("#crawl-status")).to_contain_text("최근 수집: 1/1")
        assert ("retry", ("인턴/계약직",)) in manager.starts
    finally:
        page.close()
        server.stop()


def test_fast_terminal_manual_crawl_reenables_button_and_refreshes_once(
    page: Page, live_server: LiveServer
) -> None:
    """Catches a fast completed crawl leaving the button disabled or unrefreshed."""
    requests = {"status": 0, "posts": 0, "start": 0}

    def fulfill_status(route) -> None:
        requests["status"] += 1
        if requests["status"] == 1:
            snapshot = {
                "running": False,
                "completed_categories": 0,
                "total_categories": 0,
                "new_count": 0,
                "category_errors": {},
                "run_error": None,
            }
        else:
            snapshot = {
                "running": False,
                "completed_categories": 4,
                "total_categories": 4,
                "new_count": 1,
                "category_errors": {},
                "run_error": None,
            }
        route.fulfill(json=snapshot)

    def fulfill_posts(route) -> None:
        requests["posts"] += 1
        posts = []
        if requests["posts"] == 2:
            posts = [
                {
                    "link": "https://example.test/fast-result",
                    "category": "중앙공기업",
                    "original_title": "[완료기관 채용] 정규직 신입 (전산)",
                    "institution": "완료기관",
                    "employment": "정규직",
                    "career": "신입",
                    "display_roles": ["전산"],
                    "deadline_raw": "2026.08.10",
                    "deadline_date": "2026-08-10",
                    "deadline_kind": "dated",
                    "status": "review_pending",
                    "last_seen_at": "2026-08-03T00:00:00+00:00",
                }
            ]
        route.fulfill(json={"posts": posts})

    def fulfill_start(route) -> None:
        requests["start"] += 1
        route.fulfill(json={"started": True})

    page.route("**/api/crawl/status", fulfill_status)
    page.route("**/api/posts", fulfill_posts)
    page.route("**/api/crawl/start", fulfill_start)
    page.goto(live_server.base_url)
    expect(page.locator("#crawl-status")).to_contain_text("아직 수집 결과가 없습니다")

    page.locator("#crawl-button").click()

    expect(page.locator("#crawl-status")).to_contain_text("최근 수집: 4/4")
    expect(page.locator("#crawl-button")).to_be_enabled()
    expect(page.get_by_role("link", name="완료기관", exact=True)).to_be_visible()
    assert requests == {"status": 2, "posts": 2, "start": 1}


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


def test_news_filters_opens_and_dismisses(
    page: Page, live_server: LiveServer
) -> None:
    """Catches any no-op news filter, external link, or completed-item removal."""
    page.goto(live_server.base_url)
    page.get_by_role("button", name="뉴스/기관소식").click()

    news_list = page.locator("#news-list")
    retained_row = page.locator(".news-row", has_text="공공부문 AI 전환")

    def is_news_list_response(response: Response) -> bool:
        return response.request.method == "GET" and "/api/news?" in response.url

    expect(news_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#news-count")).to_have_text("총 1건")
    expect(page.locator(".news-row", has_text="공공 데이터 개방 확대")).to_be_visible()
    expect(retained_row).to_have_count(0)

    with page.expect_response(is_news_list_response):
        page.get_by_role("button", name="최근 7일").click()
    expect(news_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#news-count")).to_have_text("총 5건")
    expect(retained_row).to_be_visible()
    expect(page.locator(".news-row", has_text="지역경제 투자 확대")).to_be_visible()
    expect(page.locator(".news-row", has_text="한국경제 경제정책")).to_be_visible()
    expect(page.locator(".news-row", has_text="클라우드 보안 강화")).to_be_visible()

    with page.expect_response(is_news_list_response):
        page.get_by_role("button", name="뉴스", exact=True).click()
    expect(news_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#news-count")).to_have_text("총 4건")
    expect(retained_row).to_be_visible()
    expect(page.locator(".news-row", has_text="공공 데이터 개방 확대")).to_have_count(0)

    page.get_by_label("매일경제", exact=True).uncheck()
    expect(page.locator("#news-count")).to_have_text("총 3건")
    expect(retained_row).to_be_visible()
    expect(page.locator(".news-row", has_text="지역경제 투자 확대")).to_have_count(0)

    page.get_by_label("경제", exact=True).uncheck()
    expect(page.locator("#news-count")).to_have_text("총 2건")
    expect(retained_row).to_be_visible()
    expect(page.locator(".news-row", has_text="한국경제 경제정책")).to_have_count(0)

    with page.expect_response(is_news_list_response):
        page.get_by_label("제목 검색").fill("AI")
    expect(news_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#news-count")).to_have_text("총 1건")
    expect(retained_row).to_be_visible()
    expect(page.locator(".news-row", has_text="클라우드 보안 강화")).to_have_count(0)

    row = retained_row
    expect(row).to_be_visible()
    expect(row.locator("a.news-title")).to_have_attribute("target", "_blank")
    expect(row.get_by_role("link", name="원문 보기")).to_have_count(0)
    row.get_by_role("button", name="처리 완료").click()
    expect(row).to_have_count(0)


def test_news_checkbox_filters_persist_but_type_defaults_to_all(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(live_server.base_url)
    page.get_by_role("button", name="뉴스/기관소식").click()
    expect(page.locator("#news-list")).to_have_attribute("aria-busy", "false")
    filter_rows = [
        page.locator('.news-filter-row[aria-label="자료 종류"]'),
        page.locator('.news-filter-row[aria-label="기간"]'),
        page.locator('.news-filter-row[aria-label="출처"]'),
        page.locator('.news-filter-row[aria-label="분류"]'),
    ]
    row_tops = [locator.bounding_box()["y"] for locator in filter_rows]
    assert row_tops == sorted(row_tops) and len(set(row_tops)) == 4

    page.get_by_role("button", name="뉴스", exact=True).click()
    page.get_by_label("매일경제", exact=True).uncheck()
    page.get_by_label("경제", exact=True).uncheck()
    page.reload()
    page.get_by_role("button", name="뉴스/기관소식").click()

    expect(page.locator('[data-news-type=""]')).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_label("매일경제", exact=True)).not_to_be_checked()
    expect(page.get_by_label("경제", exact=True)).not_to_be_checked()
    expect(page.get_by_label("한국경제", exact=True)).to_be_checked()
    expect(page.get_by_label("IT", exact=True)).to_be_checked()


def test_initial_news_load_reads_status_before_list(
    page: Page, live_server: LiveServer
) -> None:
    """Catches a startup commit landing between a stale list read and status read."""
    request_order: list[str] = []

    def fulfill_status(route) -> None:
        request_order.append("status")
        route.fulfill(
            json={
                "running": False,
                "completed_sources": 5,
                "total_sources": 5,
                "new_count": 1,
                "duplicate_count": 0,
                "expired_count": 0,
                "source_errors": {},
                "run_error": None,
            }
        )

    def fulfill_list(route) -> None:
        request_order.append("list")
        items = []
        if "status" in request_order:
            items = [
                {
                    "id": 901,
                    "item_type": "newspaper",
                    "source": "hankyung",
                    "source_name": "한국경제",
                    "category": "IT",
                    "source_category": "IT·과학",
                    "title": "시작 수집 반영 기사",
                    "url": "https://www.hankyung.com/article/901",
                    "published_at": "2026-08-25T12:00:00+09:00",
                    "discovered_at": "2026-08-25T12:01:00+09:00",
                    "used_discovered_date": False,
                }
            ]
        route.fulfill(json={"items": items})

    page.route("**/api/news/crawl/status", fulfill_status)
    page.route("**/api/news?*", fulfill_list)
    page.goto(live_server.base_url)

    with page.expect_response(
        lambda response: response.request.method == "GET" and "/api/news?" in response.url
    ):
        page.get_by_role("button", name="뉴스/기관소식").click()

    expect(page.locator("#news-list")).to_have_attribute("aria-busy", "false")
    assert request_order[:2] == ["status", "list"]
    expect(page.locator(".news-row", has_text="시작 수집 반영 기사")).to_be_visible()


def test_news_mobile_row_stays_in_viewport_and_job_panel_is_restored(
    page: Page, live_server: LiveServer
) -> None:
    """Catches mobile horizontal overflow or job status loss across primary tabs."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server.base_url)
    page.locator(".status-tabs").get_by_role(
        "button", name="지원 예정", exact=True
    ).click()
    expect(page.locator('[data-status-panel="planned"]:visible')).to_have_count(1)

    page.get_by_role("button", name="뉴스/기관소식").click()
    page.get_by_role("button", name="최근 7일").click()
    row = page.locator(".news-row", has_text="공공부문 AI 전환")
    expect(row).to_be_visible()
    metrics = row.evaluate(
        """element => {
            const bounds = element.getBoundingClientRect();
            return {
                left: bounds.left,
                right: bounds.right,
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
            };
        }"""
    )
    assert metrics["left"] >= 0, metrics
    assert metrics["right"] <= metrics["viewportWidth"], metrics
    assert metrics["documentWidth"] <= metrics["viewportWidth"], metrics

    page.get_by_role("button", name="채용공고").click()
    expect(page.locator('[data-status-panel="planned"]:visible')).to_have_count(1)


def test_blocked_news_crawl_keeps_job_crawl_available(
    browser: Browser, tmp_path: Path
) -> None:
    """Catches news crawl state leaking into the independent job crawl control."""
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    server = LiveServer(
        tmp_path / "gonggonggo.db",
        json_path,
        manager_factory=IdleJobManager,
        news_manager_factory=BlockedNewsManager,
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        server.start()
        page.goto(server.base_url)
        expect(page.locator("#crawl-button")).to_be_enabled()

        page.get_by_role("button", name="뉴스/기관소식").click()
        expect(page.locator("#news-crawl-status")).to_contain_text("수집 2/5")
        expect(page.locator("#news-crawl-button")).to_be_disabled()

        page.get_by_role("button", name="채용공고").click()
        expect(page.locator("#crawl-button")).to_be_enabled()
    finally:
        page.close()
        server.stop()


def test_manual_news_crawl_polls_partial_failure_and_refreshes_results(
    browser: Browser, tmp_path: Path
) -> None:
    """Catches a missing news manual-crawl running/terminal refresh lifecycle."""
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    db_path = tmp_path / "gonggonggo.db"
    news_manager = CompletingNewsManager(db_path)
    server = LiveServer(
        db_path,
        json_path,
        manager_factory=IdleJobManager,
        news_manager_factory=lambda: news_manager,
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        server.start()
        page.goto(server.base_url)
        page.get_by_role("button", name="뉴스/기관소식").click()
        refreshed = page.locator(".news-row", has_text="수동 수집 신규 기사")
        expect(refreshed).to_have_count(0)

        news_button = page.locator("#news-crawl-button")
        expect(news_button).to_be_enabled()
        news_button.click()

        expect(news_button).to_be_disabled()
        expect(page.locator("#news-crawl-status")).to_contain_text("수집 1/5")
        expect(news_button).to_be_enabled(timeout=4000)
        expect(page.locator("#news-crawl-status")).to_contain_text("수집 4/5")
        expect(page.locator("#news-crawl-errors")).to_contain_text(
            "mk: 목록 응답 지연"
        )
        expect(refreshed).to_be_visible()
    finally:
        page.close()
        server.stop()


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


def _seed_news(db_path: Path) -> None:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    NewsRepository(db_path).upsert_items(
        [
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="hankyung",
                source_name="한국경제",
                category="IT",
                source_category="IT·과학",
                title="공공부문 AI 전환 가속",
                url="https://example.test/news/public-ai",
                published_at=now - timedelta(days=1),
            ),
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="mk",
                source_name="매일경제",
                category="경제",
                source_category="경제",
                title="지역경제 투자 확대",
                url="https://example.test/news/local-economy",
                published_at=now - timedelta(days=2),
            ),
            CrawledNewsItem(
                item_type=NewsItemType.INSTITUTION,
                source="reb",
                source_name="한국부동산원",
                category="보도자료",
                source_category=None,
                title="공공 데이터 개방 확대",
                url="https://example.test/news/public-data",
                published_at=now,
            ),
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="hankyung",
                source_name="한국경제",
                category="경제",
                source_category="경제",
                title="한국경제 경제정책",
                url="https://example.test/news/hankyung-economy",
                published_at=now - timedelta(days=2),
            ),
            CrawledNewsItem(
                item_type=NewsItemType.NEWSPAPER,
                source="hankyung",
                source_name="한국경제",
                category="IT",
                source_category="IT·과학",
                title="클라우드 보안 강화",
                url="https://example.test/news/cloud-security",
                published_at=now - timedelta(days=1),
            ),
        ],
        now=now,
    )


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
