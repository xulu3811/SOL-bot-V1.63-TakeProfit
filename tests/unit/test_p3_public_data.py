"""Offline P3 public-source synchronization and independent Decimal references."""
import asyncio
from collections import deque
from contextlib import closing
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from solbot.market.depth_collector import DepthCollector, DepthRest
from solbot.market.rest import AccessBlocked, MarketDataError
from solbot.market.trade_collector import BookTicker, TradeWindow
from solbot.mathematics.market_features import MarketFeatures, input_hash
from solbot.mathematics.microstructure import depth_imbalance, microprice, spread_metrics
from solbot.mathematics.orderbook import OrderBook, SequenceGap
from solbot.storage.microstructure import MicrostructureStore

NOW = 1_794_355_200_000


def snapshot(uid=100, count=25):
    return {"lastUpdateId": uid,
            "bids": [[str(Decimal(100) - Decimal(index) / 10), "3"] for index in range(count)],
            "asks": [[str(Decimal(101) + Decimal(index) / 10), "1"] for index in range(count)]}


def depth(first=100, last=101, at=NOW, symbol="SOLUSDT", bids=None, asks=None):
    return {"e": "depthUpdate", "s": symbol, "E": at, "U": first, "u": last,
            "b": bids if bids is not None else [], "a": asks if asks is not None else []}


def trade(number=1, at=NOW, price="100", quantity="1", buyer_maker=False, symbol="SOLUSDT"):
    return {"e": "aggTrade", "s": symbol, "E": at, "T": at, "a": number,
            "p": price, "q": quantity, "m": buyer_maker, "f": number * 2, "l": number * 2 + 1}


def ticker(uid=101, symbol="SOLUSDT"):
    return {"u": uid, "s": symbol, "b": "100", "B": "3", "a": "101", "A": "1"}


class PublicMathTests(unittest.TestCase):
    def test_microprice_spread_imbalance_match_fraction_reference(self):
        self.assertEqual(microprice("100", "101", "3", "1"), Decimal("100.75"))
        expected = Fraction(101 - 100) / Fraction(101 + 100, 2) * 10000
        with localcontext() as context:
            context.prec = 40
            self.assertEqual(spread_metrics("100", "101")["spread_bps"], Decimal(expected.numerator) / Decimal(expected.denominator))
            self.assertEqual(depth_imbalance([["100", "3"], ["99", "1"]], [["101", "1"], ["102", "1"]]), Decimal(1) / Decimal(3))

    def test_invalid_market_numbers_are_unavailable(self):
        for arguments in [("100", "99", "1", "1"), ("100", "101", "0", "0"),
                          ("NaN", "101", "1", "1"), (100.0, "101", "1", "1"),
                          ("100", "101", "-1", "1")]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                microprice(*arguments)

    def test_snapshot_bridge_duplicates_overlap_and_gap_reset(self):
        book = OrderBook("SOLUSDT")
        book.load_snapshot(snapshot(), NOW)
        self.assertFalse(book.synced)
        self.assertFalse(book.apply(depth(90, 100), NOW))
        self.assertFalse(book.synced)
        self.assertTrue(book.apply(depth(99, 102, bids=[["100", "4"]]), NOW))
        self.assertTrue(book.synced)
        self.assertTrue(book.apply(depth(102, 104, at=NOW + 1), NOW + 1))
        self.assertEqual(book.update_id, 104)
        self.assertFalse(book.apply(depth(102, 104), NOW + 2))
        with self.assertRaises(SequenceGap):
            book.apply(depth(106, 107), NOW + 3)
        self.assertFalse(book.synced)
        self.assertIsNone(book.update_id)
        self.assertEqual(book.bids, {})

    def test_deletion_unknown_zero_and_memory_limit(self):
        book = OrderBook("SOLUSDT", max_levels=25)
        book.load_snapshot(snapshot(), NOW)
        book.apply(depth(bids=[["100", "0"], ["1", "0"]]), NOW)
        self.assertNotIn(Decimal(100), book.bids)
        self.assertNotIn(Decimal(1), book.bids)
        with self.assertRaises(SequenceGap):
            book.apply(depth(102, 102, bids=[["99.99", "2"], ["99.98", "2"]]), NOW)
        self.assertFalse(book.synced)

    def test_top_depth_does_not_claim_unknown_snapshot_tail(self):
        book = OrderBook("SOLUSDT")
        book.load_snapshot(snapshot(), NOW)
        remove = [[str(Decimal(100) - Decimal(index) / 10), "0"] for index in range(10)]
        remove += [[str(Decimal(90) - Decimal(index) / 10), "1"] for index in range(10)]
        book.apply(depth(bids=remove), NOW)
        with self.assertRaises(SequenceGap):
            book.levels(20)
        self.assertFalse(book.synced)

    def test_symbol_mismatch_does_not_mutate_book(self):
        book = OrderBook("SOLUSDT")
        book.load_snapshot(snapshot(), NOW)
        with self.assertRaises(ValueError):
            book.apply(depth(symbol="SUIUSDT"), NOW)
        self.assertEqual(book.update_id, 100)
        self.assertFalse(book.synced)

    def test_trade_ratio_volume_and_volatility_independent_reference(self):
        window = TradeWindow("SOLUSDT")
        window.add(trade(1, NOW, "100", "2"), NOW)
        window.add(trade(2, NOW + 1000, "110", "1", True), NOW + 1000)
        window.add(trade(3, NOW + 2000, "99", "1"), NOW + 2000)
        result = window.features(NOW + 2000)
        with localcontext() as context:
            context.prec = 40
            self.assertEqual(Decimal(result["buy_ratio"]), Decimal(299) / Decimal(409))
        self.assertEqual(result["trade_volume"], "4")
        # Simple returns are +0.1,-0.1; population standard deviation is 0.1.
        self.assertEqual(Decimal(result["short_volatility"]), Decimal("0.1"))
        self.assertEqual(result["trade_count"], 3)
        self.assertEqual(result["raw_trade_count"], 6)

    def test_trade_gap_duplicate_capacity_time_and_fresh_window(self):
        window = TradeWindow("SOLUSDT", window_ms=1000, max_trades=2)
        self.assertTrue(window.add(trade(1), NOW))
        self.assertFalse(window.add(trade(1), NOW))
        window.add(trade(2, NOW + 100), NOW + 100)
        window.add(trade(3, NOW + 200), NOW + 200)
        self.assertEqual(len(window.trades), 2)
        self.assertFalse(window.features(NOW + 200)["trade_window_complete"])
        window.add(trade(5, NOW + 300), NOW + 300)
        self.assertEqual(window.features(NOW + 300)["trade_count"], 1)
        self.assertEqual(window.gaps, 1)
        regressed = trade(6, NOW + 400)
        regressed["T"] = NOW - 10000
        with self.assertRaisesRegex(ValueError, "execution time regressed"):
            window.add(regressed, NOW + 400)
        self.assertEqual(window.features(NOW + 1400)["trade_volume"], "0")

    def test_book_ticker_missing_E_remains_null_and_rejects_other_symbol(self):
        book_ticker = BookTicker("SOLUSDT")
        book_ticker.add(ticker(), NOW)
        self.assertIsNone(book_ticker.value["exchange_time_ms"])
        self.assertEqual(book_ticker.value["received_time_ms"], NOW)
        with self.assertRaises(ValueError):
            book_ticker.add(ticker(102, "SUIUSDT"), NOW)
        self.assertEqual(book_ticker.value["update_id"], 101)

    def test_feature_ready_and_stale_snapshot_hash(self):
        book, trades, ticks = OrderBook("SOLUSDT"), TradeWindow("SOLUSDT", window_ms=1000), BookTicker("SOLUSDT")
        book.load_snapshot(snapshot(), NOW)
        trades.add(trade(1, NOW), NOW)
        trades.add(trade(2, NOW + 500), NOW + 500)
        trades.add(trade(3, NOW + 1100), NOW + 1100)
        ticks.add(ticker(), NOW + 1100)
        book.apply(depth(at=NOW + 1100), NOW + 1100)
        features = MarketFeatures(book, trades, ticks)
        rules = {"symbol": "SOLUSDT", "status": "TRADING", "isSpotTradingAllowed": True}
        result = features.snapshot(NOW + 1100, connected=True, rules=rules)
        self.assertTrue(result["ready"], result["reasons"])
        self.assertEqual(result["input_hash"], input_hash(result))
        self.assertIsNone(result["book_ticker_exchange_time_ms"])
        features.quote_times = deque(maxlen=2)
        for tick_time in (NOW + 100, NOW + 200, NOW + 300):
            features.quote_updated(tick_time)
        censored = features.snapshot(NOW + 1100, connected=True, rules=rules)
        self.assertFalse(censored["ready"])
        self.assertIn("QUOTE_WINDOW_CAPACITY_LOSS", censored["reasons"])
        future_ticker = ticker(102)
        future_ticker["E"] = NOW + 100000
        ticks.add(future_ticker, NOW + 1100)
        self.assertIn("BOOK_TICKER_EVENT_STALE", features.snapshot(NOW + 1100, connected=True, rules=rules)["reasons"])
        stale = features.snapshot(NOW + 20000, connected=True, rules=rules)
        self.assertFalse(stale["ready"])
        self.assertIn("DEPTH_EVENT_STALE", stale["reasons"])


class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "micro.sqlite"

    def tearDown(self):
        self.directory.cleanup()

    def row(self, at=NOW, symbol="SOLUSDT"):
        value = {"symbol": symbol, "feature_generated_time_ms": at, "ready": False, "reasons": ["fixture"]}
        value["input_hash"] = input_hash(value)
        return value

    def test_latest_readonly_sampling_retention_and_hash_tamper(self):
        store = MicrostructureStore(self.path, "SOLUSDT", max_rows=3, history_interval_ms=1000)
        for index in range(10):
            store.append(self.row(NOW + index * 1000))
        self.assertEqual(len(store.history()), 3)
        reader = MicrostructureStore(self.path, "SOLUSDT", read_only=True)
        self.assertEqual(reader.latest()["feature_generated_time_ms"], NOW + 9000)
        with self.assertRaises(PermissionError):
            reader.append(self.row())
        with self.assertRaises(ValueError):
            store.append(self.row(NOW + 20000, "SUIUSDT"))
        invalid = self.row(NOW + 20000)
        invalid["ready"] = True
        with self.assertRaises(ValueError):
            store.append(invalid)
        store.append(self.row(NOW + 8 * 86400000))
        self.assertEqual(len(store.history()), 1)

    def test_foreign_database_and_symbol_rejected_without_schema_change(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE market_klines(id INTEGER)")
            before = list(db.execute("SELECT name,sql FROM sqlite_master"))
        with self.assertRaises(ValueError):
            MicrostructureStore(self.path, "SOLUSDT")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(list(db.execute("SELECT name,sql FROM sqlite_master")), before)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        other = Path(self.directory.name) / "sui.sqlite"
        MicrostructureStore(other, "SUIUSDT")
        with self.assertRaises(ValueError):
            MicrostructureStore(other, "SOLUSDT")


class PublicCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = MicrostructureStore(Path(self.directory.name) / "micro.sqlite", "SOLUSDT")

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_depth_rest_allowlist_retry_after_and_terminal_access(self):
        calls, delays = [], []
        outcomes = [429, 503, 200]
        def handler(request):
            calls.append(request)
            code = outcomes.pop(0)
            return httpx.Response(code, json=snapshot(), headers={"Retry-After": "7"} if code == 429 else {})
        clock = [0.0]
        async def sleep(delay):
            delays.append(delay)
            clock[0] += delay
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with DepthRest("SOLUSDT", client=client, sleep=sleep, monotonic=lambda: clock[0], weight_per_minute=1000000, jitter=lambda: 0) as rest:
                self.assertEqual((await rest.depth_snapshot())["lastUpdateId"], 100)
                with self.assertRaises(ValueError):
                    await rest.request("/api/v3/order")
        self.assertIn(7.0, delays)
        self.assertTrue(all(request.url.host == "data-api.binance.vision" and request.url.path == "/api/v3/depth" for request in calls))
        self.assertTrue(all(request.url.params["symbol"] == "SOLUSDT" and request.url.params["limit"] == "500" for request in calls))
        for status in (418, 451):
            calls.clear()
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(status))) as client:
                async with DepthRest("SOLUSDT", client=client) as rest:
                    for _ in range(2):
                        with self.assertRaises(AccessBlocked):
                            await rest.depth_snapshot()
            self.assertEqual(len(calls), 1)

    async def test_reader_buffers_before_snapshot_then_bridges_and_cleans_up(self):
        collector = DepthCollector(self.store)
        queue = asyncio.Queue(maxsize=1024)
        queue.put_nowait((depth(100, 101), NOW))
        rest = type("FakeRest", (), {})()
        rest.depth_snapshot = AsyncMock(side_effect=[snapshot(90), snapshot(100)])
        with patch("solbot.market.depth_collector.utc_ms", return_value=NOW):
            task = asyncio.create_task(collector._depth_flow(rest, queue))
            for _ in range(10):
                await asyncio.sleep(0)
                if collector.book.synced:
                    break
            self.assertTrue(collector.book.synced)
            self.assertEqual(rest.depth_snapshot.await_count, 2)
            queue.put_nowait((depth(103, 104), NOW))
            with self.assertRaises(SequenceGap):
                await task
        self.assertFalse(collector.book.synced)

    async def test_actual_connection_loop_reconnects_on_sequence_gap(self):
        collector = DepthCollector(self.store)
        entered = asyncio.Event()
        calls = 0
        async def session(rest):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise SequenceGap("fixture missing updates")
            entered.set()
            await asyncio.Future()
        collector._session = session
        with patch("solbot.market.depth_collector.asyncio.sleep", new_callable=AsyncMock):
            task = asyncio.create_task(collector._connection_loop(object()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            self.assertEqual(collector.reconnects, 1)
            self.assertEqual(collector.sequence_resets, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_reader_routes_three_symbol_streams_and_preserves_no_E(self):
        events = [
            {"stream": "solusdt@aggTrade", "data": trade()},
            {"stream": "solusdt@bookTicker", "data": ticker()},
            {"stream": "solusdt@depth@100ms", "data": depth()},
        ]
        class Stream:
            async def recv(self):
                if events:
                    return json.dumps(events.pop(0))
                raise ConnectionError("fixture disconnect")
        collector = DepthCollector(self.store)
        queue = asyncio.Queue(maxsize=1024)
        with patch("solbot.market.depth_collector.utc_ms", return_value=NOW), self.assertRaises(ConnectionError):
            await collector._reader(Stream(), queue)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(collector.trades.last_id, 1)
        self.assertIsNone(collector.ticker.value["exchange_time_ms"])
        self.assertFalse(collector.book.synced)

    async def test_terminal_block_stays_unavailable_without_retry(self):
        collector = DepthCollector(self.store)
        collector._session = AsyncMock(side_effect=AccessBlocked("fixture 451"))
        collector._persist = AsyncMock()
        task = asyncio.create_task(collector._connection_loop(object()))
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertTrue(collector.blocked)
        self.assertEqual(collector._session.await_count, 1)
        self.assertIn("PUBLIC_ACCESS_BLOCKED", collector.snapshot()["reasons"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
