"""Separate S1 research ledger. It never writes the T1 strategy store.

Replaying the same candidate close is ignored, so a second run does not create
a second fill.
"""

from decimal import Decimal
import sqlite3
from pathlib import Path

from solbot.strategy.s1_short import VERSION


class S1Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS s1_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS opportunities(
                candidate_close_ms INTEGER PRIMARY KEY,
                block_id INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(
                candidate_close_ms INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                limit_price TEXT NOT NULL,
                qty TEXT,
                expires_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS fills(
                candidate_close_ms INTEGER PRIMARY KEY,
                entry_ms INTEGER NOT NULL,
                entry_price TEXT NOT NULL,
                qty TEXT NOT NULL,
                notional TEXT NOT NULL,
                entry_fee TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exits(
                candidate_close_ms INTEGER PRIMARY KEY,
                exit_ms INTEGER NOT NULL,
                exit_price TEXT NOT NULL,
                reason TEXT NOT NULL,
                exit_fee TEXT NOT NULL,
                pnl TEXT NOT NULL);
        """)
        self.connection.execute(
            "INSERT OR IGNORE INTO s1_meta VALUES ('strategy_version', ?)", (VERSION,)
        )
        row = self.connection.execute("SELECT value FROM s1_meta WHERE key='strategy_version'").fetchone()
        if row[0] != VERSION:
            raise ValueError("s1 store version differs; use a separate file")

    def close(self) -> None:
        self.connection.close()

    def record(self, opportunities: list[dict], orders: list[dict], fills: list[dict], exits: list[dict]) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for row in opportunities:
                self.connection.execute(
                    "INSERT OR IGNORE INTO opportunities VALUES (?,?,?,?,?,?)",
                    (int(row["close_time"]), int(row["block_id"]), str(row["open"]), str(row["high"]),
                     str(row["low"]), str(row["close"])),
                )
            for row in orders:
                self.connection.execute(
                    "INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?)",
                    (int(row["candidate_close_ms"]), row["status"], str(row["limit_price"]),
                     None if row.get("qty") is None else str(row["qty"]), int(row["expires"])),
                )
            for row in fills:
                self.connection.execute(
                    "INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?)",
                    (int(row["candidate_close_ms"]), int(row["entry_ms"]), str(row["entry_price"]),
                     str(row["qty"]), str(row["notional"]), str(row["entry_fee"])),
                )
            for row in exits:
                self.connection.execute(
                    "INSERT OR IGNORE INTO exits VALUES (?,?,?,?,?,?)",
                    (int(row["candidate_close_ms"]), int(row["exit_ms"]), str(row["exit_price"]),
                     row["reason"], str(row["exit_fee"]), str(row["pnl"])),
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def counts(self) -> dict:
        def one(sql):
            return int(self.connection.execute(sql).fetchone()[0])
        return {
            "opportunities": one("SELECT COUNT(*) FROM opportunities"),
            "orders": one("SELECT COUNT(*) FROM orders"),
            "fills": one("SELECT COUNT(*) FROM fills"),
            "exits": one("SELECT COUNT(*) FROM exits"),
        }


def money(value: Decimal) -> str:
    return format(value, "f")
