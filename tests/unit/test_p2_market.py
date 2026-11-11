"""P2 symbol-isolation regressions. All network inputs are local fakes."""

import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from solbot.config import Settings
from solbot.main import create_app
from solbot.market import Backfill, BinanceRest, Collector, MarketUnavailable
from solbot.storage.database import APPLICATION_IDS, Database, DatabaseUnavailable
from solbot.storage.models import INTERVAL_MS, Kline
from solbot.storage.schema import MIGRATIONS

START = 1_767_225_600_000


def row(interval="1m", opened=START, price="100"):
    return [opened, price, price, price, price, "10", opened + INTERVAL_MS[interval] - 1,
            "1000", 20, "4", "400", "0"]


def candle(symbol="SOLUSDT", interval="1m", opened=START, price="100"):
    return Kline.from_rest(row(interval, opened, price), interval, opened + INTERVAL_MS[interval], symbol=symbol)


def ws(symbol, interval="1m", opened=START):
    item = candle(symbol, interval, opened)
    return {"stream": f"{symbol.lower()}@kline_{interval}", "data": {
        "s": symbol, "e": "kline", "E": item.close_time + 1, "k": {
            "s": symbol, "i": interval, "t": opened, "T": item.close_time,
            "o": item.open, "h": item.high, "l": item.low, "c": item.close,
            "v": item.volume, "q": item.quote_volume, "n": item.trade_count,
            "V": item.taker_buy_base_volume, "Q": item.taker_buy_quote_volume, "x": True,
        },
    }}


class SymbolConfigurationTests(unittest.TestCase):
    def test_sui_defaults_and_environment_remain_isolated_from_sol(self):
        self.assertEqual(Settings().symbol, "SOLUSDT")
        self.assertEqual(Settings().port, 8000)
        self.assertEqual(Settings(symbol="SUIUSDT").db_path.name, "sui-market.sqlite")
        self.assertEqual(Settings(symbol="SUIUSDT").port, 8001)
        with patch.dict("os.environ", {"SOLBOT_SYMBOL": "SUIUSDT", "SOLBOT_PORT": "8009", "SOLBOT_DB_PATH": "sui.sqlite"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual((settings.symbol, settings.port, settings.db_path), ("SUIUSDT", 8009, Path("sui.sqlite")))

    def test_only_reviewed_symbols_are_accepted(self):
        for symbol in ("BTCUSDT", "suiusdt", "SUIUSDT' OR 1=1", "", None):
            with self.subTest(symbol=symbol):
                for factory in (lambda: Settings(symbol=symbol), lambda: BinanceRest(symbol),
                                lambda: Database("unused.sqlite", symbol=symbol)):
                    with self.assertRaises(ValueError):
                        factory()

    def test_models_accept_sui_but_validate_expected_symbol(self):
        self.assertEqual(candle().symbol, "SOLUSDT")
        self.assertEqual(candle("SUIUSDT").symbol, "SUIUSDT")
        self.assertEqual(Kline.from_ws(ws("SUIUSDT"), symbol="SUIUSDT").symbol, "SUIUSDT")
        with self.assertRaisesRegex(ValueError, "configured market"):
            Kline.from_ws(ws("SOLUSDT"), symbol="SUIUSDT")


class SymbolStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sol = Database(self.root / "sol.sqlite")
        self.sui = Database(self.root / "sui.sqlite", "SUIUSDT")
        await self.sol.start()
        await self.sui.start()

    async def asyncTearDown(self):
        await self.sol.close()
        await self.sui.close()
        self.temp.cleanup()

    async def test_identical_times_and_intervals_keep_separate_rows_and_sync(self):
        await self.sol.write_klines([candle("SOLUSDT", interval, price="100") for interval in INTERVAL_MS])
        await self.sui.write_klines([candle("SUIUSDT", interval, price="2") for interval in INTERVAL_MS])
        await self.sui.replace_gaps("1m", [(START + 60_000, START + 60_000)], START, START + 120_000)
        await self.sui.set_sync("1m", START + 60_000, "unavailable")
        self.assertEqual(self.sol.gaps(), [])
        self.assertEqual(len(self.sui.gaps()), 1)
        for interval in INTERVAL_MS:
            self.assertEqual(self.sol.latest(interval)["symbol"], "SOLUSDT")
            self.assertEqual(self.sui.latest(interval)["symbol"], "SUIUSDT")
            self.assertEqual(self.sol.latest(interval)["close"], "100")
            self.assertEqual(self.sui.latest(interval)["close"], "2")
        self.assertEqual(self.sui.summary()["symbol"], "SUIUSDT")
        self.assertEqual(self.sol.summary()["intervals"]["1m"]["sync_status"], "never_started")

    async def test_foreign_batches_rejected_atomically_including_open_rows(self):
        with self.assertRaisesRegex(ValueError, "ownership"):
            await self.sui.write_klines([candle("SUIUSDT"), candle("SOLUSDT", opened=START + 60_000)])
        self.assertIsNone(self.sui.latest("1m"))
        with self.assertRaises(ValueError):
            await self.sol.write_klines([replace(candle("SUIUSDT"), is_closed=False)])
        self.assertIsNone(self.sol.latest("1m"))

    async def test_sql_constraint_blocks_foreign_symbol_even_without_application_writer(self):
        await self.sui.write_klines([candle("SUIUSDT")])
        with closing(sqlite3.connect(self.sui.path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE market_klines SET symbol='SOLUSDT'")
            conn.rollback()
            self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], APPLICATION_IDS["SUIUSDT"])
        self.assertEqual(self.sui.latest("1m")["symbol"], "SUIUSDT")

    async def test_empty_database_cannot_be_reopened_as_another_symbol(self):
        await self.sol.close()
        wrong = Database(self.sol.path, "SUIUSDT")
        with self.assertRaises(DatabaseUnavailable):
            await wrong.start()
        self.assertFalse(wrong.available)
        await self.sol.start()
        self.assertEqual(self.sol.summary()["symbol"], "SOLUSDT")
        self.assertIsNone(self.sol.latest("1m"))

    async def test_empty_legacy_v1_schema_is_owned_by_sol_before_any_rows_exist(self):
        path = self.root / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as conn:
            for statement in MIGRATIONS[1]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES(1,?)", (START,))
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        wrong = Database(path, "SUIUSDT")
        with self.assertRaises(DatabaseUnavailable):
            await wrong.start()
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], 0)
        legacy = Database(path)
        await legacy.start()
        try:
            self.assertEqual(legacy.summary()["schema_version"], 1)
            await legacy.write_klines([candle()])
            self.assertEqual(legacy.latest("1m")["symbol"], "SOLUSDT")
        finally:
            await legacy.close()

    async def test_backup_preserves_symbol_ownership(self):
        await self.sui.write_klines([candle("SUIUSDT")])
        target = self.root / "restore.sqlite"
        await self.sui.backup(target)
        with self.assertRaises(DatabaseUnavailable):
            await Database(target).start()
        restored = Database(target, "SUIUSDT")
        await restored.start()
        try:
            self.assertEqual(restored.latest("1m")["symbol"], "SUIUSDT")
        finally:
            await restored.close()

    async def test_sui_gap_repair_never_writes_sol_database(self):
        class Rest:
            symbol = "SUIUSDT"

            async def server_time(self):
                return START + 120_000

            async def klines(self, interval, start, end, limit=1000):
                return [candle("SUIUSDT", interval, opened)
                        for opened in range(start, end + 1, INTERVAL_MS[interval])][:limit]

        rest = Rest()
        gaps = await Backfill(self.sui, rest).run(START, START + 120_000)
        self.assertTrue(all(not value for value in gaps.values()))
        self.assertEqual(len(self.sui.klines("1m")), 2)
        self.assertIsNone(self.sol.latest("1m"))
        with self.assertRaisesRegex(ValueError, "symbols disagree"):
            Backfill(self.sol, rest)

    async def test_collector_rejects_other_symbol_stream_and_db(self):
        collector = Collector(self.sui)
        self.assertEqual(collector.health["symbol"], "SUIUSDT")
        self.assertEqual(len(collector._streams), 5)
        self.assertTrue(all(stream.startswith("suiusdt@") for stream in collector._streams))
        with self.assertRaises(ValueError):
            await collector._handle_message(json.dumps(ws("SOLUSDT")))
        await collector._handle_message(json.dumps(ws("SUIUSDT")))
        self.assertEqual(self.sui.latest("1m")["symbol"], "SUIUSDT")
        self.assertIsNone(self.sol.latest("1m"))
        with self.assertRaisesRegex(ValueError, "symbols disagree"):
            Collector(self.sol, symbol="SUIUSDT")

    async def test_ineligible_sui_is_blocked_before_websocket_subscription(self):
        info_calls = 0

        class Rest:
            symbol = "SUIUSDT"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def server_time(self):
                return START + 60_000

            async def exchange_info(self):
                nonlocal info_calls
                info_calls += 1
                raise MarketUnavailable("SUIUSDT is not an active spot market")

        def forbidden_ws(*args, **kwargs):
            self.fail("ineligible SUI must not open a stream")

        collector = Collector(self.sui, rest_factory=Rest, ws_connect=forbidden_ws)
        task = asyncio.create_task(collector.run())

        async def wait_for_block():
            while collector.health["market_status"] != "BLOCKED":
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_block(), timeout=2)
        self.assertEqual(collector.health["status"], "unavailable")
        self.assertEqual(info_calls, 1)
        self.assertIsNone(self.sui.latest("1m"))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class SymbolRestTests(unittest.IsolatedAsyncioTestCase):
    async def test_sui_rest_requests_and_typed_candles_use_only_configured_symbol(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/time"):
                payload = {"serverTime": START + 60_000}
            elif request.url.path.endswith("/exchangeInfo"):
                payload = {"symbols": [{"symbol": "SUIUSDT", "baseAsset": "SUI", "quoteAsset": "USDT",
                                        "status": "TRADING", "isSpotTradingAllowed": True, "filters": []}]}
            elif request.url.path.endswith("/bookTicker"):
                payload = {"symbol": "SUIUSDT", "bidPrice": "2", "askPrice": "2.01", "bidQty": "10", "askQty": "10"}
            else:
                payload = [row(price="2")]
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with BinanceRest("SUIUSDT", client=client, weight_per_minute=1_000_000) as rest:
                self.assertEqual((await rest.exchange_info())["symbol"], "SUIUSDT")
                self.assertEqual((await rest.book_ticker())["symbol"], "SUIUSDT")
                self.assertEqual((await rest.klines("1m", START, START))[0].symbol, "SUIUSDT")
                with self.assertRaises(ValueError):
                    await rest.request("/api/v3/klines", {"symbol": "SOLUSDT"})
        self.assertTrue(all(request.url.host == "data-api.binance.vision" for request in requests))
        self.assertTrue(all(request.url.params["symbol"] == "SUIUSDT"
                            for request in requests if not request.url.path.endswith("/time")))

    async def test_invalid_symbol_and_inactive_spot_are_explicitly_blocked(self):
        cases = [httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."}),
                 httpx.Response(200, json={"symbols": [{"symbol": "SUIUSDT", "baseAsset": "SUI",
                     "quoteAsset": "USDT", "status": "BREAK", "isSpotTradingAllowed": True, "filters": []}]}),
                 httpx.Response(200, json={"symbols": [{"symbol": "SOLUSDT", "baseAsset": "SOL",
                     "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True, "filters": []}]})]
        for response in cases:
            with self.subTest(response=response):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                    async with BinanceRest("SUIUSDT", client=client) as rest:
                        with self.assertRaises(MarketUnavailable):
                            await rest.exchange_info()


class SymbolApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_apps_serve_only_their_configured_market_and_fail_independently(self):
        class LocalCollector:
            def __init__(self, db):
                now = time.time_ns() // 1_000_000
                self.health = {"symbol": db.symbol, "status": "available", "ws_connected": True,
                               "last_message_ms": now, "last_by_interval": dict.fromkeys(INTERVAL_MS, now),
                               "backfill_complete": True, "history_gaps": 0, "clock_offset_ms": 0}

            async def run(self):
                await asyncio.Future()

        with tempfile.TemporaryDirectory() as directory:
            sol = create_app(Settings(db_path=Path(directory) / "sol.sqlite"), collector_factory=LocalCollector)
            sui = create_app(Settings(db_path=Path(directory) / "sui.sqlite", symbol="SUIUSDT"), collector_factory=LocalCollector)
            async with sol.router.lifespan_context(sol), sui.router.lifespan_context(sui):
                now = time.time_ns() // 1_000_000
                for app in (sol, sui):
                    for interval, step in INTERVAL_MS.items():
                        await app.state.database.write_klines([candle(app.state.settings.symbol, interval, (now // step - 1) * step)])
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sol), base_url="http://sol.local") as sol_http, \
                           httpx.AsyncClient(transport=httpx.ASGITransport(app=sui), base_url="http://sui.local") as sui_http:
                    for endpoint in ("klines", "latest", "status", "gaps"):
                        response = await sui_http.get(f"/api/v1/market/{endpoint}")
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["symbol"], "SUIUSDT")
                        cross = await sui_http.get(f"/api/v1/market/{endpoint}", params={"symbol": "SOLUSDT"})
                        self.assertEqual(cross.status_code, 422)
                    self.assertEqual((await sol_http.get("/api/v1/market/latest")).json()["kline"]["symbol"], "SOLUSDT")
                    sui.state.collector.health["ws_connected"] = False
                    self.assertEqual((await sui_http.get("/health")).status_code, 503)
                    self.assertEqual((await sol_http.get("/health")).status_code, 200)
                    await sui.state.database.close()
                    self.assertEqual((await sui_http.get("/api/v1/market/latest")).status_code, 503)
                    self.assertEqual((await sol_http.get("/health")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
