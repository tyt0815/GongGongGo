import sqlite3

import pytest

from src.news.schema import initialize_news_schema


def test_initialize_news_schema_defines_news_tables_columns_and_indexes():
    connection = sqlite3.connect(":memory:")

    initialize_news_schema(connection)
    initialize_news_schema(connection)

    item_columns = tuple(
        row[1] for row in connection.execute("PRAGMA table_info(news_items)")
    )
    dismissal_columns = tuple(
        row[1] for row in connection.execute("PRAGMA table_info(news_dismissals)")
    )
    indexes = {
        row[1]
        for row in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type = 'index' AND name LIKE 'news_%'"
        )
    }

    assert item_columns == (
        "id",
        "item_type",
        "source",
        "source_name",
        "category",
        "source_category",
        "title",
        "url",
        "published_at",
        "discovered_at",
    )
    assert dismissal_columns == ("url", "dismissed_at", "expires_at")
    assert indexes == {
        "news_items_filter_index",
        "news_dismissals_expiry_index",
    }


def test_news_items_url_is_unique_and_item_type_is_constrained():
    connection = sqlite3.connect(":memory:")
    initialize_news_schema(connection)
    values = (
        "newspaper",
        "hankyung",
        "Korean Economic Daily",
        "economy",
        None,
        "A news item",
        "https://example.test/news/1",
        None,
        "2026-08-25T00:00:00+00:00",
    )

    connection.execute(
        "INSERT INTO news_items("
        "item_type, source, source_name, category, source_category, title, url, "
        "published_at, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO news_items("
            "item_type, source, source_name, category, source_category, title, url, "
            "published_at, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO news_items("
            "item_type, source, source_name, category, source_category, title, url, "
            "published_at, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("other", *values[1:]),
        )
