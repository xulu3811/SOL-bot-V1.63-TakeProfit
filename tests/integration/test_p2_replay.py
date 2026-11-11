"""Durable replay tests using the real P1 schema/model/storage path."""

import asyncio
from copy import deepcopy
from contextlib import closing
from dataclasses import fields
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from solbot.storage.database import Database
from solbot.storage.models import Kline
from solbot.strategy import evaluate
from solbot.strategy.scheduler import Scheduler, StrategyStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_p2_strategy import AS_OF, BOUNDARY, positive_fixture  # noqa: E402


async def write_fixture(path, data):
    db = Database(path)
    await db.start()
    try:
        await db.write_klines([Kline(**row) for rows in data.values() for row in rows])
    finally:
        await db.close()


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.market = self.root / "market.sqlite"
        self.data = positive_fixture()
        asyncio.run(write_fixture(self.market, self.data))

    def store(self, name):
        store = StrategyStore(self.root / name)
        self.addCleanup(store.close)
        return store

    def test_two_replays_byte_identical_and_source_untouched(self):
        before = self.market.read_bytes()
        left, right = self.store("left.sqlite"), self.store("right.sqlite")
        a = Scheduler(self.market, left, "SOLUSDT").replay(BOUNDARY - 1, AS_OF)
        b = Scheduler(self.market, right, "SOLUSDT").replay(BOUNDARY - 1, AS_OF)
        self.assertEqual(a, b)
        self.assertEqual(a["processed"], 3)
        self.assertEqual(a["counts"]["T1"]["accepted"], 1)
        for table in ("decisions", "snapshots", "checkpoints"):
            self.assertEqual([tuple(r) for r in left.connection.execute(f"SELECT * FROM {table}")],
                             [tuple(r) for r in right.connection.execute(f"SELECT * FROM {table}")])
        self.assertEqual(before, self.market.read_bytes())
        with self.assertRaises(ValueError):
            left.accepted_after(now_ms=AS_OF)

    def test_rejected_first_confirmation_can_accept_later_then_restart_dedup(self):
        store = self.store("runtime.sqlite")
        scheduler = Scheduler(self.market, store, "SOLUSDT")
        first = scheduler.tick(BOUNDARY + 1)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["counts"]["T1"]["accepted"], 0)
        later = Scheduler(self.market, store, "SOLUSDT").tick(AS_OF + 1)
        self.assertEqual(later["counts"]["T1"]["accepted"], 1)
        self.assertEqual(Scheduler(self.market, store, "SOLUSDT").tick(AS_OF + 2)["processed"], 0)
        accepted = store.accepted_after(symbol="SOLUSDT", now_ms=AS_OF + 1)
        self.assertEqual(len(accepted), 1)
        self.assertTrue(accepted[0]["accepted"])
        self.assertEqual(store.accepted_after(after_id=accepted[0]["decision_id"], now_ms=AS_OF + 1), [])
        # A later confirmation on the same five-minute candidate remains
        # rejected across a scheduler recreation, despite valid pure strategy.
        next_time = AS_OF + 60_000
        next_row = dict(self.data["1m"][-1])
        next_row.update(open_time=next_row["open_time"] + 60_000, close_time=next_time,
                        open="96.65", high="96.9", low="96.5", close="96.8",
                        event_time=next_time + 10, ingested_at=next_time + 20)
        self.data["1m"].append(next_row)
        # Keep the higher-low condition and use a rising continuation.
        self.assertTrue(evaluate("SOLUSDT", self.data, next_time)[0]["accepted"])
        asyncio.run(write_fixture(self.market, {"1m": [next_row]}))
        final = Scheduler(self.market, store, "SOLUSDT").tick(next_time + 1)
        self.assertEqual(final["counts"]["T1"]["accepted"], 0)
        self.assertEqual(final["reasons"]["T1"]["DUPLICATE_CANDIDATE"], 1)

    def test_catchup_expired_candidates_never_enter_execution_feed(self):
        store = self.store("runtime.sqlite")
        scheduler = Scheduler(self.market, store, "SOLUSDT")
        scheduler.tick(BOUNDARY + 1)
        report = scheduler.tick(AS_OF + 600_000)
        self.assertEqual(report["counts"]["T1"]["accepted"], 0)
        self.assertIn("EXPIRED_DURING_CATCHUP", report["reasons"]["T1"])
        self.assertEqual(store.accepted_after(now_ms=AS_OF + 600_000), [])

    def test_atomic_failure_does_not_advance_cursor(self):
        store = self.store("atomic.sqlite")
        store.connection.execute("""CREATE TRIGGER reject_snapshot BEFORE INSERT ON snapshots
                                   BEGIN SELECT RAISE(ABORT,'injected snapshot failure'); END""")
        scheduler = Scheduler(self.market, store, "SOLUSDT")
        with self.assertRaises(sqlite3.IntegrityError):
            scheduler.tick(AS_OF + 1)
        self.assertIsNone(store.checkpoint("SOLUSDT"))
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
        store.connection.execute("DROP TRIGGER reject_snapshot")
        self.assertEqual(scheduler.tick(AS_OF + 1)["counts"]["T1"]["accepted"], 1)

    def test_foreign_symbol_source_and_store_mode_refused(self):
        store = self.store("foreign.sqlite")
        with self.assertRaises(ValueError):
            Scheduler(self.market, store, "SUIUSDT").tick(AS_OF + 1)
        replay = self.store("replay.sqlite")
        scheduler = Scheduler(self.market, replay, "SOLUSDT")
        scheduler.replay(AS_OF, AS_OF)
        with self.assertRaises(ValueError):
            scheduler.tick(AS_OF + 1)
        before = self.market.read_bytes()
        with self.assertRaises(ValueError):
            Scheduler(self.market, StrategyStore(self.market), "SOLUSDT")
        self.assertEqual(before, self.market.read_bytes())
        with closing(sqlite3.connect(self.market)) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='decisions'").fetchone())

    def test_empty_database_ownership_still_enforced(self):
        empty = self.root / "empty-sol.sqlite"
        asyncio.run(write_fixture(empty, {}))
        with self.assertRaises(ValueError):
            Scheduler(empty, self.store("empty-strategy.sqlite"), "SUIUSDT").tick(AS_OF + 1)

    def test_future_rows_excluded_and_snapshots_record_temporal_bounds(self):
        store = self.store("bounds.sqlite")
        Scheduler(self.market, store, "SOLUSDT").replay(BOUNDARY - 1, AS_OF)
        snapshots = [json.loads(row[0]) for row in store.connection.execute("SELECT payload_json FROM snapshots")]
        for snapshot in snapshots:
            for source in snapshot["source"].values():
                self.assertLessEqual(source["last_close"], snapshot["as_of_ms"])
                self.assertLessEqual(source["bars"], 120)
                self.assertEqual(len(source["sha256"]), 64)

    def test_gap_prevents_signal_then_repair_recovers_on_later_event(self):
        with closing(sqlite3.connect(self.market)) as conn:
            missing = self.data["1h"][-3]
            conn.execute("DELETE FROM market_klines WHERE interval='1h' AND open_time=?", (missing["open_time"],))
            conn.commit()
        store = self.store("gap.sqlite")
        scheduler = Scheduler(self.market, store, "SOLUSDT")
        first = scheduler.tick(BOUNDARY + 1)
        self.assertEqual(first["regimes"], {"DATA_NOT_READY": 1})
        asyncio.run(write_fixture(self.market, {"1h": [missing]}))
        later = scheduler.tick(AS_OF + 1)
        self.assertEqual(later["counts"]["T1"]["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
