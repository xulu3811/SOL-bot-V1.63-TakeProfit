"""Explicit schema migrations. Append migrations; never edit a deployed version."""

SCHEMA_VERSION = 1

MIGRATIONS = {
    1: (
        """CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)""",
        """CREATE TABLE market_klines (
            symbol TEXT NOT NULL CHECK(symbol = 'SOLUSDT'),
            interval TEXT NOT NULL CHECK(interval IN ('1m','5m','15m','1h','4h')),
            open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
            open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
            volume TEXT NOT NULL, quote_volume TEXT NOT NULL,
            trade_count INTEGER NOT NULL CHECK(trade_count >= 0),
            taker_buy_base_volume TEXT NOT NULL, taker_buy_quote_volume TEXT NOT NULL,
            is_closed INTEGER NOT NULL CHECK(is_closed = 1),
            source TEXT NOT NULL CHECK(source IN ('rest','ws')),
            ingested_at INTEGER NOT NULL, event_time INTEGER,
            PRIMARY KEY(symbol, interval, open_time)
        ) WITHOUT ROWID""",
        "CREATE INDEX market_klines_interval_time ON market_klines(interval, open_time)",
        """CREATE TABLE market_sync_state (
            interval TEXT PRIMARY KEY CHECK(interval IN ('1m','5m','15m','1h','4h')),
            cursor INTEGER, status TEXT NOT NULL, updated_at INTEGER NOT NULL,
            checked_start INTEGER, checked_end INTEGER, checked_at INTEGER
        ) WITHOUT ROWID""",
        """CREATE TABLE market_data_gaps (
            id INTEGER PRIMARY KEY, interval TEXT NOT NULL,
            start_time INTEGER NOT NULL, end_time INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open','resolved')),
            detected_at INTEGER NOT NULL, last_checked_at INTEGER NOT NULL,
            resolved_at INTEGER,
            CHECK(end_time >= start_time),
            FOREIGN KEY(interval) REFERENCES market_sync_state(interval)
        )""",
        """CREATE UNIQUE INDEX market_data_gaps_open_unique
            ON market_data_gaps(interval,start_time,end_time) WHERE status='open'""",
        "CREATE INDEX market_data_gaps_status_time ON market_data_gaps(status,interval,start_time)",
        """CREATE TABLE market_connection_events (
            id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL,
            kind TEXT NOT NULL, detail TEXT NOT NULL)""",
        """CREATE TABLE system_health_snapshots (
            id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, payload TEXT NOT NULL)""",
    ),
}


def migrations_for(symbol: str):
    """Render a new isolated file's CHECK from a fixed allowlist.

    The deployed SOL v1 SQL and its version remain unchanged. An SUI file has
    the same table contract with a strict SUI CHECK; no existing table is rebuilt.
    """
    from .models import validate_symbol

    validate_symbol(symbol)
    return {version: tuple(statement.replace("CHECK(symbol = 'SOLUSDT')",
                                           f"CHECK(symbol = '{symbol}')")
                           for statement in statements)
            for version, statements in MIGRATIONS.items()}
