import pytest
from pydantic import ValidationError

from src.domain import PostStatus, Settings


def test_status_values_are_stable():
    assert [status.value for status in PostStatus] == [
        "review_pending", "planned", "applied", "excluded"
    ]


@pytest.mark.parametrize("value", [1, 2, 4])
def test_settings_accept_supported_concurrency(value):
    assert Settings(concurrency=value, open_browser=False).concurrency == value


def test_settings_reject_unsupported_concurrency():
    with pytest.raises(ValidationError):
        Settings(concurrency=3, open_browser=False)
