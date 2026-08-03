import logging
import threading
import time
import urllib.request
import webbrowser

import uvicorn
from fastapi import FastAPI

from .config import DB_PATH, JSON_PATH, LOG_DIR
from .database import initialize_database
from .logging_config import configure_logging
from .repository import Repository


_APP_URL = "http://127.0.0.1:8000"
_HEALTH_URL = f"{_APP_URL}/health"
_HEALTH_RETRY_SECONDS = 0.1
logger = logging.getLogger(__name__)


def default_health_check(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def open_browser_when_healthy(
    url: str,
    health_url: str,
    opener=webbrowser.open,
    health_check=default_health_check,
) -> None:
    while True:
        try:
            if health_check(health_url):
                opener(url)
                return
        except OSError:
            pass
        time.sleep(_HEALTH_RETRY_SECONDS)


def run(app: FastAPI) -> None:
    configure_logging(LOG_DIR)
    try:
        initialize_database(DB_PATH, JSON_PATH)
        settings = Repository(DB_PATH).get_settings()
        if settings.open_browser:
            threading.Thread(
                target=open_browser_when_healthy,
                args=(_APP_URL, _HEALTH_URL),
                daemon=True,
            ).start()
        uvicorn.run(app, host="127.0.0.1", port=8000, log_config=None)
    except KeyboardInterrupt:
        raise
    except BaseException:
        logger.exception("Server startup failed")
        raise
