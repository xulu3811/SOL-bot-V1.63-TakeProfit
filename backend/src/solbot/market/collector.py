"""Five-stream SOLUSDT collector with concurrent history repair and health."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import websockets

from solbot.storage.models import INTERVAL_MS, Kline, validate_symbol

from .backfill import Backfill, history_start
from .rest import AccessBlocked, BinanceRest, MarketDataError, MarketUnavailable

STREAMS = tuple(f"solusdt@kline_{interval}" for interval in INTERVAL_MS)
WS_URL = "wss://data-stream.binance.vision:443/stream?streams=" + "/".join(STREAMS)


def _utc_ms() -> int:
    return time.time_ns() // 1_000_000


class ServerShutdown(MarketDataError):
    """Official serverShutdown notification requests an immediate reconnect."""


class Collector:
    """Start stream ingestion immediately while REST history is reconciled.

    Binance sends protocol ping frames every 20 seconds. ``websockets`` answers
    them automatically, including their payload; client pings are disabled.
    Connections rotate at 23h50m, before Binance's documented 24h lifetime.
    Stop by cancelling ``run``; all child tasks and network clients are closed.
    """

    def __init__(
        self,
        db: Any,
        *,
        symbol: str | None = None,
        rest_factory: Callable[[], Any] | None = None,
        ws_connect: Callable[..., Any] = websockets.connect,
        reconcile_seconds: float = 60,
        stale_seconds: float = 15,
        rotate_seconds: float = 23 * 3600 + 50 * 60,
    ) -> None:
        if reconcile_seconds <= 0 or stale_seconds <= 0 or not 0 < rotate_seconds < 86400:
            raise ValueError("invalid collector timing settings")
        self.db = db
        self.symbol = validate_symbol(symbol or getattr(db, "symbol", "SOLUSDT"))
        if getattr(db, "symbol", self.symbol) != self.symbol:
            raise ValueError("collector and database symbols disagree")
        self._rest_factory = rest_factory or (lambda: BinanceRest(self.symbol))
        self._streams = tuple(f"{self.symbol.lower()}@kline_{interval}" for interval in INTERVAL_MS)
        self._ws_url = "wss://data-stream.binance.vision:443/stream?streams=" + "/".join(self._streams)
        self._symbol_verified = asyncio.Event()
        self._ws_connect = ws_connect
        self._reconcile_seconds = reconcile_seconds
        self._stale_seconds = stale_seconds
        self._rotate_seconds = rotate_seconds
        self._repair_requested = asyncio.Event()
        self._open: dict[str, Kline] = {}  # At most one unfinished candle per interval.
        self._errors: deque[dict[str, Any]] = deque(maxlen=20)
        self._state: dict[str, Any] = {
            "symbol": self.symbol, "market_status": "VERIFYING", "blocked_reason": None,
            "status": "unavailable", "ws_connected": False, "rest_connected": False,
            "last_message_ms": None, "last_by_interval": dict.fromkeys(INTERVAL_MS),
            "stream_event_ms": dict.fromkeys(INTERVAL_MS), "reconnects": 0,
            "backfill_complete": False, "history_gaps": None, "history_missing_candles": None,
            "last_reconcile_ms": None, "server_time_ms": None, "clock_offset_ms": None,
            "access_blocked": False, "running": False, "book_ticker": None,
            "book_ticker_ms": None, "symbol_info": None,
            "latest_processing_latency_ms": None, "max_processing_latency_ms": 0.0,
            "latest_source_latency_ms": None, "last_backfill_duration_ms": None,
            "max_backfill_duration_ms": 0.0,
        }

    @property
    def health(self) -> dict[str, Any]:
        """A fresh JSON-safe snapshot; stale status ages even if tasks are waiting."""
        now = _utc_ms()
        result = dict(self._state)
        result["last_by_interval"] = dict(self._state["last_by_interval"])
        result["stream_event_ms"] = dict(self._state["stream_event_ms"])
        result["errors"] = list(self._errors)
        result["open_candle_count"] = len(self._open)
        result["interval_health"] = {}
        offset = result["clock_offset_ms"]
        for interval in INTERVAL_MS:
            receipt = result["last_by_interval"][interval]
            event = result["stream_event_ms"][interval]
            source_age = None if event is None or offset is None else now + offset - event
            stale = (
                not result["ws_connected"] or receipt is None or now - receipt > self._stale_seconds * 1000
                or (source_age is not None and (source_age > self._stale_seconds * 1000 or source_age < -5000))
            )
            result["interval_health"][interval] = {
                "last_message_ms": receipt, "last_event_ms": event, "source_age_ms": source_age,
                "stale": stale, "status": "unavailable" if stale else "available",
            }
        ready = (
            result["running"] and result["rest_connected"] and result["backfill_complete"]
            and result["history_gaps"] == 0 and not result["access_blocked"]
            and all(not item["stale"] for item in result["interval_health"].values())
        )
        result["status"] = "available" if ready else "unavailable"
        return result

    def _error(self, kind: str, exc: BaseException) -> None:
        self._errors.append({"time_ms": _utc_ms(), "kind": kind, "message": str(exc)[:300]})

    async def _connection_event(self, kind: str, detail: dict[str, Any]) -> None:
        try:
            await self.db.event(kind, detail)
        except Exception as exc:
            self._error("event_storage", exc)

    async def _handle_message(self, raw: str | bytes) -> str | None:
        """Parse an expected combined stream, persist only validated closed rows."""
        started = time.perf_counter()
        payload = json.loads(raw)
        event = payload.get("data", payload) if isinstance(payload, dict) else None
        if isinstance(event, dict) and event.get("e") == "serverShutdown":
            raise ServerShutdown("Binance announced serverShutdown")
        if not isinstance(payload, dict) or payload.get("stream") not in self._streams:
            raise ValueError("unexpected combined stream")
        candle = Kline.from_ws(payload, symbol=self.symbol)
        if payload["stream"] != f"{self.symbol.lower()}@kline_{candle.interval}":
            raise ValueError("stream name and candle interval disagree")
        event_ms = payload["data"]["E"]
        previous_event = self._state["stream_event_ms"][candle.interval]
        if previous_event is not None and event_ms < previous_event:
            return None
        if candle.is_closed:
            await self.db.write_klines([candle])
            cached = self._open.get(candle.interval)
            if cached is not None and cached.open_time <= candle.open_time:
                del self._open[candle.interval]
        else:
            cached = self._open.get(candle.interval)
            if cached is None or candle.open_time >= cached.open_time:
                self._open[candle.interval] = candle
        receipt = _utc_ms()
        self._state["last_message_ms"] = receipt
        self._state["last_by_interval"][candle.interval] = receipt
        self._state["stream_event_ms"][candle.interval] = event_ms
        latency = round((time.perf_counter() - started) * 1000, 3)
        self._state["latest_processing_latency_ms"] = latency
        self._state["max_processing_latency_ms"] = max(self._state["max_processing_latency_ms"], latency)
        offset = self._state["clock_offset_ms"]
        self._state["latest_source_latency_ms"] = None if offset is None else receipt + offset - event_ms
        return candle.interval

    async def _stream_once(self) -> None:
        # No subscription messages or client pings consume the server's incoming
        # message quota; the library handles server ping/pong protocol frames.
        async with self._ws_connect(
            self._ws_url, ping_interval=None, open_timeout=10, close_timeout=5,
            max_size=131_072, max_queue=32, proxy=None,
        ) as stream:
            self._state["ws_connected"] = True
            self._state["last_by_interval"] = dict.fromkeys(INTERVAL_MS)
            self._state["stream_event_ms"] = dict.fromkeys(INTERVAL_MS)
            await self._connection_event("ws_connected", {"symbol": self.symbol, "streams": list(self._streams)})
            self._repair_requested.set()
            connected = time.monotonic()
            seen = dict.fromkeys(INTERVAL_MS, connected)
            while True:
                now = time.monotonic()
                if now - connected >= self._rotate_seconds:
                    return
                if any(now - last > self._stale_seconds for last in seen.values()):
                    raise MarketDataError("one or more kline intervals stopped updating")
                timeout = min(5, self._stale_seconds, self._rotate_seconds - (now - connected))
                try:
                    raw = await asyncio.wait_for(stream.recv(), timeout=timeout)
                except TimeoutError:
                    continue
                try:
                    interval = await self._handle_message(raw)
                except (ValueError, TypeError, KeyError) as exc:
                    self._error("ws_payload", exc)
                    continue
                if interval is not None:
                    seen[interval] = time.monotonic()

    async def _stream_loop(self) -> None:
        failures = 0
        while True:
            started = time.monotonic()
            immediate = False
            try:
                await self._stream_once()
                immediate = True  # Planned rotation.
            except ServerShutdown as exc:
                immediate = True
                await self._connection_event("ws_server_shutdown", {"message": str(exc)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # websockets >=14 exposes the handshake HTTP response here.
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", getattr(exc, "status_code", None))
                if status in (418, 451):
                    raise AccessBlocked(f"Binance WebSocket access blocked (HTTP {status})") from exc
                self._error("websocket", exc)
                await self._connection_event("ws_error", {"error": str(exc)[:300]})
            finally:
                self._state["ws_connected"] = False
                self._open.clear()
                await self._connection_event("ws_disconnected", {"duration_seconds": round(time.monotonic() - started, 3)})
            self._state["reconnects"] += 1
            if immediate:
                continue
            # A healthy session resets the retry exponent; rapid failures cap at 30s.
            failures = 0 if time.monotonic() - started >= 60 else min(failures + 1, 6)
            await asyncio.sleep(min(30.0, 2 ** max(0, failures - 1) * (0.5 + random.random())))

    async def _reconcile_once(self, rest: BinanceRest) -> None:
        now = await rest.server_time()
        self._state["server_time_ms"] = now
        self._state["clock_offset_ms"] = now - _utc_ms()
        if self._state["symbol_info"] is None:
            self._state["symbol_info"] = await rest.exchange_info()
            self._state["market_status"] = "READY"
            self._symbol_verified.set()
        self._state["book_ticker"] = await rest.book_ticker()
        self._state["book_ticker_ms"] = _utc_ms()
        backfill = Backfill(self.db, rest)
        started = time.perf_counter()
        try:
            gaps = await backfill.run(history_start(now), now)
        finally:
            duration = round((time.perf_counter() - started) * 1000, 3)
            self._state["last_backfill_duration_ms"] = duration
            self._state["max_backfill_duration_ms"] = max(self._state["max_backfill_duration_ms"], duration)
        self._state["history_gaps"] = sum(len(ranges) for ranges in gaps.values())
        self._state["history_missing_candles"] = sum(
            (end - start) // INTERVAL_MS[interval] + 1
            for interval, ranges in gaps.items() for start, end in ranges
        )
        self._state["backfill_complete"] = self._state["history_gaps"] == 0 and not backfill.errors
        self._state["rest_connected"] = not backfill.errors
        self._state["last_reconcile_ms"] = _utc_ms()
        for interval, error in backfill.errors.items():
            self._error(f"backfill_{interval}", MarketDataError(error))
        await self.db.snapshot(self.health)

    async def _reconcile_loop(self, rest: BinanceRest) -> None:
        while True:
            self._repair_requested.clear()
            try:
                await self._reconcile_once(rest)
            except (AccessBlocked, MarketUnavailable):
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state["rest_connected"] = False
                self._state["backfill_complete"] = False
                self._error("reconciliation", exc)
            # A reconnection during a long backfill is not lost: the event remains
            # set and causes immediate reconciliation of candles missed meanwhile.
            try:
                await asyncio.wait_for(self._repair_requested.wait(), timeout=self._reconcile_seconds)
            except TimeoutError:
                pass

    async def run(self) -> None:
        if self._state["running"]:
            raise RuntimeError("collector is already running")
        self._state["running"] = True
        tasks: list[asyncio.Task[Any]] = []
        try:
            async with self._rest_factory() as rest:
                if getattr(rest, "symbol", self.symbol) != self.symbol:
                    raise ValueError("REST client and collector symbols disagree")
                async def verified_stream() -> None:
                    # Preserve P1 startup behavior; the newly introduced SUI
                    # subscription cannot start before official Spot verification.
                    if self.symbol == "SUIUSDT":
                        await self._symbol_verified.wait()
                    await self._stream_loop()

                tasks = [
                    asyncio.create_task(verified_stream(), name="solbot-ws"),
                    asyncio.create_task(self._reconcile_loop(rest), name="solbot-reconcile"),
                ]
                try:
                    await asyncio.gather(*tasks)
                except (AccessBlocked, MarketUnavailable) as exc:
                    self._state["access_blocked"] = isinstance(exc, AccessBlocked)
                    self._state["market_status"] = "BLOCKED"
                    self._state["blocked_reason"] = str(exc)[:300]
                    self._state["rest_connected"] = False
                    self._state["backfill_complete"] = False
                    kind = "access_blocked" if isinstance(exc, AccessBlocked) else "market_blocked"
                    self._error(kind, exc)
                    await self._connection_event(kind, {"symbol": self.symbol, "error": str(exc)[:300]})
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    # Remain visible to the API, making zero further requests.
                    # An operator can restart after resolving the actual block.
                    await asyncio.Future()
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._state["running"] = False
            self._state["ws_connected"] = False
            self._open.clear()
