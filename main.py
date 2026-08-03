import argparse
from collections.abc import Sequence

from src.runtime import run
from src.web import create_app


app = create_app()


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GongGongGo local server")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show the Playwright Chromium window while crawling",
    )
    return parser.parse_args(arguments)


if __name__ == "__main__":
    options = parse_arguments()
    run(create_app(crawler_headless=not options.debug))
