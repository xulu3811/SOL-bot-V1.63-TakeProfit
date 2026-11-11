"""Offline HTTP/lifespan contract checks using real SQLite and a local collector."""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from solbot.config import Settings
from solbot.main import create_app
from solbot.storage.models import INTERVAL_MS, Kline


class LocalCollector:
    def __init__(self, database):
        self.database = database
        self.cancelled = False
        self.crash = asyncio.Event()
        now = int(time.time() * 1000)
        self.health = {
            "status": "available", "ws_connected": True,
            "last_message_ms": now, "last_by_interval": dict.fromkeys(INTERVAL_MS, now),
            "backfill_complete": True, "history_gaps": 0, "reconnects": 0,
            "clock_offset_ms": 0, "errors": [],
        }

    async def run(self):
        try:
            await self.crash.wait()
            raise RuntimeError("injected collector failure")
        finally:
            self.cancelled = True


class MarketApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(
            Settings(db_path=Path(self.directory.name) / "market.sqlite"),
            collector_factory=LocalCollector,
        )
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.db = self.app.state.database
        self.collector = self.app.state.collector
        self.now = int(time.time() * 1000)
        self.starts = {}
        for interval, step in INTERVAL_MS.items():
            first = (self.now // step - 3) * step
            self.starts[interval] = first
            candles = [Kline.from_rest([
                opened, "150.00000001", "152", "149", "151.00000001", "10",
                opened + step - 1, "1510", 20, "5", "755", "0",
            ], interval, self.now) for opened in range(first, first + 3 * step, step)]
            await self.db.write_klines(candles)
            await self.db.replace_gaps(interval, [], first, first + 2 * step)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test.local"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.directory.cleanup()

    async def test_health_and_status_report_all_five_intervals(self):
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ready"])
        self.assertEqual(set(health.json()["intervals"]), set(INTERVAL_MS))
        response = await self.client.get("/api/v1/market/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["storage"]["intervals"]["1m"]["count"], 3)

    async def test_kline_pagination_is_bounded_ordered_and_inclusive(self):
        url = "/api/v1/market/klines"
        first = self.starts["1m"]
        response = await self.client.get(url, params={"interval": "1m", "limit": 2, "start": first})
        page = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["open_time"] for row in page["items"]], [first, first + 60_000])
        self.assertEqual(page["next_start"], first + 120_000)
        self.assertEqual(page["items"][0]["open"], "150.00000001")
        self.assertTrue(all(row["is_closed"] for row in page["items"]))
        second = await self.client.get(url, params={"start": page["next_start"], "limit": 2})
        self.assertEqual(second.json()["count"], 1)
        self.assertIsNone(second.json()["next_start"])
        single = await self.client.get(url, params={"start": first, "end": first})
        self.assertEqual(single.json()["count"], 1)

    async def test_latest_returns_last_closed_candle(self):
        response = await self.client.get("/api/v1/market/latest", params={"interval": "4h"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["available"])
        self.assertEqual(response.json()["kline"]["open_time"], self.starts["4h"] + 2 * INTERVAL_MS["4h"])

    async def test_invalid_queries_are_rejected_before_database_access(self):
        cases = [
            ("klines", {"interval": "2m"}),
            ("klines", {"symbol": "BTCUSDT"}),
            ("klines", {"limit": 1001}),
            ("klines", {"limit": 0}),
            ("klines", {"start": 1_700_000_000}),
            ("klines", {"end": 4_102_444_800_000}),
            ("klines", {"start": self.now, "end": self.now - 1}),
            ("klines", {"interval": "1m'; DROP TABLE klines;--"}),
            ("latest", {"interval": "1d"}),
            ("gaps", {"offset": -1}),
            ("gaps", {"offset": 1_000_001}),
            ("gaps", {"limit": 1001}),
        ]
        for endpoint, parameters in cases:
            with self.subTest(endpoint=endpoint, parameters=parameters):
                response = await self.client.get(f"/api/v1/market/{endpoint}", params=parameters)
                self.assertEqual(response.status_code, 422)

    async def test_disconnection_and_stale_interval_are_unready(self):
        self.collector.health["ws_connected"] = False
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertIn("websocket_disconnected", response.json()["reasons"])
        self.collector.health["ws_connected"] = True
        self.collector.health["last_by_interval"]["4h"] = self.now - 60_000
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertIn("4h:stream_stale", response.json()["reasons"])
        # Diagnostics remain readable when exchange connectivity is degraded.
        self.assertEqual((await self.client.get("/api/v1/market/status")).status_code, 200)

    async def test_stale_closed_candles_are_unready_even_with_fresh_stream(self):
        # Move exchange time forward without introducing any real network calls.
        self.collector.health["clock_offset_ms"] = 3 * INTERVAL_MS["1m"]
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertIn("1m:closed_candles_stale", response.json()["reasons"])

    async def test_history_gaps_are_visible_and_paginated(self):
        step = INTERVAL_MS["1m"]
        first = self.starts["1m"]
        await self.db.replace_gaps("1m", [(first - 3 * step, first - 3 * step),
                                             (first - step, first - step)], first - 3 * step, first + 2 * step)
        response = await self.client.get("/api/v1/market/gaps", params={"interval": "1m", "limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["next_offset"], 1)
        response = await self.client.get("/api/v1/market/gaps", params={"interval": "1m", "limit": 1, "offset": 1})
        self.assertEqual(response.json()["count"], 1)
        self.assertIsNone(response.json()["next_offset"])
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 503)
        self.assertIn("history_gaps", health.json()["reasons"])
        self.assertEqual(health.json()["missing_bars"], 2)

    async def test_collector_crash_is_reported(self):
        self.collector.crash.set()
        await asyncio.sleep(0)
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 503)
        self.assertIn("collector_stopped", health.json()["reasons"])

    async def test_database_failure_is_explicit_503_on_every_read_route(self):
        await self.db.close()
        for path in ["/health", "/api/v1/market/status", "/api/v1/market/klines",
                     "/api/v1/market/latest", "/api/v1/market/gaps"]:
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(self.directory.name, response.text)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_start_failure_preserves_health_and_skips_collector(self):
        class FailedDatabase:
            def __init__(self, path):
                self.closed = False

            async def start(self):
                raise sqlite3.OperationalError("injected unavailable database")

            async def close(self):
                self.closed = True

        def unexpected_collector(database):
            self.fail("collector must not start without a database")

        app = create_app(database_factory=FailedDatabase, collector_factory=unexpected_collector)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
                response = await client.get("/health")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["database"], "unavailable")
        self.assertTrue(app.state.database.closed)

    async def test_lifespan_has_one_collector_and_cancels_it(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(db_path=Path(directory) / "market.sqlite"), collector_factory=LocalCollector)
            async with app.router.lifespan_context(app):
                await asyncio.sleep(0)
                collector = app.state.collector
                self.assertIs(collector.database, app.state.database)
                self.assertFalse(app.state.collector_task.done())
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
                    latest = await client.get("/api/v1/market/latest")
                    self.assertFalse(latest.json()["available"])
                    self.assertIsNone(latest.json()["kline"])
            self.assertTrue(collector.cancelled)
            self.assertTrue(app.state.collector_task.done())

    def test_environment_and_loopback_entrypoint(self):
        with patch.dict("os.environ", {"SOLBOT_DB_PATH": "temporary.sqlite"}):
            self.assertEqual(Settings.from_env().db_path, Path("temporary.sqlite"))
        with patch.dict("os.environ", {"SOLBOT_DB_PATH": ":memory:"}):
            self.assertRaises(ValueError, Settings.from_env)
        from solbot.main import main
        with patch("uvicorn.run") as run:
            main()
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["workers"], 1)


if __name__ == "__main__":
    unittest.main()
