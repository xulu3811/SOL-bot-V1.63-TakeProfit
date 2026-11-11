"""Offline integration of the existing Scheduler/math through frozen real-data tooling."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from solbot.mathematics.market_features import input_hash
from solbot.storage.microstructure import MicrostructureStore
from solbot.storage.schema import migrations_for
from solbot.strategy.scheduler import Scheduler, StrategyStore
from test_p2_strategy import AS_OF, BOUNDARY, bars, positive_fixture
from test_p3_pricing import features

SPEC = importlib.util.spec_from_file_location("p3_real_replay", Path(__file__).resolve().parents[2] / "scripts" / "p3-real-replay.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def market_fixture(path, symbol, shift=0, data=None):
    with closing(sqlite3.connect(path)) as db:
        for statement in migrations_for(symbol)[1]:
            db.execute(statement)
        for rows in (positive_fixture(symbol=symbol) if data is None else data).values():
            for row in rows:
                row = dict(row)
                for key in ("open_time", "close_time", "ingested_at", "event_time"):
                    row[key] += shift
                names = list(row)
                db.execute(f"INSERT INTO market_klines ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", list(row.values()))
        db.commit()


def snapshot(symbol="SOLUSDT", now=AS_OF):
    result = features(symbol)
    for key in ("exchange_time_ms", "received_time_ms", "trade_event_time_ms", "trade_received_time_ms", "book_ticker_received_time_ms"):
        result[key] = now - 10
    result["feature_generated_time_ms"] = now
    result["input_hash"] = input_hash(result)
    return result


class RealReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def micro(self, name="micro.sqlite", symbol="SOLUSDT"):
        return MicrostructureStore(self.root / name, symbol)

    def test_asof_generated_time_not_event_time_prevents_lookahead(self):
        store = self.micro()
        store.append(snapshot(now=AS_OF))
        later = snapshot(now=AS_OF + 5000)
        later["exchange_time_ms"] = AS_OF - 1
        later["input_hash"] = input_hash(later)
        store.append(later)
        reader = replay.HistoricalMicro(store.path, "SOLUSDT")
        self.addCleanup(reader.close)
        self.assertEqual(reader.select(AS_OF - 1)[1]["status"], "NO_HISTORICAL_DEPTH")
        used, reference = reader.select(AS_OF)
        self.assertEqual(used["feature_generated_time_ms"], AS_OF)
        self.assertEqual(reference["status"], "AVAILABLE")
        self.assertEqual(reader.select(AS_OF + 3000)[1]["status"], "STALE_HISTORICAL_DEPTH")

    def test_future_source_time_or_corrupt_payload_is_never_used(self):
        store = self.micro()
        data = snapshot()
        data["trade_event_time_ms"] = AS_OF + 1
        data["input_hash"] = input_hash(data)
        store.append(data)
        reader = replay.HistoricalMicro(store.path, "SOLUSDT")
        self.assertEqual(reader.select(AS_OF)[1]["status"], "FUTURE_OR_INVALID_SOURCE_TIME")
        reader.close()
        with closing(sqlite3.connect(store.path)) as db:
            db.execute("UPDATE micro_samples SET input_hash=?", ("0" * 64,))
            db.commit()
        reader = replay.HistoricalMicro(store.path, "SOLUSDT")
        self.addCleanup(reader.close)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            reader.select(AS_OF)

    def test_wrong_symbol_rejected(self):
        store = self.micro()
        with self.assertRaisesRegex(ValueError, "foreign"):
            replay.HistoricalMicro(store.path, "SUIUSDT")

    def test_asof_join_rechecks_trade_and_ticker_age(self):
        for field, age in (("trade_event_time_ms", 15000), ("book_ticker_received_time_ms", 5000)):
            with self.subTest(field=field):
                store = self.micro(field + ".sqlite")
                data = snapshot()
                data[field] = AS_OF - age
                data["input_hash"] = input_hash(data)
                store.append(data)
                reader = replay.HistoricalMicro(store.path, "SOLUSDT")
                try:
                    self.assertEqual(reader.select(AS_OF)[1]["status"], "AVAILABLE")
                    self.assertEqual(reader.select(AS_OF + 1)[1]["status"], "STALE_HISTORICAL_TRADE_OR_TICKER")
                finally:
                    reader.close()

    def sources(self):
        data = self.root / "live"
        data.mkdir()
        # Align the final fixture boundary to a real UTC 6h research entry.
        shift = (-BOUNDARY) % (12 * replay.HOUR)
        for symbol, names in replay.SOURCES.items():
            market_fixture(data / names[0], symbol, shift)
            store = MicrostructureStore(data / names[1], symbol)
            store.append(snapshot(symbol, AS_OF + shift))
        return data

    def test_freeze_includes_committed_wal_and_validates_reuse(self):
        data = self.sources()
        source = sqlite3.connect(data / "market.sqlite")
        self.addCleanup(source.close)
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("UPDATE market_klines SET trade_count=101 WHERE interval='1m'")
        source.commit()
        archive = self.root / "frozen"
        manifest = replay.freeze_sources(data, archive)
        with closing(replay.read_db(archive / "source" / "market.sqlite")) as frozen:
            self.assertEqual(frozen.execute("SELECT trade_count FROM market_klines WHERE interval='1m' LIMIT 1").fetchone()[0], 101)
        self.assertEqual(source.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(manifest, replay.freeze_sources(data, archive))
        with (archive / "source" / "market.sqlite").open("ab") as target:
            target.write(b"modified")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            replay.freeze_sources(data, archive)

    def test_full_two_symbol_pipeline_preserves_sources_and_hashes_all_payloads(self):
        data = self.sources()
        before = {p.name: replay.sha_file(p) for p in data.glob("*.sqlite")}
        result = replay.run(data, self.root / "result.json", self.root / "archive", hours=3, horizon_minutes=1)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["live_authorized"])
        for symbol, report in result["symbols"].items():
            self.assertTrue(report["deterministic"])
            left, right = report["strategy_passes"]
            self.assertEqual(left, right)
            self.assertGreater(left["decisions"]["payload_count"], 0)
            self.assertGreater(report["missing_historical_depth_decisions"], 0)
            self.assertEqual(set(left["report"]["counts"]), {"T1", "R1"})
            math = report["math_passes"][0]
            self.assertEqual(math["coverage"]["AVAILABLE"], 1)
            self.assertEqual(math["output_statuses"]["T1:expected_return:INSUFFICIENT_EVIDENCE"], 1)
            payloads = Path(result["run_dir"]) / f"{symbol}-1" / "math-payloads.jsonl"
            for line in payloads.read_text().splitlines():
                payload = json.loads(line)
                for decision in payload["outputs"].values():
                    self.assertFalse(decision["live_authorized"])
                    self.assertEqual(decision["context"]["symbol"], symbol)
        self.assertGreaterEqual(result["real_close_path_comparison"]["count"], 1)
        self.assertEqual(before, {p.name: replay.sha_file(p) for p in data.glob("*.sqlite")})

    def test_complete_payload_hash_detects_change_with_same_counts(self):
        store = StrategyStore(self.root / "results.sqlite")
        self.addCleanup(store.close)
        market_fixture(self.root / "market.sqlite", "SOLUSDT")
        Scheduler(self.root / "market.sqlite", store, "SOLUSDT").replay(BOUNDARY - 1, AS_OF)
        first = replay.payload_digest(store.connection, "decisions")
        row = store.connection.execute("SELECT id,payload_json FROM decisions LIMIT 1").fetchone()
        changed = json.loads(row[1])
        changed["reason_for_test"] = "changed full payload while count unchanged"
        store.connection.execute("UPDATE decisions SET payload_json=? WHERE id=?", (replay.encoded(changed), row[0]))
        second = replay.payload_digest(store.connection, "decisions")
        self.assertEqual(first["payload_count"], second["payload_count"])
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_sparse_six_hour_comparisons_refresh_scheduler_cache(self):
        data = positive_fixture()
        data["1m"] = bars("1m", [100] * 900)
        data["5m"] = bars("5m", [100] * 300)
        shift = (-BOUNDARY) % (12 * replay.HOUR)
        market_fixture(self.root / "market.sqlite", "SOLUSDT", shift, data)
        end = AS_OF + shift
        result = replay.close_comparison(self.root / "market.sqlite", self.root,
            end - 13 * replay.HOUR, end, horizon_minutes=1)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["skipped"], {})

    def test_output_cannot_alias_source_and_windows_are_bounded(self):
        with self.assertRaises(ValueError):
            replay.run(self.root, self.root / "market.sqlite", self.root / "archive")
        with self.assertRaises(ValueError):
            replay.run(self.root, self.root / "result.json", self.root / "archive", hours=73)


if __name__ == "__main__":
    unittest.main()
