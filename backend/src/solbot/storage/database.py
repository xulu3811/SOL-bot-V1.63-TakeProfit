"""File-backed SQLite WAL store with one controlled asynchronous write channel.

All mutations, migration, backup and close operations share one asyncio lock.
Cancellation waits for an in-flight worker to finish before releasing that lock.
Reads use short-lived read-only connections and can be called via to_thread.
Query and gap bounds are inclusive candle open timestamps.
"""

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, fields
from decimal import Decimal
import json
import os
import re
from pathlib import Path
import sqlite3
from typing import Any, Callable, TypeVar
import uuid

from .models import INTERVAL_MS, Kline, timestamp_ms, utc_ms, validate_symbol
from .schema import SCHEMA_VERSION, migrations_for


T = TypeVar("T")
COLUMNS = tuple(field.name for field in fields(Kline))
DECIMAL_COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume",
                   "taker_buy_base_volume", "taker_buy_quote_volume")
EVENT_RETENTION = 10_000
SNAPSHOT_RETENTION = 1_440
RESOLVED_GAP_RETENTION = 5_000
MAX_JSON_BYTES = 65_536
APPLICATION_IDS = {"SOLUSDT": int.from_bytes(b"SOL1", "big"), "SUIUSDT": int.from_bytes(b"SUI1", "big")}


class DatabaseUnavailable(RuntimeError):
    """The database lifecycle has not started or has already closed."""


def _interval(value: str) -> int:
    if value not in INTERVAL_MS:
        raise ValueError("unsupported kline interval")
    return INTERVAL_MS[value]


def _json(value: Any) -> str:
    result = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(result.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("metadata exceeds 64 KiB")
    return result


def _record(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["is_closed"] = bool(result["is_closed"])
    return result


def _same_values(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a["trade_count"] == b["trade_count"] and a["close_time"] == b["close_time"]
            and all(Decimal(a[name]) == Decimal(b[name]) for name in DECIMAL_COLUMNS))


class Database:
    def __init__(self, path: str | os.PathLike[str], symbol: str = "SOLUSDT"):
        if str(path) == ":memory:":
            raise ValueError("a file path is required for WAL and independent read connections")
        self.path = Path(path).expanduser().resolve()
        self.symbol = validate_symbol(symbol)
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    @property
    def available(self) -> bool:
        return self._connection is not None

    async def _serialized(self, operation: Callable[[], T]) -> T:
        async with self._lock:
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled await does not stop a SQLite call in its worker.
                # Keep the single-writer lock until that operation actually ends,
                # including when shutdown cancellation is requested repeatedly.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()  # Retrieve any failure without masking cancellation.
                raise

    def _writer(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseUnavailable("database is not started")
        return self._connection

    async def start(self) -> None:
        def operation() -> None:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                existing={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                expected={'schema_migrations','market_klines','market_sync_state','market_data_gaps','market_connection_events','system_health_snapshots'}
                if existing:
                    if existing != expected:
                        raise DatabaseUnavailable('database contains a foreign or incomplete schema')
                    self._validate_ownership(conn,allow_legacy=True)
                conn.execute("PRAGMA busy_timeout=5000")
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise DatabaseUnavailable("SQLite refused WAL mode")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-16000")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA wal_autocheckpoint=1000")
                conn.execute("PRAGMA journal_size_limit=16777216")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise DatabaseUnavailable("database schema is newer than this application")
                with self._transaction(conn):
                    for target in range(version + 1, SCHEMA_VERSION + 1):
                        for statement in migrations_for(self.symbol)[target]:
                            conn.execute(statement)
                        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)", (target, utc_ms()))
                        conn.execute(f"PRAGMA user_version={target}")
                    applied = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                    if applied != SCHEMA_VERSION:
                        raise DatabaseUnavailable("schema migration history is inconsistent")
                    self._validate_ownership(conn, allow_legacy=True)
                    conn.execute(f"PRAGMA application_id={APPLICATION_IDS[self.symbol]}")
                    conn.executemany("""INSERT INTO market_sync_state(interval,status,updated_at)
                        VALUES (?,'never_started',?) ON CONFLICT(interval) DO NOTHING""",
                                     [(interval, utc_ms()) for interval in INTERVAL_MS])
                self._connection = conn
            except BaseException:
                conn.close()
                raise
        await self._serialized(operation)

    def _validate_ownership(self, conn: sqlite3.Connection, *, allow_legacy: bool = False) -> None:
        """An empty v1 SOL file is still SOL-owned; rows alone cannot prove it."""
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        expected = APPLICATION_IDS[self.symbol]
        if application_id != expected and not (allow_legacy and application_id == 0):
            raise DatabaseUnavailable("database belongs to a different market symbol")
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='market_klines'").fetchone()
        match = re.search(r"CHECK\s*\(\s*symbol\s*=\s*'(SOLUSDT|SUIUSDT)'\s*\)", row[0], re.IGNORECASE) if row else None
        if match is None or match.group(1).upper() != self.symbol:
            raise DatabaseUnavailable("database symbol constraint does not match configured market")
        if conn.execute("SELECT 1 FROM market_klines WHERE symbol<>? LIMIT 1", (self.symbol,)).fetchone():
            raise DatabaseUnavailable("database contains foreign-symbol market rows")

    @staticmethod
    @contextmanager
    def _transaction(conn: sqlite3.Connection):
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    @contextmanager
    def _reader(self):
        self._writer()  # Fail explicitly outside the started lifecycle.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_IDS[self.symbol]:
                raise DatabaseUnavailable("database belongs to a different market symbol")
            yield conn
        finally:
            conn.close()

    async def close(self) -> None:
        def operation() -> None:
            if self._connection is not None:
                conn = self._connection
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                finally:
                    conn.close()
                    self._connection = None
        await self._serialized(operation)

    async def write_klines(self, klines: list[Kline]) -> int:
        """Atomically validate/write a batch; return inserted or changed row count.

        Open events are ignored. Numeric-equivalent repeats are idempotent.
        REST is authoritative for closed conflicts, which are recorded in the
        same transaction. Conflicting WS records never replace closed records.
        """
        batch = tuple(klines)

        def operation() -> int:
            conn = self._writer()
            for item in batch:
                if not isinstance(item, Kline):
                    raise ValueError("write_klines accepts Kline records only")
                item.validate()
                if item.symbol != self.symbol:
                    raise ValueError("kline symbol does not match database ownership")
            records = [asdict(item) for item in batch if item.is_closed]
            if not records:
                return 0
            changed = 0
            with self._transaction(conn):
                # Fetch existing keys in bounded bulk queries, never one SELECT
                # per candle during the initial 30-day backfill.
                by_interval: dict[str, set[int]] = defaultdict(set)
                for item in records:
                    by_interval[item["interval"]].add(item["open_time"])
                existing: dict[tuple[str, int], dict[str, Any]] = {}
                for interval, timestamps in by_interval.items():
                    times = list(timestamps)
                    for offset in range(0, len(times), 900):
                        chunk = times[offset:offset + 900]
                        placeholders = ",".join("?" for _ in chunk)
                        rows = conn.execute(f"SELECT * FROM market_klines WHERE symbol=? AND interval=? AND open_time IN ({placeholders})", (self.symbol, interval, *chunk))
                        for row in rows:
                            existing[(interval, row["open_time"])] = _record(row)
                upserts: dict[tuple[str, int], dict[str, Any]] = {}
                for incoming in records:
                    key = (incoming["interval"], incoming["open_time"])
                    previous = existing.get(key)
                    if previous is not None:
                        identical = _same_values(previous, incoming)
                        if identical:
                            if previous["source"] == "rest" or incoming["source"] != "rest":
                                continue
                        else:
                            action = "rest_authoritative" if incoming["source"] == "rest" else "ignored_ws"
                            self._insert_event(conn, "kline_conflict", _json({
                                "interval": key[0], "open_time": key[1], "resolution": action,
                                "existing": previous, "incoming": incoming,
                            }))
                            if incoming["source"] != "rest":
                                continue
                    upserts[key] = incoming
                    existing[key] = incoming
                if upserts:
                    columns = ",".join(COLUMNS)
                    values = ",".join("?" for _ in COLUMNS)
                    updates = ",".join(f"{name}=excluded.{name}" for name in COLUMNS if name not in {"symbol", "interval", "open_time"})
                    conn.executemany(f"INSERT INTO market_klines({columns}) VALUES ({values}) ON CONFLICT(symbol,interval,open_time) DO UPDATE SET {updates}",
                                     [tuple(item[name] for name in COLUMNS) for item in upserts.values()])
                    changed = len(upserts)
                self._prune_events(conn)
            return changed
        return await self._serialized(operation)

    def klines(self, interval: str, limit: int = 500, start: int | None = None,
               end: int | None = None) -> list[dict[str, Any]]:
        _interval(interval)
        if type(limit) is not int or not 1 <= limit <= 1001:
            raise ValueError("limit must be between 1 and 1001")
        clauses = ["symbol=?", "interval=?"]
        params: list[Any] = [self.symbol, interval]
        if start is not None:
            clauses.append("open_time>=?")
            params.append(timestamp_ms(start, "start"))
        if end is not None:
            clauses.append("open_time<=?")
            params.append(timestamp_ms(end, "end"))
        if start is not None and end is not None and end < start:
            raise ValueError("end precedes start")
        # With a start cursor return the next page; otherwise the latest page.
        order = "ASC" if start is not None else "DESC"
        with self._reader() as conn:
            rows = [_record(row) for row in conn.execute(
                f"SELECT * FROM market_klines WHERE {' AND '.join(clauses)} ORDER BY open_time {order} LIMIT ?", (*params, limit))]
        return rows if order == "ASC" else list(reversed(rows))

    def latest(self, interval: str) -> dict[str, Any] | None:
        records = self.klines(interval, limit=1)
        return records[0] if records else None

    def open_times(self, interval: str, start: int, end: int) -> list[int]:
        _interval(interval)
        timestamp_ms(start, "start")
        timestamp_ms(end, "end")
        if end < start:
            return []
        with self._reader() as conn:
            return [row[0] for row in conn.execute("""SELECT open_time FROM market_klines
                WHERE symbol=? AND interval=? AND open_time>=? AND open_time<=? ORDER BY open_time""", (self.symbol, interval, start, end))]

    def unconfirmed_open_times(self, interval: str, start: int, end: int) -> list[int]:
        """Closed WS candles in a bounded window still awaiting REST confirmation."""
        _interval(interval)
        timestamp_ms(start, "start")
        timestamp_ms(end, "end")
        if end < start:
            return []
        with self._reader() as conn:
            return [row[0] for row in conn.execute("""SELECT open_time FROM market_klines
                WHERE symbol=? AND interval=? AND source='ws' AND open_time>=? AND open_time<=?
                ORDER BY open_time""", (self.symbol, interval, start, end))]

    def summary(self) -> dict[str, Any]:
        with self._reader() as conn:
            # One read transaction gives health a consistent cross-table view.
            conn.execute("BEGIN")
            aggregates = {row["interval"]: dict(row) for row in conn.execute("""SELECT interval,
                COUNT(*) AS count, MIN(open_time) AS first_open_time, MAX(open_time) AS last_open_time,
                MAX(close_time) AS last_close_time FROM market_klines WHERE symbol=? GROUP BY interval""", (self.symbol,))}
            gaps = {row["interval"]: dict(row) for row in conn.execute("""SELECT interval,
                COUNT(*) AS gap_count, SUM(end_time-start_time) AS span FROM market_data_gaps
                WHERE status='open' GROUP BY interval""")}
            states = {row["interval"]: dict(row) for row in conn.execute("SELECT * FROM market_sync_state")}
            intervals = {}
            for interval, step in INTERVAL_MS.items():
                totals = aggregates.get(interval, {})
                gap = gaps.get(interval, {})
                state = states.get(interval, {})
                gap_count = gap.get("gap_count", 0)
                intervals[interval] = {
                    "count": totals.get("count", 0), "first_open_time": totals.get("first_open_time"),
                    "last_open_time": totals.get("last_open_time"), "last_close_time": totals.get("last_close_time"),
                    "gap_count": gap_count, "missing_bars": gap.get("span", 0) // step + gap_count,
                    "sync_status": state.get("status", "never_started"), "sync_cursor": state.get("cursor"),
                    "checked_start": state.get("checked_start"), "checked_end": state.get("checked_end"),
                    "checked_at": state.get("checked_at"), "updated_at": state.get("updated_at"),
                }
            return {"symbol": self.symbol, "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
                    "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0], "intervals": intervals}

    def gaps(self, *, interval: str | None = None, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        if interval is not None:
            _interval(interval)
        if type(limit) is not int or not 1 <= limit <= 1001 or type(offset) is not int or offset < 0:
            raise ValueError("invalid gap pagination")
        clause = " AND interval=?" if interval else ""
        params: tuple[Any, ...] = (interval, limit, offset) if interval else (limit, offset)
        with self._reader() as conn:
            return [dict(row) for row in conn.execute(f"""SELECT * FROM market_data_gaps
                WHERE status='open'{clause} ORDER BY interval,start_time LIMIT ? OFFSET ?""", params)]

    def gap_history(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1001:
            raise ValueError("invalid gap history limit")
        with self._reader() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM market_data_gaps ORDER BY id DESC LIMIT ?", (limit,))]

    async def replace_gaps(self, interval: str, gaps: list[tuple[int, int]],
                           checked_start: int, checked_end: int) -> None:
        step = _interval(interval)
        timestamp_ms(checked_start, "checked_start")
        timestamp_ms(checked_end, "checked_end")
        if checked_start % step or checked_end % step or checked_end < checked_start:
            raise ValueError("checked gap range must contain aligned candle open times")
        ranges = sorted(set(tuple(item) for item in gaps))
        for index, (start, end) in enumerate(ranges):
            timestamp_ms(start, "gap start")
            timestamp_ms(end, "gap end")
            if start % step or end % step or not checked_start <= start <= end <= checked_end:
                raise ValueError("gap is outside the checked aligned range")
            if index and start <= ranges[index - 1][1]:
                raise ValueError("gap ranges overlap")

        def operation() -> None:
            conn = self._writer()
            now = utc_ms()
            with self._transaction(conn):
                previous = list(conn.execute("""SELECT * FROM market_data_gaps WHERE interval=?
                    AND status='open' AND start_time<=? AND end_time>=?""", (interval, checked_end, checked_start)))
                # Preserve unchecked portions when only a recent window is scanned.
                desired = set(ranges)
                for row in previous:
                    if row["start_time"] < checked_start:
                        desired.add((row["start_time"], checked_start - step))
                    if row["end_time"] > checked_end:
                        desired.add((checked_end + step, row["end_time"]))
                for row in previous:
                    key = (row["start_time"], row["end_time"])
                    if key in desired:
                        conn.execute("UPDATE market_data_gaps SET last_checked_at=? WHERE id=?", (now, row["id"]))
                        desired.remove(key)
                    else:
                        conn.execute("""UPDATE market_data_gaps SET status='resolved',resolved_at=?,last_checked_at=?
                            WHERE id=?""", (now, now, row["id"]))
                conn.executemany("""INSERT INTO market_data_gaps
                    (interval,start_time,end_time,status,detected_at,last_checked_at)
                    VALUES (?,?,?,'open',?,?)""", [
                        (interval, start, end, min(
                            (row["detected_at"] for row in previous
                             if row["start_time"] <= end and row["end_time"] >= start),
                            default=now), now)
                        for start, end in sorted(desired)
                    ])
                conn.execute("""UPDATE market_sync_state SET checked_start=?,checked_end=?,checked_at=?,updated_at=?
                    WHERE interval=?""", (checked_start, checked_end, now, now, interval))
                conn.execute("""DELETE FROM market_data_gaps WHERE status='resolved' AND id NOT IN
                    (SELECT id FROM market_data_gaps WHERE status='resolved' ORDER BY id DESC LIMIT ?)""", (RESOLVED_GAP_RETENTION,))
        await self._serialized(operation)

    async def set_sync(self, interval: str, cursor: int | None, status: str) -> None:
        step = _interval(interval)
        if cursor is not None and timestamp_ms(cursor, "cursor") % step:
            raise ValueError("sync cursor is not interval-aligned")
        if not isinstance(status, str) or not status or len(status) > 64:
            raise ValueError("sync status must have 1 to 64 characters")
        def operation() -> None:
            conn = self._writer()
            with self._transaction(conn):
                conn.execute("""UPDATE market_sync_state SET cursor=?,status=?,updated_at=?
                    WHERE interval=?""", (cursor, status, utc_ms(), interval))
        await self._serialized(operation)

    @staticmethod
    def _insert_event(conn: sqlite3.Connection, kind: str, detail: str) -> None:
        conn.execute("INSERT INTO market_connection_events(created_at,kind,detail) VALUES (?,?,?)", (utc_ms(), kind, detail))

    @staticmethod
    def _prune_events(conn: sqlite3.Connection) -> None:
        conn.execute("""DELETE FROM market_connection_events WHERE id <=
            (SELECT MAX(id)-? FROM market_connection_events)""", (EVENT_RETENTION,))

    async def event(self, kind: str, detail: Any) -> None:
        if not isinstance(kind, str) or not kind or len(kind) > 128:
            raise ValueError("event kind must have 1 to 128 characters")
        encoded = _json(detail)
        def operation() -> None:
            conn = self._writer()
            with self._transaction(conn):
                self._insert_event(conn, kind, encoded)
                self._prune_events(conn)
        await self._serialized(operation)

    async def snapshot(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("health snapshot must be an object")
        encoded = _json(payload)
        def operation() -> None:
            conn = self._writer()
            with self._transaction(conn):
                conn.execute("INSERT INTO system_health_snapshots(created_at,payload) VALUES (?,?)", (utc_ms(), encoded))
                conn.execute("""DELETE FROM system_health_snapshots WHERE id <=
                    (SELECT MAX(id)-? FROM system_health_snapshots)""", (SNAPSHOT_RETENTION,))
        await self._serialized(operation)

    async def backup(self, destination: str | os.PathLike[str]) -> None:
        target = Path(destination).expanduser().resolve()
        if target == self.path or (target.exists() and self.path.exists() and os.path.samefile(target, self.path)):
            raise ValueError("backup destination must differ from the live database")
        def operation() -> None:
            conn = self._writer()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                backup_conn = sqlite3.connect(temporary)
                try:
                    conn.backup(backup_conn, pages=256)
                    if backup_conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise DatabaseUnavailable("backup integrity check failed")
                finally:
                    backup_conn.close()
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        await self._serialized(operation)

    async def checkpoint_and_truncate_wal(self) -> tuple[int, int, int]:
        return await self._serialized(lambda: tuple(self._writer().execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()))
