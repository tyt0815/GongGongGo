import sqlite3


def initialize_news_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS news_items (
            id INTEGER PRIMARY KEY,
            item_type TEXT NOT NULL CHECK (item_type IN ('newspaper', 'institution')),
            source TEXT NOT NULL,
            source_name TEXT NOT NULL,
            category TEXT NOT NULL,
            source_category TEXT,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            published_at TEXT,
            discovered_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS news_items_filter_index
        ON news_items(item_type, source, category);

        CREATE TABLE IF NOT EXISTS news_dismissals (
            url TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS news_dismissals_expiry_index
        ON news_dismissals(expires_at);
        """
    )
