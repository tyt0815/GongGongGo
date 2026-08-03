from pathlib import Path

from main import parse_arguments


def test_debug_flag_enables_visible_crawler_browser() -> None:
    assert parse_arguments([]).debug is False
    assert parse_arguments(["--debug"]).debug is True


def test_debug_launcher_passes_debug_flag() -> None:
    launcher = (Path(__file__).parents[1] / "ggg_debug.bat").read_text(encoding="utf-8")

    assert "main.py --debug" in launcher
