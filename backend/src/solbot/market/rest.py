"""Credential-free, paced access to Binance's public market-data origin."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from solbot.storage.models import INTERVAL_MS, Kline, SYMBOL_BASE_ASSETS, validate_symbol

REST_ORIGIN = "https://data-api.binance.vision"
SYMBOL = "SOLUSDT"
ENDPOINT_WEIGHTS = {
    "/api/v3/time": 1,
    "/api/v3/exchangeInfo": 20,
    "/api/v3/ticker/bookTicker": 2,
    "/api/v3/klines": 2,
}
ALLOWED_PARAMS = {
    "/api/v3/time": set(),
    "/api/v3/exchangeInfo": {"symbol"},
    "/api/v3/ticker/bookTicker": {"symbol"},
    "/api/v3/klines": {"symbol", "interval", "startTime", "endTime", "limit"},
}


class MarketDataError(RuntimeError):
    """A bounded, safe-to-report public data failure."""


class AccessBlocked(MarketDataError):
    """418/451 is terminal: never switch origin, proxy, or retry it."""


class MarketUnavailable(MarketDataError):
    """The configured pair failed official Spot eligibility verification."""


class BinanceRest:
    """Only four GET endpoints, with a conservative 120 weight/minute budget.

    Retries are finite. Retry-After delays are honored in full. The default
    client follows no redirects, sends no credentials, and has finite timeouts.
    ``client`` and clocks are injectable for offline tests.
    """

    def __init__(
        self,
        symbol: str = SYMBOL,
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 4,
        weight_per_minute: float = 120,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if max_retries < 0 or weight_per_minute <= 0:
            raise ValueError("invalid retry or pacing settings")
        self.symbol = validate_symbol(symbol)
        self._client = client
        self._owns_client = client is None
        self._max_retries = max_retries
        self._weight_period = 60 / weight_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._next_request = 0.0
        self._pace_lock = asyncio.Lock()
        self._server_ms: int | None = None
        self._server_at = 0.0
        self._blocked: AccessBlocked | None = None

    async def __aenter__(self) -> BinanceRest:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                follow_redirects=False,
                trust_env=False,
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def now_ms(self) -> int:
        if self._server_ms is None:
            raise MarketDataError("server clock has not been synchronized")
        return self._server_ms + int((self._monotonic() - self._server_at) * 1000)

    async def _pace(self, weight: int) -> None:
        async with self._pace_lock:
            now = self._monotonic()
            delay = max(0.0, self._next_request - now)
            if delay:
                await self._sleep(delay)
            self._next_request = self._monotonic() + weight * self._weight_period

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if value is None:
            return 0.0
        try:
            seconds = float(value)
            return max(0.0, seconds) if seconds < float("inf") else 0.0
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 0.0

    async def request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if endpoint not in ENDPOINT_WEIGHTS:
            raise ValueError("endpoint is outside the public market-data allowlist")
        params = dict(params or {})
        if set(params) - ALLOWED_PARAMS[endpoint]:
            raise ValueError("parameter is outside the public market-data allowlist")
        if endpoint != "/api/v3/time":
            if params.get("symbol", self.symbol) != self.symbol:
                raise ValueError("request symbol does not match configured market")
            params["symbol"] = self.symbol
        if self._blocked:
            raise self._blocked
        if self._client is None:
            raise RuntimeError("use BinanceRest as an async context manager")
        for attempt in range(self._max_retries + 1):
            await self._pace(ENDPOINT_WEIGHTS[endpoint])
            retry_after = 0.0
            try:
                response = await self._client.get(
                    REST_ORIGIN + endpoint, params=params, follow_redirects=False
                )
                if response.status_code in (418, 451):
                    self._blocked = AccessBlocked(f"Binance access blocked (HTTP {response.status_code})")
                    raise self._blocked
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    retry_after = self._retry_after(response.headers.get("Retry-After"))
                    if attempt == self._max_retries:
                        # Preserve the cooldown for the next reconciliation attempt too.
                        self._next_request = max(self._next_request, self._monotonic() + retry_after)
                        raise MarketDataError(f"Binance retry budget exhausted (HTTP {response.status_code})")
                elif response.status_code != 200:
                    if endpoint == "/api/v3/exchangeInfo" and response.status_code == 400:
                        try:
                            invalid_symbol = response.json().get("code") == -1121
                        except (ValueError, AttributeError):
                            invalid_symbol = False
                        if invalid_symbol:
                            raise MarketUnavailable(f"{self.symbol} is not listed by Binance")
                    raise MarketDataError(f"Binance request failed (HTTP {response.status_code})")
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise MarketDataError("Binance returned invalid JSON") from exc
                    if isinstance(payload, dict) and "code" in payload:
                        raise MarketDataError("Binance returned an API error")
                    return payload
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self._max_retries:
                    raise MarketDataError(f"Binance transport retry budget exhausted ({type(exc).__name__})") from exc
            delay = min(30.0, 2**attempt * (0.5 + self._jitter()))
            await self._sleep(max(delay, retry_after))
        raise AssertionError("unreachable")

    async def server_time(self) -> int:
        payload = await self.request("/api/v3/time")
        value = payload.get("serverTime") if isinstance(payload, dict) else None
        if type(value) is not int or not 1_000_000_000_000 <= value < 10_000_000_000_000:
            raise MarketDataError("invalid millisecond server timestamp")
        self._server_ms = value
        self._server_at = self._monotonic()
        return value

    async def exchange_info(self) -> dict[str, Any]:
        payload = await self.request("/api/v3/exchangeInfo")
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
            raise MarketDataError("invalid exchangeInfo response")
        symbol = symbols[0]
        if symbol.get("symbol") != self.symbol or symbol.get("baseAsset") != SYMBOL_BASE_ASSETS[self.symbol] or symbol.get("quoteAsset") != "USDT":
            raise MarketUnavailable(f"exchangeInfo does not describe {self.symbol}")
        if not isinstance(symbol.get("filters"), list) or not isinstance(symbol.get("status"), str):
            raise MarketDataError(f"invalid {self.symbol} metadata")
        if symbol["status"] != "TRADING" or symbol.get("isSpotTradingAllowed") is not True:
            raise MarketUnavailable(f"{self.symbol} is not an active spot market")
        return symbol

    async def book_ticker(self) -> dict[str, str]:
        payload = await self.request("/api/v3/ticker/bookTicker")
        if not isinstance(payload, dict) or payload.get("symbol") != self.symbol:
            raise MarketDataError(f"invalid {self.symbol} book ticker")
        result = {"symbol": self.symbol}
        for field in ("bidPrice", "bidQty", "askPrice", "askQty"):
            value = payload.get(field)
            if not isinstance(value, str):
                raise MarketDataError(f"invalid book ticker {field}")
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise MarketDataError(f"invalid book ticker {field}") from exc
            if not number.is_finite() or number < 0:
                raise MarketDataError(f"invalid book ticker {field}")
            result[field] = value
        if Decimal(result["bidPrice"]) > Decimal(result["askPrice"]):
            raise MarketDataError("crossed book ticker")
        return result

    async def klines(self, interval: str, start: int, end: int, limit: int = 1000) -> list[Kline]:
        if interval not in INTERVAL_MS or not 1 <= limit <= 1000 or start < 0 or end < start:
            raise ValueError("invalid kline range, interval, or limit")
        if self._server_ms is None:
            await self.server_time()
        payload = await self.request("/api/v3/klines", {
            "interval": interval, "startTime": start, "endTime": end, "limit": limit,
        })
        if not isinstance(payload, list) or len(payload) > limit:
            raise MarketDataError("invalid kline response")
        try:
            return [Kline.from_rest(row, interval, self.now_ms, symbol=self.symbol) for row in payload]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise MarketDataError("invalid kline payload") from exc
