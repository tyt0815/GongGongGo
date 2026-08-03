import logging
from datetime import date
from pathlib import Path

import pytest

from src.logging_config import cleanup_old_logs, configure_logging


@pytest.fixture(autouse=True)
def close_application_log_handlers():
    yield
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if getattr(handler, "_gonggonggo_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


def test_cleanup_removes_only_logs_older_than_14_days(tmp_path: Path):
    (tmp_path / "gonggonggo-2026-07-19.log").touch()
    (tmp_path / "gonggonggo-2026-07-20.log").touch()
    (tmp_path / "other-2026-07-01.log").touch()
    (tmp_path / "gonggonggo-not-a-date.log").touch()

    cleanup_old_logs(tmp_path, today=date(2026, 8, 3), retention_days=14)

    assert not (tmp_path / "gonggonggo-2026-07-19.log").exists()
    assert (tmp_path / "gonggonggo-2026-07-20.log").exists()
    assert (tmp_path / "other-2026-07-01.log").exists()
    assert (tmp_path / "gonggonggo-not-a-date.log").exists()


def test_configure_logging_writes_utf8_korean_to_dated_file_and_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    logger = configure_logging(tmp_path, today=date(2026, 8, 3))

    logger.info("서버 준비 완료")

    for handler in logger.handlers:
        handler.flush()
    assert (tmp_path / "gonggonggo-2026-08-03.log").read_text(encoding="utf-8").find(
        "서버 준비 완료"
    ) >= 0
    assert "서버 준비 완료" in capsys.readouterr().err


def test_configure_logging_does_not_duplicate_handlers_when_called_twice(tmp_path: Path):
    first = configure_logging(tmp_path, today=date(2026, 8, 3))
    second = configure_logging(tmp_path, today=date(2026, 8, 3))
    second.info("한 번만 기록")

    assert first is second
    assert (tmp_path / "gonggonggo-2026-08-03.log").read_text(
        encoding="utf-8"
    ).count("한 번만 기록") == 1


def test_configure_logging_captures_production_module_logs(tmp_path: Path):
    logger = configure_logging(tmp_path, today=date(2026, 8, 3))

    logging.getLogger("src.crawler").error("크롤러 오류")

    for handler in logger.handlers:
        handler.flush()
    assert "크롤러 오류" in (tmp_path / "gonggonggo-2026-08-03.log").read_text(
        encoding="utf-8"
    )


def test_configure_logging_captures_uvicorn_and_root_errors_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    configure_logging(tmp_path, today=date(2026, 8, 3))
    configure_logging(tmp_path, today=date(2026, 8, 3))

    logging.getLogger("uvicorn.error").error("주소 바인딩 실패")
    logging.getLogger("unhandled.startup").error("시작 예외")

    content = (tmp_path / "gonggonggo-2026-08-03.log").read_text(
        encoding="utf-8"
    )
    console = capsys.readouterr().err
    assert content.count("주소 바인딩 실패") == 1
    assert content.count("시작 예외") == 1
    assert console.count("주소 바인딩 실패") == 1
    assert console.count("시작 예외") == 1
