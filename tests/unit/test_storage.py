import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from solbot.storage.database import Database, DatabaseUnavailable
from solbot.storage.models import INTERVAL_MS
from test_models import START, candle


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "market.sqlite3"
        self.db = Database(self.path)
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.close()
        self.temporary.cleanup()

    def rows(self, query, params=()):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params)]

    async def test_six_tables_wal_and_idempotent_migration(self):
        self.assertEqual({row["name"] for row in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}, {
            "market_klines", "market_sync_state", "market_data_gaps", "market_connection_events",
            "system_health_snapshots", "schema_migrations"})
        self.assertEqual(self.db.summary()["journal_mode"], "wal")
        self.assertEqual(self.db.summary()["schema_version"], 1)
        await self.db.start()
        await self.db.close()
        await self.db.start()
        self.assertEqual(len(self.rows("SELECT * FROM schema_migrations")), 1)
        self.assertEqual(set(self.db.summary()["intervals"]), set(INTERVAL_MS))

    async def test_duplicates_atomic_batches_and_open_candles(self):
        self.assertEqual(await self.db.write_klines([candle(), candle()]), 1)
        self.assertEqual(await self.db.write_klines([candle()]), 0)
        self.assertEqual(await self.db.write_klines([candle(source="ws", is_closed=False, close="100.50")]), 0)
        self.assertEqual(self.db.latest("1m")["close"], "101.00")
        with self.assertRaises(ValueError):
            await self.db.write_klines([candle(index=1), candle(index=2, high="0")])
        self.assertEqual(self.db.summary()["intervals"]["1m"]["count"], 1)
        self.assertTrue(self.db.latest("1m")["is_closed"])

    async def test_rest_is_authoritative_and_conflicts_are_audited(self):
        await self.db.write_klines([candle(source="ws")])
        self.assertEqual(self.db.unconfirmed_open_times("1m", START, START), [START])
        self.assertEqual(await self.db.write_klines([candle(close="100.50")]), 1)
        self.assertEqual(await self.db.write_klines([candle(source="ws", close="100.75")]), 0)
        self.assertEqual(self.db.latest("1m")["close"], "100.50")
        self.assertEqual(self.db.unconfirmed_open_times("1m", START, START), [])
        events = self.rows("SELECT * FROM market_connection_events ORDER BY id")
        self.assertEqual([json.loads(row["detail"])["resolution"] for row in events], ["rest_authoritative", "ignored_ws"])
        self.assertTrue(all(row["kind"] == "kline_conflict" for row in events))

    async def test_equivalent_decimals_upgrade_provenance_without_conflict(self):
        await self.db.write_klines([candle(source="ws")])
        self.assertEqual(await self.db.write_klines([candle(close="101.0")]), 1)
        self.assertEqual(await self.db.write_klines([candle(close="101.000")]), 0)
        self.assertEqual(self.db.latest("1m")["source"], "rest")
        self.assertEqual(self.rows("SELECT * FROM market_connection_events"), [])

    async def test_sql_failure_rolls_back_entire_batch_and_conflict_audit(self):
        await self.db.write_klines([candle(source="ws")])
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"""CREATE TRIGGER reject_second BEFORE INSERT ON market_klines
                WHEN NEW.open_time={START + 60_000} BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.write_klines([candle(close="100.50"), candle(index=1)])
        self.assertEqual(self.db.latest("1m")["source"], "ws")
        self.assertEqual(self.db.latest("1m")["close"], "101.00")
        self.assertEqual(self.rows("SELECT * FROM market_connection_events"), [])

    async def test_fifty_concurrent_writes_and_reads(self):
        async def worker(index):
            await self.db.write_klines([candle(index=index)])
            await self.db.event("test", {"index": index})
            return await asyncio.to_thread(self.db.latest, "1m")
        results = await asyncio.gather(*(worker(index) for index in range(50)))
        self.assertTrue(all(result is not None for result in results))
        self.assertEqual(self.db.summary()["intervals"]["1m"]["count"], 50)
        self.assertEqual(len(self.rows("SELECT * FROM market_connection_events")), 50)

    async def test_queries_bounds_pagination_and_latest(self):
        await self.db.write_klines([candle(index=index) for index in range(10)])
        self.assertEqual([row["open_time"] for row in self.db.klines("1m", 2)], [START + 8*60_000, START + 9*60_000])
        self.assertEqual([row["open_time"] for row in self.db.klines("1m", 2, start=START + 2*60_000)], [START + 2*60_000, START + 3*60_000])
        self.assertEqual(self.db.open_times("1m", START + 60_000, START + 2*60_000), [START + 60_000, START + 2*60_000])
        self.assertEqual(self.db.klines("1m", end=START)[0]["open_time"], START)
        self.assertIsNone(self.db.latest("4h"))
        with self.assertRaises(ValueError):
            self.db.klines("1m", 1002)

    async def test_gap_lifecycle_and_checked_history(self):
        await self.db.replace_gaps("1m", [(START, START + 120_000)], START, START + 300_000)
        initial_id = self.db.gaps()[0]["id"]
        await self.db.replace_gaps("1m", [(START, START + 120_000)], START, START + 300_000)
        self.assertEqual(self.db.gaps()[0]["id"], initial_id)
        status = self.db.summary()["intervals"]["1m"]
        self.assertEqual((status["gap_count"], status["missing_bars"]), (1, 3))
        self.assertEqual(status["checked_start"], START)
        await self.db.set_sync("1m", START + 360_000, "complete")
        await self.db.replace_gaps("1m", [], START, START + 300_000)
        self.assertEqual(self.db.gaps(), [])
        history = self.db.gap_history()
        self.assertEqual(history[0]["status"], "resolved")
        self.assertIsNotNone(history[0]["resolved_at"])
        self.assertEqual(self.db.summary()["intervals"]["1m"]["sync_status"], "complete")

    async def test_partial_gap_scan_preserves_unchecked_missing_data(self):
        await self.db.replace_gaps("1m", [(START, START + 600_000)], START, START + 600_000)
        await self.db.replace_gaps("1m", [], START + 240_000, START + 360_000)
        self.assertEqual([(row["start_time"], row["end_time"]) for row in self.db.gaps()], [
            (START, START + 180_000), (START + 420_000, START + 600_000)])
        self.assertEqual(self.db.summary()["intervals"]["1m"]["missing_bars"], 8)
        self.assertEqual(len(self.db.gaps(interval="1m", limit=1, offset=1)), 1)

    async def test_remaining_gap_pieces_keep_first_discovery_timestamp(self):
        first_seen, checked_later = START + 1_000_000, START + 2_000_000
        with patch("solbot.storage.database.utc_ms", return_value=first_seen):
            await self.db.replace_gaps("1m", [(START, START + 600_000)], START, START + 900_000)
        with patch("solbot.storage.database.utc_ms", return_value=checked_later):
            # Repair two interior bars, preserve unchecked edges, and discover
            # a separate new hole inside the checked window.
            await self.db.replace_gaps("1m", [(START + 360_000, START + 480_000),
                                             (START + 780_000, START + 780_000)],
                                       START + 240_000, START + 840_000)
        remaining = self.db.gaps()
        self.assertEqual([(row["start_time"], row["end_time"], row["detected_at"])
                          for row in remaining], [
            (START, START + 180_000, first_seen),
            (START + 360_000, START + 480_000, first_seen),
            (START + 780_000, START + 780_000, checked_later),
        ])
        self.assertTrue(all(row["last_checked_at"] == checked_later for row in remaining))

    async def test_merged_gap_keeps_earliest_overlapping_discovery(self):
        with patch("solbot.storage.database.utc_ms", return_value=START + 1_000_000):
            await self.db.replace_gaps("1m", [(START, START + 60_000)], START, START + 60_000)
        with patch("solbot.storage.database.utc_ms", return_value=START + 2_000_000):
            await self.db.replace_gaps("1m", [(START + 180_000, START + 240_000)],
                                       START + 180_000, START + 240_000)
        with patch("solbot.storage.database.utc_ms", return_value=START + 3_000_000):
            await self.db.replace_gaps("1m", [(START, START + 240_000)], START, START + 240_000)
        self.assertEqual(len(self.db.gaps()), 1)
        self.assertEqual(self.db.gaps()[0]["detected_at"], START + 1_000_000)

    async def test_bad_gap_ranges_do_not_change_state(self):
        for gaps in ([(START + 1, START + 60_000)], [(START, START + 120_000), (START + 60_000, START + 180_000)], [(START, START + 600_000)]):
            with self.subTest(gaps=gaps), self.assertRaises(ValueError):
                await self.db.replace_gaps("1m", gaps, START, START + 300_000)
        self.assertEqual(self.db.gaps(), [])

    async def test_metadata_retention_and_invalid_json(self):
        with patch("solbot.storage.database.EVENT_RETENTION", 3), patch("solbot.storage.database.SNAPSHOT_RETENTION", 2):
            for index in range(6):
                await self.db.event("test", {"index": index})
                await self.db.snapshot({"index": index})
        self.assertEqual(len(self.rows("SELECT * FROM market_connection_events")), 3)
        self.assertEqual(len(self.rows("SELECT * FROM system_health_snapshots")), 2)
        with self.assertRaises(ValueError):
            await self.db.snapshot({"bad": float("nan")})
        with self.assertRaises(ValueError):
            await self.db.event("test", "x" * 70_000)

    async def test_backup_restores_wal_content_and_remains_usable(self):
        await self.db.write_klines([candle(index=index) for index in range(20)])
        target = Path(self.temporary.name) / "backups" / "market.sqlite3"
        await self.db.backup(target)
        with closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_klines").fetchone()[0], 20)
        await self.db.write_klines([candle(index=20)])
        self.assertEqual(self.db.summary()["intervals"]["1m"]["count"], 21)
        with self.assertRaises(ValueError):
            await self.db.backup(self.path)

    async def test_cancellation_does_not_release_write_channel_early(self):
        entered, release = threading.Event(), threading.Event()
        later_entered = threading.Event()
        def blocked():
            entered.set()
            release.wait(timeout=5)
        first = asyncio.create_task(self.db._serialized(blocked))
        await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        second = asyncio.create_task(self.db._serialized(later_entered.set))
        await asyncio.sleep(0.03)
        self.assertFalse(later_entered.is_set())
        first.cancel()  # Repeated cancellation must not unlock the writer.
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await second
        self.assertTrue(later_entered.is_set())

    async def test_closed_database_fails_explicitly_and_can_restart(self):
        await self.db.close()
        with self.assertRaises(DatabaseUnavailable):
            self.db.summary()
        with self.assertRaises(DatabaseUnavailable):
            await self.db.write_klines([candle()])
        await self.db.start()
        self.assertTrue(self.db.available)

    async def test_future_schema_version_is_rejected(self):
        await self.db.close()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA user_version=999")
        with self.assertRaises(DatabaseUnavailable):
            await self.db.start()
        self.assertFalse(self.db.available)


if __name__ == "__main__":
    unittest.main()
