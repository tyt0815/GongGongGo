"""Individual static HTML news source crawlers."""

from . import hankyung, kodit, kogas, mk, reb
from ..registry import SOURCES


SOURCE_CRAWLERS = {
    "hankyung": hankyung.crawl,
    "mk": mk.crawl,
    "reb": reb.crawl,
    "kodit": kodit.crawl,
    "kogas": kogas.crawl,
}
assert tuple(SOURCE_CRAWLERS) == tuple(SOURCES)


__all__ = ["SOURCE_CRAWLERS"]
