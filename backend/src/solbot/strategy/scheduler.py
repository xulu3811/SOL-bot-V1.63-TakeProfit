"""Durable, separate-process closed-candle scheduling and deterministic replay.

Market SQLite is always opened read-only. A new runtime starts at the latest
closed 1m event; resumed catch-up persists expired results as rejected. Replay
stores are explicitly marked and cannot later be consumed as runtime stores.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time

from solbot.storage.models import INTERVAL_MS, timestamp_ms
from . import VERSION, WINDOW, SYMBOLS, evaluate, prepare, provenance


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class StrategyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.connection.row_factory = sqlite3.Row
        tables = {row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        expected = {'strategy_metadata', 'checkpoints', 'decisions', 'snapshots'}
        if tables and tables != expected:
            self.connection.close()
            raise ValueError("a foreign or incomplete database cannot be used as a strategy store")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS strategy_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(
                symbol TEXT PRIMARY KEY,last_close_time INTEGER NOT NULL,strategy_version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,strategy_id TEXT NOT NULL,
                signal_time INTEGER NOT NULL,candidate_time INTEGER NOT NULL,accepted INTEGER NOT NULL,
                strategy_version TEXT NOT NULL,signal_id TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,
                UNIQUE(symbol,strategy_id,signal_time,strategy_version));
            CREATE UNIQUE INDEX IF NOT EXISTS one_candidate_intent
                ON decisions(symbol,candidate_time) WHERE accepted=1;
            CREATE INDEX IF NOT EXISTS accepted_decisions ON decisions(symbol,accepted,id);
            CREATE TABLE IF NOT EXISTS snapshots(
                symbol TEXT NOT NULL,as_of_ms INTEGER NOT NULL,strategy_version TEXT NOT NULL,
                payload_json TEXT NOT NULL,PRIMARY KEY(symbol,as_of_ms,strategy_version));
        """)

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def configure(self, mode: str, symbol: str, market_path: Path) -> None:
        with self.transaction() as conn:
            for key, value in (("mode", mode), ("strategy_version", VERSION),
                               ("source:" + symbol, str(market_path.resolve()))):
                previous = conn.execute("SELECT value FROM strategy_metadata WHERE key=?", (key,)).fetchone()
                if previous is not None and previous[0] != value:
                    raise ValueError(f"strategy store {key} differs; use a separate store for replay/version/source")
                conn.execute("INSERT OR IGNORE INTO strategy_metadata VALUES (?,?)", (key, value))

    def checkpoint(self, symbol: str) -> int | None:
        row = self.connection.execute("SELECT last_close_time FROM checkpoints WHERE symbol=?", (symbol,)).fetchone()
        return row[0] if row else None

    def accepted_after(self, after_id: int = 0, symbol: str | None = None,
                       now_ms: int | None = None, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000 or after_id < 0:
            raise ValueError("invalid decision cursor/limit")
        mode = self.connection.execute("SELECT value FROM strategy_metadata WHERE key='mode'").fetchone()
        if mode and mode[0] == "replay":
            raise ValueError("replay decisions cannot enter the runtime execution feed")
        query, args = "SELECT id,payload_json FROM decisions WHERE accepted=1 AND id>?", [after_id]
        if symbol:
            query += " AND symbol=?"
            args.append(symbol)
        # Filter expiration at SQL level so an old accepted page cannot hide
        # later fresh decisions indefinitely from a polling consumer.
        if now_ms is not None:
            timestamp_ms(now_ms)
            query += " AND signal_time<=? AND candidate_time+300000>?"
            args.extend((now_ms, now_ms))
        query += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [{"decision_id": row["id"], **json.loads(row["payload_json"])}
                for row in self.connection.execute(query, args)]

    def persist(self, symbol: str, as_of_ms: int, results: list[dict], source: dict,
                now_ms: int | None = None) -> list[dict]:
        with self.transaction() as conn:
            previous = conn.execute("SELECT last_close_time FROM checkpoints WHERE symbol=?", (symbol,)).fetchone()
            if previous and previous[0] >= as_of_ms:
                return []
            output = []
            for original in results:
                result = dict(original)
                result["reasons"] = list(original["reasons"])
                if result["accepted"] and now_ms is not None and now_ms >= result["expires_at"]:
                    result["accepted"] = False
                    result["reasons"].append("EXPIRED_DURING_CATCHUP")
                if result["accepted"] and conn.execute(
                        "SELECT 1 FROM decisions WHERE symbol=? AND candidate_time=? AND accepted=1",
                        (symbol, result["candidate_time"])).fetchone():
                    result["accepted"] = False
                    result["reasons"].append("DUPLICATE_CANDIDATE")
                conn.execute("""INSERT INTO decisions(symbol,strategy_id,signal_time,candidate_time,
                    accepted,strategy_version,signal_id,payload_json) VALUES (?,?,?,?,?,?,?,?)""",
                             (symbol, result["strategy_id"], as_of_ms, result["candidate_time"],
                              int(result["accepted"]), VERSION, result["signal_id"], encoded(result)))
                output.append(result)
            snapshot = {"source": source, "decisions": output, "window_bars": WINDOW,
                        "strategy_version": VERSION, "as_of_ms": as_of_ms}
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)", (symbol, as_of_ms, VERSION, encoded(snapshot)))
            conn.execute("""INSERT INTO checkpoints VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE
                SET last_close_time=excluded.last_close_time,strategy_version=excluded.strategy_version""",
                         (symbol, as_of_ms, VERSION))
            return output


class Scheduler:
    def __init__(self, market_db: str | Path, store: StrategyStore, symbol: str):
        if symbol not in SYMBOLS:
            raise ValueError("unsupported symbol")
        self.market_path, self.store, self.symbol = Path(market_db).expanduser().resolve(), store, symbol
        if self.market_path == self.store.path:
            raise ValueError("market and strategy databases must be separate")
        self._cache: dict[str, list[dict]] = {}
        self._source_validated = False

    @contextmanager
    def reader(self):
        conn = sqlite3.connect(self.market_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            if not self._source_validated:
                app_id = conn.execute("PRAGMA application_id").fetchone()[0]
                expected_id = int.from_bytes((self.symbol[:-4] + "1").encode(), "big")
                schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='market_klines'").fetchone()
                owner = re.search(r"CHECK\s*\(\s*symbol\s*=\s*'(SOLUSDT|SUIUSDT)'\s*\)", schema[0], re.IGNORECASE) if schema else None
                if app_id not in (0, expected_id) or owner is None or owner.group(1).upper() != self.symbol:
                    raise ValueError("market database ownership differs from strategy symbol")
                symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM market_klines")}
                if symbols - {self.symbol}:
                    raise ValueError("market database contains a foreign symbol")
                self._source_validated = True
            yield conn
        finally:
            conn.close()

    def _inputs(self, conn: sqlite3.Connection, as_of_ms: int) -> dict:
        result = {}
        for interval, step in INTERVAL_MS.items():
            expected_close = ((as_of_ms + 1) // step) * step - 1
            cached = self._cache.get(interval, [])
            if cached and cached[-1]["close_time"] == expected_close:
                rows = cached
            elif cached and cached[-1]["close_time"] < expected_close:
                incoming = [dict(row) for row in conn.execute("""SELECT * FROM market_klines
                    WHERE symbol=? AND interval=? AND is_closed=1 AND open_time>? AND open_time<=?
                    ORDER BY open_time LIMIT ?""",
                    (self.symbol, interval, cached[-1]["open_time"], expected_close - step + 1, WINDOW))]
                rows = (cached + incoming)[-WINDOW:]
            else:
                rows = list(reversed([dict(row) for row in conn.execute("""SELECT * FROM market_klines
                    WHERE symbol=? AND interval=? AND is_closed=1 AND open_time<=?
                    ORDER BY open_time DESC LIMIT ?""",
                    (self.symbol, interval, expected_close - step + 1, WINDOW))]))
            self._cache[interval] = result[interval] = rows
        return result

    def _event(self, as_of_ms: int, now_ms: int | None) -> list[dict]:
        with self.reader() as conn:
            conn.execute("BEGIN")
            inputs = self._inputs(conn, as_of_ms)
        selected, _ = prepare(self.symbol, inputs, as_of_ms)
        results = evaluate(self.symbol, inputs, as_of_ms)
        return self.store.persist(self.symbol, as_of_ms, results, provenance(selected), now_ms)

    def tick(self, now_ms: int | None = None) -> dict:
        now_ms = time.time_ns() // 1_000_000 if now_ms is None else timestamp_ms(now_ms)
        self.store.configure("runtime", self.symbol, self.market_path)
        checkpoint = self.store.checkpoint(self.symbol)
        # Refresh bounded inputs between polling iterations, including repaired
        # gaps / authoritative REST corrections; within a replay cache is safe.
        self._cache.clear()
        with self.reader() as conn:
            if checkpoint is None:
                events = [row[0] for row in conn.execute("""SELECT close_time FROM market_klines
                    WHERE symbol=? AND interval='1m' AND is_closed=1 AND open_time<=?
                    ORDER BY open_time DESC LIMIT 1""", (self.symbol, now_ms // 60_000 * 60_000 - 60_000))]
            else:
                events = [row[0] for row in conn.execute("""SELECT close_time FROM market_klines
                    WHERE symbol=? AND interval='1m' AND is_closed=1 AND open_time>? AND open_time<=?
                    ORDER BY open_time LIMIT 1440""", (self.symbol, checkpoint - 59_999, now_ms // 60_000 * 60_000 - 60_000))]
        report = Report(self.symbol, "runtime")
        for event in events:
            report.add(self._event(event, now_ms))
        return report.result()

    def replay(self, start_ms: int, end_ms: int) -> dict:
        timestamp_ms(start_ms)
        timestamp_ms(end_ms)
        if end_ms < start_ms:
            raise ValueError("replay end precedes start")
        self.store.configure("replay", self.symbol, self.market_path)
        report = Report(self.symbol, "replay")
        cursor = max(start_ms - 1, self.store.checkpoint(self.symbol) or start_ms - 1)
        self._cache.clear()
        while True:
            with self.reader() as conn:
                events = [row[0] for row in conn.execute("""SELECT close_time FROM market_klines
                    WHERE symbol=? AND interval='1m' AND is_closed=1 AND open_time>? AND open_time<=?
                    ORDER BY open_time LIMIT 1000""", (self.symbol, cursor - 59_999, end_ms - 59_999))]
            if not events:
                break
            for event in events:
                report.add(self._event(event, None))
            cursor = events[-1]
        return report.result()


class Report:
    def __init__(self, symbol: str, mode: str):
        self.symbol, self.mode = symbol, mode
        self.processed = 0
        self.counts = {name: Counter() for name in ("T1", "R1")}
        self.reasons = {name: Counter() for name in ("T1", "R1")}
        self.regimes = Counter()
        self.samples: dict[str, dict] = {}
        self.first = self.last = None

    def add(self, results: list[dict]) -> None:
        if not results:
            return
        self.processed += 1
        self.first = results[0]["signal_time"] if self.first is None else self.first
        self.last = results[0]["signal_time"]
        self.regimes[results[0]["regime"]] += 1
        for result in results:
            strategy = result["strategy_id"]
            kind = "accepted" if result["accepted"] else "rejected"
            self.counts[strategy][kind] += 1
            self.reasons[strategy].update(result["reasons"])
            self.samples.setdefault(strategy + ":" + kind, result)

    def result(self) -> dict:
        return {"symbol": self.symbol, "mode": self.mode, "strategy_version": VERSION,
                "processed": self.processed, "first_signal_time": self.first, "last_signal_time": self.last,
                "counts": {key: {"accepted": value["accepted"], "rejected": value["rejected"]}
                           for key, value in self.counts.items()},
                "reasons": {key: dict(sorted(value.items())) for key, value in self.reasons.items()},
                "regimes": dict(sorted(self.regimes.items())), "samples": self.samples,
                "limitations": ["Research thresholds are unvalidated per symbol",
                                "Candle replay is not execution or profitability validation",
                                "R1 live is disabled", "Bounded 120-bar indicator initialization"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--strategy-db", required=True)
    parser.add_argument("--symbol", choices=sorted(SYMBOLS), required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--once", action="store_true")
    action.add_argument("--replay", action="store_true")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.replay and (args.start_ms is None or args.end_ms is None):
        parser.error("--replay requires --start-ms and --end-ms")
    if Path(args.market_db).expanduser().resolve() == Path(args.strategy_db).expanduser().resolve():
        parser.error("market and strategy databases must be separate")
    store = StrategyStore(args.strategy_db)
    try:
        scheduler = Scheduler(args.market_db, store, args.symbol)
        report = scheduler.replay(args.start_ms, args.end_ms) if args.replay else scheduler.tick()
        result = json.dumps(report, indent=2, sort_keys=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(result + "\n", encoding="utf-8")
        print(result)
    finally:
        store.close()


if __name__ == "__main__":
    main()
