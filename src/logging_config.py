import logging
import re
from datetime import date
from pathlib import Path


_LOG_FILENAME = re.compile(r"gonggonggo-(\d{4}-\d{2}-\d{2})\.log\Z")
_LOGGER_NAME = "src"
_FORWARDED_LOGGERS = (_LOGGER_NAME, "uvicorn", "uvicorn.error", "uvicorn.access")
_HANDLER_MARKER = "_gonggonggo_handler"


def cleanup_old_logs(
    log_dir: Path, today: date | None = None, retention_days: int = 14
) -> None:
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return

    cutoff = (today or date.today()).toordinal() - retention_days
    for path in log_dir.iterdir():
        match = _LOG_FILENAME.fullmatch(path.name)
        if match is None or not path.is_file():
            continue
        try:
            log_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if log_date.toordinal() < cutoff:
            path.unlink()


def configure_logging(log_dir: Path, today: date | None = None) -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    current_day = today or date.today()
    cleanup_old_logs(log_dir, today=current_day)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        if not getattr(handler, _HANDLER_MARKER, False):
            continue
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(
        log_dir / f"gonggonggo-{current_day.isoformat()}.log", encoding="utf-8"
    )
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)

    for logger_name in _FORWARDED_LOGGERS:
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        logger.disabled = False
    return logging.getLogger(_LOGGER_NAME)
