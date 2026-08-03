from pathlib import Path
from unittest.mock import Mock

import pytest

from src import runtime


def test_browser_opens_only_after_health_succeeds():
    health_check = Mock(side_effect=[ConnectionError(), 200])
    opener = Mock()

    runtime.open_browser_when_healthy(
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/health",
        opener=opener,
        health_check=health_check,
    )

    assert health_check.call_count == 2
    opener.assert_called_once_with("http://127.0.0.1:8000")


def test_run_does_not_create_opener_thread_when_persisted_setting_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db_path = tmp_path / "gonggonggo.db"
    json_path = tmp_path / "job_posts.json"
    json_path.write_text("[]", encoding="utf-8")
    calls: list[tuple[object, str, int]] = []

    monkeypatch.setattr(runtime, "DB_PATH", db_path)
    monkeypatch.setattr(runtime, "JSON_PATH", json_path)
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime, "configure_logging", lambda log_dir: None)
    monkeypatch.setattr(
        runtime.threading,
        "Thread",
        lambda *args, **kwargs: pytest.fail("browser opener thread was created"),
    )
    monkeypatch.setattr(
        runtime.uvicorn,
        "run",
        lambda app, host, port: calls.append((app, host, port)),
    )

    app = object()
    runtime.run(app)

    assert calls == [(app, "127.0.0.1", 8000)]
