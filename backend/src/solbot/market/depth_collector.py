"""Independent public microstructure process. No account, credential or order APIs."""
import asyncio
from collections import deque
from contextlib import suppress
import json
import os
from pathlib import Path
import random
import time

import httpx
import websockets

from solbot.mathematics.market_features import MarketFeatures, input_hash
from solbot.mathematics.orderbook import OrderBook, SequenceGap
from solbot.storage.microstructure import MicrostructureStore
from solbot.storage.models import validate_symbol
from .rest import AccessBlocked, BinanceRest, MarketDataError, MarketUnavailable, REST_ORIGIN
from .trade_collector import BookTicker, TradeWindow


def utc_ms():
    return time.time_ns() // 1_000_000


class DepthRest(BinanceRest):
    """Reuse P1 public client/time/rules/pacing; add one fixed public depth request."""
    async def depth_snapshot(self):
        if self._blocked:
            raise self._blocked
        if self._client is None:
            raise RuntimeError("use DepthRest as an async context manager")
        # Official weight for 101..500 depth levels is 25. No dynamic endpoint,
        # signed parameter, proxy, redirect or API key can enter this path.
        for attempt in range(self._max_retries + 1):
            await self._pace(25)
            retry_after = 0.0
            try:
                response = await self._client.get(REST_ORIGIN + "/api/v3/depth",
                                                  params={"symbol": self.symbol, "limit": 500}, follow_redirects=False)
                if response.status_code in (418, 451):
                    self._blocked = AccessBlocked(f"public depth blocked (HTTP {response.status_code})")
                    raise self._blocked
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    retry_after = self._retry_after(response.headers.get("Retry-After"))
                    self._next_request = max(self._next_request, self._monotonic() + retry_after)
                    if attempt == self._max_retries:
                        raise MarketDataError("depth retry budget exhausted")
                elif response.status_code != 200:
                    raise MarketDataError(f"public depth request failed (HTTP {response.status_code})")
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise MarketDataError("depth returned invalid JSON") from exc
                    if not isinstance(payload, dict) or "lastUpdateId" not in payload or any(not isinstance(payload.get(side), list) or len(payload[side]) > 500 for side in ("bids", "asks")):
                        raise MarketDataError("invalid bounded depth snapshot")
                    return payload
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self._max_retries:
                    raise MarketDataError("depth transport retry budget exhausted") from exc
            await self._sleep(max(retry_after, min(30, 2**attempt * (0.5 + self._jitter()))))
        raise AssertionError("unreachable")


class DepthCollector:
    def __init__(self, store, *, symbol=None, rest_factory=DepthRest, ws_connect=websockets.connect,
                 status_path=None, sample_seconds=1, rotate_seconds=23 * 3600 + 50 * 60, stale_seconds=10):
        self.symbol = validate_symbol(symbol or store.symbol)
        if self.symbol != store.symbol:
            raise ValueError("collector/store symbol mismatch")
        if not 0.1 <= sample_seconds <= 60 or not 0 < rotate_seconds < 86400 or not 0 < stale_seconds <= 60:
            raise ValueError("invalid collector timing")
        self.store, self.rest_factory, self.ws_connect = store, rest_factory, ws_connect
        self.sample_seconds, self.rotate_seconds, self.stale_seconds = sample_seconds, rotate_seconds, stale_seconds
        self.status_path = Path(status_path).resolve() if status_path else None
        if self.status_path is not None:
            if self.status_path.suffix.lower() != ".json" or self.status_path == store.path or (self.status_path.exists() and store.path.exists() and os.path.samefile(self.status_path, store.path)):
                raise ValueError("status must be a separate .json file")
        self.book = OrderBook(self.symbol)
        self.trades, self.ticker = TradeWindow(self.symbol), BookTicker(self.symbol)
        self.features = MarketFeatures(self.book, self.trades, self.ticker)
        self.rules = None
        self.clock_offset_ms = 0
        self.connected = False
        self.blocked = False
        self.reconnects = self.sequence_resets = 0
        self.errors = deque(maxlen=20)
        self._running = False

    @property
    def url(self):
        symbol = self.symbol.lower()
        return f"wss://data-stream.binance.vision:443/stream?streams={symbol}@depth@100ms/{symbol}@aggTrade/{symbol}@bookTicker"

    def snapshot(self, now_ms=None):
        result = self.features.snapshot(now_ms or utc_ms(), connected=self.connected, rules=self.rules,
                                        clock_offset_ms=self.clock_offset_ms,
                                        extra_reasons=("PUBLIC_ACCESS_BLOCKED",) if self.blocked else ())
        result["collector"] = {"connected": self.connected, "blocked": self.blocked, "reconnects": self.reconnects,
                               "sequence_resets": self.sequence_resets, "errors": list(self.errors),
                               "depth_level_bound_per_side": self.book.max_levels,
                               "depth_buffer_bound": 1024, "trade_buffer_bound": self.trades.max_trades,
                               "research_retention_days": self.store.retention_ms // 86400000,
                               "research_max_rows": self.store.max_rows, "research_sample_interval_ms": self.store.history_interval_ms}
        result["input_hash"] = input_hash(result)
        return result

    async def _persist(self):
        snapshot = self.snapshot()
        await asyncio.to_thread(self.store.append, snapshot)
        if self.status_path is not None:
            def write_status():
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.status_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(snapshot, sort_keys=True, allow_nan=False), encoding="utf-8")
                os.replace(temporary, self.status_path)
            await asyncio.to_thread(write_status)

    async def _sample_loop(self):
        while True:
            await self._persist()
            await asyncio.sleep(self.sample_seconds)

    async def _reader(self, stream, queue):
        prefix = self.symbol.lower()
        while True:
            raw = await asyncio.wait_for(stream.recv(), timeout=self.stale_seconds)
            received = utc_ms()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise MarketDataError("malformed combined public stream")
            event = payload["data"]
            if event.get("e") == "serverShutdown":
                raise SequenceGap("official serverShutdown requires reconnect")
            if event.get("s") != self.symbol:
                raise MarketDataError("foreign public stream symbol")
            stream_name = payload.get("stream")
            if stream_name == prefix + "@depth@100ms":
                if event.get("e") != "depthUpdate":
                    raise MarketDataError("incorrect depth stream event type")
                try:
                    queue.put_nowait((event, received))
                except asyncio.QueueFull as exc:
                    raise SequenceGap("depth bootstrap/consumer queue overflow") from exc
            elif stream_name == prefix + "@aggTrade":
                self.trades.add(event, received)
            elif stream_name == prefix + "@bookTicker":
                if self.ticker.add(event, received):
                    self.features.quote_updated(received)
            else:
                raise MarketDataError("unexpected combined stream name")

    async def _depth_flow(self, rest, queue):
        # Reader starts first. Its first U anchors snapshot staleness validation.
        first_event, first_received = await asyncio.wait_for(queue.get(), timeout=self.stale_seconds)
        for attempt in range(3):
            snapshot = await rest.depth_snapshot()
            if snapshot["lastUpdateId"] >= first_event["U"]:
                break
            if attempt == 2:
                raise SequenceGap("REST snapshots remain behind the buffered depth stream")
        self.book.load_snapshot(snapshot, utc_ms())
        self.book.apply(first_event, first_received)
        while True:
            event, received = await asyncio.wait_for(queue.get(), timeout=self.stale_seconds)
            if self.book.apply(event, received):
                if not -2000 <= utc_ms() + self.clock_offset_ms - self.book.exchange_time_ms <= 5000:
                    raise SequenceGap("depth event stale; resynchronize source clock and book")
                self.book.levels(20)

    async def _session(self, rest):
        self.book.reset()
        self.trades.reset()
        self.ticker.value = None
        self.features.quote_times.clear()
        self.features.quote_incomplete_until_ms = 0
        # Eligibility is checked before accepting any market-derived feature.
        self.rules = await rest.exchange_info()
        server_now = await rest.server_time()
        self.clock_offset_ms = server_now - utc_ms()
        async with self.ws_connect(self.url, ping_interval=None, open_timeout=10, close_timeout=5,
                                   max_size=262144, max_queue=64, proxy=None) as stream:
            self.connected = True
            queue = asyncio.Queue(maxsize=1024)
            tasks = [asyncio.create_task(self._reader(stream, queue)), asyncio.create_task(self._depth_flow(rest, queue))]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=self.rotate_seconds)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.connected = False
                self.book.reset()

    async def _connection_loop(self, rest):
        failures = 0
        while True:
            started = time.monotonic()
            try:
                await self._session(rest)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                response = getattr(exc, "response", None)
                http_status = getattr(response, "status_code", getattr(exc, "status_code", None))
                self.errors.append({"time_ms": utc_ms(), "kind": type(exc).__name__, "message": str(exc)[:200]})
                if isinstance(exc, (AccessBlocked, MarketUnavailable)) or http_status in (418, 451):
                    self.blocked = True
                    self.connected = False
                    self.book.reset()
                    await self._persist()
                    await asyncio.Future()  # Operator action required; no further requests.
                if isinstance(exc, SequenceGap):
                    self.sequence_resets += 1
                self.connected = False
                self.book.reset()
            self.reconnects += 1
            failures = 0 if time.monotonic() - started > 60 else min(failures + 1, 6)
            await asyncio.sleep(min(30, 2 ** max(0, failures - 1) * (0.5 + random.random())))

    async def run(self):
        if self._running:
            raise RuntimeError("microstructure collector is already running")
        self._running = True
        tasks = []
        try:
            async with self.rest_factory(self.symbol) as rest:
                tasks = [asyncio.create_task(self._connection_loop(rest)), asyncio.create_task(self._sample_loop())]
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.connected = False
            self.book.reset()
            self._running = False
            with suppress(Exception):
                await self._persist()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Public depth/aggTrade/bookTicker research collector; no private access")
    parser.add_argument("--symbol", choices=("SOLUSDT", "SUIUSDT"), required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--status-path", required=True)
    args = parser.parse_args()
    store = MicrostructureStore(args.database, args.symbol)
    collector = DepthCollector(store, status_path=args.status_path)
    try:
        asyncio.run(collector.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
