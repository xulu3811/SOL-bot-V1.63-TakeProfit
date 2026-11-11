"""Offline market ingestion tests: no Binance requests or credentials."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import time
import httpx
import unittest
from unittest.mock import AsyncMock, patch
from solbot.market import AccessBlocked, Backfill, BinanceRest, Collector, MarketDataError, detect_gaps, history_start
from solbot.market.backfill import DAY_MS
from solbot.market.collector import STREAMS, WS_URL, ServerShutdown
from solbot.storage.models import INTERVAL_MS, Kline
BASE = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)

def rest_row(open_time, interval='1m'):
    return [open_time, '100', '102', '99', '101', '10', open_time + INTERVAL_MS[interval] - 1, '1010', 20, '5', '505', '0']

def candle(open_time, interval='1m', now=None):
    return Kline.from_rest(rest_row(open_time, interval), interval, now or BASE + 40 * DAY_MS)

def ws_message(open_time, interval='1m', *, closed=False, event_time=None):
    step = INTERVAL_MS[interval]
    event_time = event_time or open_time + (step if closed else 1000)
    return json.dumps({'stream': f'solusdt@kline_{interval}', 'data': {'e': 'kline', 'E': event_time, 's': 'SOLUSDT', 'k': {'s': 'SOLUSDT', 'i': interval, 't': open_time, 'T': open_time + step - 1, 'o': '100', 'h': '102', 'l': '99', 'c': '101', 'v': '10', 'q': '1010', 'n': 20, 'V': '5', 'Q': '505', 'x': closed}}})

class FakeClock:

    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    async def sleep(self, duration):
        self.sleeps.append(duration)
        self.value += duration

class FakeDB:

    def __init__(self, rows=()):
        self.rows = {(row.interval, row.open_time): row for row in rows}
        self.sync = {}
        self.gaps = {}
        self.events = []
        self.snapshots = []
        self.writes = []

    def open_times(self, interval, start, end):
        return sorted((open_time for item_interval, open_time in self.rows if item_interval == interval and start <= open_time <= end))

    def unconfirmed_open_times(self, interval, start, end):
        return [row.open_time for row in self.rows.values() if row.interval == interval and start <= row.open_time <= end and (row.source == 'ws')]

    async def write_klines(self, rows):
        assert all((row.is_closed for row in rows))
        self.writes.append(list(rows))
        for row in rows:
            self.rows[row.interval, row.open_time] = row
        return len(rows)

    async def replace_gaps(self, interval, gaps, start, end):
        assert start <= end
        self.gaps[interval] = list(gaps)

    async def set_sync(self, interval, cursor, status):
        self.sync[interval] = {'cursor': cursor, 'status': status}

    async def event(self, kind, detail):
        self.events.append((kind, detail))

    async def snapshot(self, value):
        self.snapshots.append(value)

class FakeRest:

    def __init__(self, now, transform=None):
        self.now = now
        self.calls = []
        self.transform = transform

    async def server_time(self):
        return self.now

    async def klines(self, interval, start, end, limit=1000):
        self.calls.append((interval, start, end, limit))
        step = INTERVAL_MS[interval]
        rows = [candle(value, interval, self.now) for value in range(start, min(end, start + (limit - 1) * step) + 1, step)]
        return self.transform(interval, start, rows) if self.transform else rows

def make_rest(handler, **kwargs):
    clock = FakeClock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = BinanceRest(client=client, sleep=clock.sleep, monotonic=clock.monotonic, jitter=lambda: 0, weight_per_minute=1000000, **kwargs)
    return (rest, client, clock)

class MarketTests(unittest.IsolatedAsyncioTestCase):

    async def test_stream_reconnect_and_server_shutdown_execute_real_loop(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                db = FakeDB()
                second_connection = asyncio.Event()
                connections = 0

                class Stream:
                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *_):
                        pass

                    async def recv(self):
                        if connections == 1:
                            if shutdown:
                                return json.dumps({"data": {"e": "serverShutdown"}})
                            raise ConnectionError("mock connection lost")
                        second_connection.set()
                        await asyncio.Future()

                def connect(*args, **kwargs):
                    nonlocal connections
                    connections += 1
                    return Stream()

                collector = Collector(db, ws_connect=connect)
                with patch("solbot.market.collector.asyncio.sleep", new_callable=AsyncMock) as sleeper:
                    task = asyncio.create_task(collector._stream_loop())
                    await asyncio.wait_for(second_connection.wait(), timeout=1)
                    self.assertEqual(connections, 2)
                    self.assertEqual(collector.health["reconnects"], 1)
                    self.assertTrue(collector._repair_requested.is_set())
                    if shutdown:
                        sleeper.assert_not_awaited()
                    else:
                        sleeper.assert_awaited_once()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                kinds = [kind for kind, detail in db.events]
                self.assertEqual(kinds.count("ws_connected"), 2)
                self.assertEqual(kinds.count("ws_disconnected"), 2)
                self.assertIn("ws_server_shutdown" if shutdown else "ws_error", kinds)
                self.assertFalse(collector.health["ws_connected"])

    async def test_rotation_finishes_before_configured_lifetime(self):
        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def recv(self):
                await asyncio.Future()

        collector = Collector(FakeDB(), ws_connect=lambda *args, **kwargs: Stream(), rotate_seconds=0.01)
        await asyncio.wait_for(collector._stream_once(), timeout=1)
        with self.assertRaises(ValueError):
            Collector(FakeDB(), rotate_seconds=86400)

    def test_previous_30_full_utc_days_and_interior_gap_detection(self):
        assert history_start(BASE + 19 * 3600000) == BASE - 30 * DAY_MS
        step = 60000
        assert detect_gaps([BASE + step, BASE + step, BASE + 3 * step], BASE, BASE + 4 * step, step) == [(BASE, BASE), (BASE + 2 * step, BASE + 2 * step), (BASE + 4 * step, BASE + 4 * step)]
        assert detect_gaps([], BASE + 1, BASE + step - 1, step) == []

    async def test_rest_timeout_503_429_retry_after_then_success(self):
        outcomes = ['timeout', 503, 429, 200]
        requests = []

        def handler(request):
            requests.append(request)
            outcome = outcomes.pop(0)
            if outcome == 'timeout':
                raise httpx.ReadTimeout('fixture timeout', request=request)
            return httpx.Response(outcome, json={'serverTime': BASE}, headers={'Retry-After': '7'} if outcome == 429 else {})
        rest, client, clock = make_rest(handler)
        async with client, rest:
            assert await rest.server_time() == BASE
        assert len(requests) == 4
        assert all((str(request.url) == 'https://data-api.binance.vision/api/v3/time' for request in requests))
        assert clock.sleeps == [0.5, 1.0, 7.0]

    async def test_access_block_is_terminal_even_on_subsequent_calls(self):
        for status in [418, 451]:
            with self.subTest(status=status):
                await self._case_test_access_block_is_terminal_even_on_subsequent_calls(status)

    async def _case_test_access_block_is_terminal_even_on_subsequent_calls(self, status):
        calls = []
        rest, client, clock = make_rest(lambda request: calls.append(request) or httpx.Response(status))
        async with client, rest:
            for _ in range(2):
                with self.assertRaises(AccessBlocked):
                    await rest.server_time()
        assert len(calls) == 1
        assert clock.sleeps == []

    async def test_retries_are_bounded_and_final_retry_after_persists(self):
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(429, headers={'Retry-After': '11'})
        rest, client, clock = make_rest(handler, max_retries=1)
        async with client, rest:
            with self.assertRaisesRegex(MarketDataError, 'retry budget'):
                await rest.server_time()
            assert count == 2
            assert rest._next_request >= clock.value + 11

    async def test_only_allowlisted_fixed_public_requests_and_no_redirects(self):
        calls = []
        rest, client, _ = make_rest(lambda request: calls.append(request) or httpx.Response(302, headers={'Location': 'https://example.com'}))
        async with client, rest:
            for endpoint, params in [('https://example.com/api/v3/time', {}), ('/api/v3/order', {}), ('/api/v3/klines', {'symbol': 'BTCUSDT'}), ('/api/v3/time', {'signature': 'x'})]:
                with self.assertRaises(ValueError):
                    await rest.request(endpoint, params)
            with self.assertRaisesRegex(MarketDataError, '302'):
                await rest.server_time()
        assert len(calls) == 1

    async def test_rest_typed_candles_exclude_open_and_reject_microseconds(self):
        payload = [rest_row(BASE), rest_row(BASE + 60000)]

        def handler(request):
            return httpx.Response(200, json={'serverTime': BASE + 60001} if request.url.path.endswith('/time') else payload)
        rest, client, _ = make_rest(handler)
        async with client, rest:
            rows = await rest.klines('1m', BASE, BASE + 120000)
            assert [row.is_closed for row in rows] == [True, False]
            assert all((isinstance(row, Kline) for row in rows))
            payload[0][0] *= 1000
            with self.assertRaisesRegex(MarketDataError, 'invalid kline'):
                await rest.klines('1m', BASE, BASE + 120000)

    async def test_exchange_info_requires_active_spot(self):
        for status, spot in [('BREAK', True), ('TRADING', False), ('TRADING', None)]:
            with self.subTest(status=status, spot=spot):
                await self._case_test_exchange_info_requires_active_spot(status, spot)

    async def _case_test_exchange_info_requires_active_spot(self, status, spot):
        payload = {'symbols': [{'symbol': 'SOLUSDT', 'baseAsset': 'SOL', 'quoteAsset': 'USDT', 'status': status, 'isSpotTradingAllowed': spot, 'filters': []}]}
        rest, client, _ = make_rest(lambda request: httpx.Response(200, json=payload))
        async with client, rest:
            with self.assertRaisesRegex(MarketDataError, 'active spot'):
                await rest.exchange_info()

    async def test_book_ticker_rejects_nonfinite_or_crossed_prices(self):
        payload = {'symbol': 'SOLUSDT', 'bidPrice': 'NaN', 'bidQty': '1', 'askPrice': '101', 'askQty': '1'}
        rest, client, _ = make_rest(lambda request: httpx.Response(200, json=payload))
        async with client, rest:
            with self.assertRaisesRegex(MarketDataError, 'bidPrice'):
                await rest.book_ticker()
            payload['bidPrice'] = '102'
            with self.assertRaisesRegex(MarketDataError, 'crossed'):
                await rest.book_ticker()

    async def test_paging_deduplicates_and_resume_uses_stored_rows(self):
        db = FakeDB()
        end = BASE + 1001 * 60000 - 1
        failed = False

        def transform(interval, start, rows):
            nonlocal failed
            if interval == '1m' and start == BASE:
                return rows[:999] + [rows[0]]
            if interval == '1m' and (not failed):
                failed = True
                raise MarketDataError('interrupted page')
            return rows
        rest = FakeRest(end + 1, transform)
        gaps = await Backfill(db, rest).run(BASE, end)
        assert len(db.open_times('1m', BASE, end)) == 999
        assert gaps['1m'] == [(BASE + 999 * 60000, BASE + 1000 * 60000)]
        assert db.sync['1m'] == {'cursor': BASE + 999 * 60000, 'status': 'unavailable'}
        rest.calls.clear()
        result = await Backfill(db, rest).run(BASE, end)
        assert all((not values for values in result.values()))
        assert rest.calls[0][1] == BASE + 999 * 60000
        assert len(db.open_times('1m', BASE, end)) == 1001
        assert max((len(batch) for batch in db.writes)) <= 1000

    async def test_interior_gap_repaired_despite_newer_persisted_cursor_and_page_hole(self):
        step = 60000
        db = FakeDB([candle(BASE), candle(BASE + 5 * step)])
        db.sync['1m'] = {'cursor': BASE + 6 * step, 'status': 'complete'}
        omitted = False

        def transform(interval, start, rows):
            nonlocal omitted
            if interval == '1m' and (not omitted):
                omitted = True
                return [row for row in rows if row.open_time != BASE + 3 * step]
            return rows
        rest = FakeRest(BASE + 6 * step, transform)
        result = await Backfill(db, rest).run(BASE, BASE + 6 * step)
        assert result['1m'] == []
        assert db.open_times('1m', BASE, BASE + 6 * step) == [BASE + index * step for index in range(6)]
        assert any((start == BASE + 3 * step and end == start for interval, start, end, _ in rest.calls if interval == '1m'))

    async def test_empty_rest_page_remains_unavailable_without_spinning(self):
        rest = FakeRest(BASE + DAY_MS, lambda interval, start, rows: [])
        db = FakeDB()
        backfill = Backfill(db, rest)
        gaps = await backfill.run(BASE, BASE + 60000)
        assert gaps['1m'] == [(BASE, BASE + 60000)]
        assert len(rest.calls) <= 5
        assert db.sync['1m']['status'] == 'unavailable'
        assert '1m' in backfill.errors

    async def test_rest_reconciles_preexisting_ws_closed_rows(self):
        db = FakeDB([replace(candle(BASE), source='ws', close='100.5')])
        rest = FakeRest(BASE + 60000)
        gaps = await Backfill(db, rest).run(BASE, BASE + 60000)
        assert not gaps['1m']
        assert db.rows['1m', BASE].source == 'rest'
        assert db.rows['1m', BASE].close == '101'

    async def test_missing_rest_confirmation_cannot_be_hidden_by_present_ws_rows(self):
        db = FakeDB([replace(candle(BASE + index * 60000), source='ws') for index in range(3)])
        rest = FakeRest(BASE + 3 * 60000, lambda interval, start, rows: [row for row in rows if row.open_time != BASE + 60000])
        backfill = Backfill(db, rest)
        gaps = await backfill.run(BASE, BASE + 3 * 60000)
        assert gaps['1m'] == []
        assert '1m' in backfill.errors
        assert 'did not confirm' in backfill.errors['1m']
        self.assertEqual(db.sync['1m']['status'], 'unavailable')

    async def test_ws_keeps_only_five_open_candles_and_persists_close_only(self):
        db = FakeDB()
        collector = Collector(db)
        for index in range(30):
            for interval, step in INTERVAL_MS.items():
                await collector._handle_message(ws_message(BASE + index * step, interval))
        assert len(collector._open) == 5
        assert db.rows == {}
        for interval, step in INTERVAL_MS.items():
            await collector._handle_message(ws_message(BASE + 29 * step, interval, closed=True))
        assert collector._open == {}
        assert len(db.rows) == 5
        assert collector.health['latest_processing_latency_ms'] is not None
        for _ in range(50):
            collector._error('fixture', ValueError('x'))
        assert len(collector.health['errors']) == 20
        json.dumps(collector.health, allow_nan=False)

    async def test_ws_rejects_malformed_mismatched_and_shutdown_messages(self):
        collector = Collector(FakeDB())
        with self.assertRaises(ValueError):
            await collector._handle_message('not JSON')
        payload = json.loads(ws_message(BASE))
        payload['stream'] = 'solusdt@kline_5m'
        with self.assertRaisesRegex(ValueError, 'disagree'):
            await collector._handle_message(json.dumps(payload))
        with self.assertRaises(ServerShutdown):
            await collector._handle_message(json.dumps({'stream': '!serverShutdown', 'data': {'e': 'serverShutdown', 'E': BASE}}))
        assert collector.health['last_message_ms'] is None

    def test_health_detects_one_stale_interval_and_disconnect(self):
        collector = Collector(FakeDB())
        now = time.time_ns() // 1000000
        collector._state.update({'running': True, 'rest_connected': True, 'ws_connected': True, 'backfill_complete': True, 'history_gaps': 0, 'clock_offset_ms': 0})
        collector._state['last_by_interval'] = dict.fromkeys(INTERVAL_MS, now)
        collector._state['stream_event_ms'] = dict.fromkeys(INTERVAL_MS, now)
        assert collector.health['status'] == 'available'
        collector._state['last_by_interval']['4h'] -= 20000
        assert collector.health['status'] == 'unavailable'
        assert collector.health['interval_health']['4h']['stale']
        collector._state['last_by_interval']['4h'] = now
        collector._state['ws_connected'] = False
        assert all((item['stale'] for item in collector.health['interval_health'].values()))

    async def test_stream_watchdog_catches_single_stalled_interval_and_uses_official_url(self):
        options = {}

        class FakeStream:

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def recv(self):
                await asyncio.sleep(0.002)
                now = time.time_ns() // 1000000
                return ws_message(now // 60000 * 60000, event_time=now)

        def connect(url, **kwargs):
            options.update(kwargs)
            assert url == WS_URL
            assert len(STREAMS) == 5
            return FakeStream()
        db = FakeDB()
        collector = Collector(db, ws_connect=connect, stale_seconds=0.02)
        with self.assertRaisesRegex(MarketDataError, 'stopped updating'):
            await asyncio.wait_for(collector._stream_once(), timeout=1)
        assert options['ping_interval'] is None
        assert options['proxy'] is None
        assert options['max_queue'] == 32
        assert db.events[0][0] == 'ws_connected'

    async def test_run_ingests_while_backfill_waits_and_cleans_up_on_cancel(self):
        db = FakeDB()
        started = asyncio.Event()
        connected = asyncio.Event()
        closed = asyncio.Event()

        class SlowRest:

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                closed.set()

        class TestCollector(Collector):

            async def _reconcile_loop(self, rest):
                started.set()
                await asyncio.Future()

            async def _stream_loop(self):
                await started.wait()
                await self._handle_message(ws_message(BASE, closed=True))
                connected.set()
                await asyncio.Future()
        collector = TestCollector(db, rest_factory=SlowRest)
        task = asyncio.create_task(collector.run())
        await asyncio.wait_for(connected.wait(), timeout=1)
        assert len(db.rows) == 1
        assert not collector.health['backfill_complete']
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        assert not collector.health['running']
        assert not collector.health['ws_connected']

    async def test_collector_terminal_block_cancels_stream_and_makes_no_retries(self):
        db = FakeDB()
        stopped = asyncio.Event()
        calls = 0

        class DummyRest:

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        class TestCollector(Collector):

            async def _reconcile_loop(self, rest):
                nonlocal calls
                calls += 1
                raise AccessBlocked('HTTP 451')

            async def _stream_loop(self):
                try:
                    await asyncio.Future()
                finally:
                    stopped.set()
        collector = TestCollector(db, rest_factory=DummyRest)
        task = asyncio.create_task(collector.run())
        await asyncio.wait_for(stopped.wait(), timeout=1)
        assert collector.health['access_blocked']
        assert collector.health['status'] == 'unavailable'
        assert calls == 1
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
if __name__ == '__main__':
    unittest.main()
