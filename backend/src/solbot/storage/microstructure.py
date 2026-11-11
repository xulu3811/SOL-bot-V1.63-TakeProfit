"""Bounded public research storage, deliberately separate from durable decision audits."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading

from solbot.mathematics.market_features import input_hash
from .models import timestamp_ms, validate_symbol


class MicrostructureStore:
    TABLES = {"micro_owner", "micro_latest", "micro_samples"}

    def __init__(self, path, symbol, *, read_only=False, retention_days=7, max_rows=120_000, history_interval_ms=5000):
        self.path, self.symbol = Path(path).resolve(), validate_symbol(symbol)
        self.read_only = read_only
        if not 1 <= retention_days <= 7 or not 1 <= max_rows <= 120_000 or not 1000 <= history_interval_ms <= 60000:
            raise ValueError("research retention must remain bounded")
        self.retention_ms, self.max_rows, self.history_interval_ms = retention_days * 86400000, max_rows, history_interval_ms
        self._lock = threading.Lock()
        if self.path.exists():
            with closing(self._connect(read_only=True)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables and (tables != self.TABLES or db.execute("SELECT symbol,kind FROM micro_owner WHERE singleton=1").fetchone() != (symbol, "PUBLIC_MICRO_RESEARCH")):
                    raise ValueError("foreign database or symbol; refusing microstructure initialization")
                if read_only and tables != self.TABLES:
                    raise ValueError("microstructure database is not initialized")
        elif read_only:
            raise FileNotFoundError(self.path)
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=1000")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("CREATE TABLE IF NOT EXISTS micro_owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1),symbol TEXT NOT NULL,kind TEXT NOT NULL)")
                db.execute("INSERT OR IGNORE INTO micro_owner VALUES (1,?,'PUBLIC_MICRO_RESEARCH')", (symbol,))
                if db.execute("SELECT symbol FROM micro_owner").fetchone()[0] != symbol:
                    raise ValueError("microstructure symbol ownership mismatch")
                db.execute("CREATE TABLE IF NOT EXISTS micro_latest(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generated_at INTEGER NOT NULL,input_hash TEXT NOT NULL,payload TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS micro_samples(id INTEGER PRIMARY KEY,generated_at INTEGER NOT NULL,input_hash TEXT NOT NULL,payload TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS micro_samples_time ON micro_samples(generated_at)")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _connect(self, *, read_only=False):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5) if read_only else sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        if read_only:
            db.execute("PRAGMA query_only=ON")
        return db

    def append(self, snapshot):
        if self.read_only:
            raise PermissionError("read-only research store")
        if not isinstance(snapshot, dict) or snapshot.get("symbol") != self.symbol:
            raise ValueError("snapshot symbol mismatch")
        generated = timestamp_ms(snapshot.get("feature_generated_time_ms"))
        digest = input_hash(snapshot)
        if snapshot.get("input_hash") != digest:
            raise ValueError("snapshot input hash mismatch")
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("microstructure snapshot exceeds 64 KiB")
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT generated_at FROM micro_latest WHERE singleton=1").fetchone()
                if old is not None and generated < old[0]:
                    raise ValueError("microstructure snapshot time regressed")
                db.execute("INSERT OR REPLACE INTO micro_latest VALUES (1,?,?,?)", (generated, digest, encoded))
                last = db.execute("SELECT generated_at FROM micro_samples ORDER BY id DESC LIMIT 1").fetchone()
                if last is None or generated // self.history_interval_ms > last[0] // self.history_interval_ms:
                    db.execute("INSERT INTO micro_samples(generated_at,input_hash,payload) VALUES (?,?,?)", (generated, digest, encoded))
                    db.execute("DELETE FROM micro_samples WHERE generated_at<?", (generated - self.retention_ms,))
                    db.execute("DELETE FROM micro_samples WHERE id<=(SELECT MAX(id)-? FROM micro_samples)", (self.max_rows,))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def latest(self):
        with closing(self._connect(read_only=True)) as db:
            row = db.execute("SELECT payload,input_hash FROM micro_latest WHERE singleton=1").fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if result.get("symbol") != self.symbol or input_hash(result) != row[1] or result.get("input_hash") != row[1]:
            raise ValueError("corrupt/mismatched microstructure snapshot")
        return result

    def history(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("history limit must be 1..1000")
        with closing(self._connect(read_only=True)) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM micro_samples ORDER BY id DESC LIMIT ?", (limit,))]
