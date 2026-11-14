"""Binance public kline normalization without binary floating-point prices."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Mapping, Sequence


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
               "1h": 3_600_000, "4h": 14_400_000}
SYMBOL_BASE_ASSETS = {"SOLUSDT": "SOL", "SUIUSDT": "SUI"}
LIVE_ACCOUNTS = {"SUI-live": "SUIUSDT", "SOL-live": "SOLUSDT"}
LIVE_ACCOUNT_BY_SYMBOL = {"SUIUSDT": "SUI-live", "SOLUSDT": "SOL-live"}
LIVE_SYMBOLS = frozenset(("SOLUSDT", "SUIUSDT", "BNBUSDT"))


def resolve_live_account(account_id: str = "SUI-live", symbol: str | None = None) -> tuple[str, str, str]:
    """Validate and resolve (account_id, symbol, base_asset) for P4/P5 live custody."""
    if account_id not in LIVE_ACCOUNTS:
        raise ValueError("unsupported live account")
    expected_symbol = LIVE_ACCOUNTS[account_id]
    resolved_symbol = expected_symbol if symbol is None else symbol
    if resolved_symbol != expected_symbol:
        raise ValueError("account/symbol mismatch")
    return account_id, resolved_symbol, SYMBOL_BASE_ASSETS[resolved_symbol]


def validate_symbol(value: str) -> str:
    """Only reviewed public Spot pairs can own a market process/database."""
    if not isinstance(value, str) or value not in SYMBOL_BASE_ASSETS:
        raise ValueError("supported market symbols are SOLUSDT and SUIUSDT")
    return value
# Milliseconds since the Unix epoch, within the supported market-data era.
MIN_TIMESTAMP_MS = 1_000_000_000_000
MAX_TIMESTAMP_MS = 4_102_444_800_000


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


def timestamp_ms(value: Any, name: str = "timestamp") -> int:
    """Reject seconds, microseconds, floats and booleans, rather than guessing."""
    if type(value) is not int or not MIN_TIMESTAMP_MS <= value < MAX_TIMESTAMP_MS:
        raise ValueError(f"{name} must be an integer UTC millisecond timestamp")
    return value


def _decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 64 or value.strip() != value:
        raise ValueError(f"{name} must be a decimal string of at most 64 characters")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'} and finite")
    # Keep pathological exponents out of arithmetic and later serialization.
    if parsed and not -30 <= parsed.adjusted() <= 30:
        raise ValueError(f"{name} is outside the supported decimal range")
    return parsed


@dataclass(frozen=True, slots=True)
class Kline:
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str
    trade_count: int
    taker_buy_base_volume: str
    taker_buy_quote_volume: str
    is_closed: bool
    source: str
    ingested_at: int
    event_time: int | None = None

    def validate(self) -> "Kline":
        validate_symbol(self.symbol)
        if self.interval not in INTERVAL_MS:
            raise ValueError("unsupported kline interval")
        step = INTERVAL_MS[self.interval]
        timestamp_ms(self.open_time, "open_time")
        timestamp_ms(self.close_time, "close_time")
        timestamp_ms(self.ingested_at, "ingested_at")
        if self.open_time % step or self.close_time != self.open_time + step - 1:
            raise ValueError("kline times must align to their UTC interval")
        if type(self.is_closed) is not bool:
            raise ValueError("is_closed must be a boolean")
        if self.source not in {"rest", "ws"}:
            raise ValueError("source must be rest or ws")
        if self.event_time is not None:
            timestamp_ms(self.event_time, "event_time")
            if self.event_time < self.open_time:
                raise ValueError("event_time precedes the candle")
            if self.is_closed and self.event_time <= self.close_time:
                raise ValueError("a closed candle must end before its source event")
        elif self.is_closed and self.ingested_at <= self.close_time:
            raise ValueError("a closed candle must end before ingestion")
        if type(self.trade_count) is not int or not 0 <= self.trade_count <= 2**63 - 1:
            raise ValueError("trade_count must be a nonnegative SQLite integer")
        prices = {name: _decimal(getattr(self, name), name, positive=True)
                  for name in ("open", "high", "low", "close")}
        if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
            raise ValueError("OHLC prices are inconsistent")
        values = {name: _decimal(getattr(self, name), name) for name in (
            "volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume")}
        if values["taker_buy_base_volume"] > values["volume"] or values["taker_buy_quote_volume"] > values["quote_volume"]:
            raise ValueError("taker-buy volume exceeds total volume")
        return self

    @classmethod
    def from_rest(cls, row: Sequence[Any], interval: str, now_ms: int, symbol: str = "SOLUSDT") -> "Kline":
        timestamp_ms(now_ms, "server_time")
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence) or len(row) < 11:
            raise ValueError("REST kline must contain at least 11 fields")
        close_time = timestamp_ms(row[6], "close_time")
        return cls(
            symbol=validate_symbol(symbol), interval=interval, open_time=row[0], close_time=close_time,
            open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
            quote_volume=row[7], trade_count=row[8], taker_buy_base_volume=row[9],
            taker_buy_quote_volume=row[10], is_closed=close_time < now_ms,
            source="rest", ingested_at=utc_ms(), event_time=now_ms,
        ).validate()

    @classmethod
    def from_ws(cls, payload: Mapping[str, Any], symbol: str | None = None) -> "Kline":
        if not isinstance(payload, Mapping):
            raise ValueError("WebSocket kline must be an object")
        event = payload.get("data", payload)
        if not isinstance(event, Mapping) or event.get("e") != "kline":
            raise ValueError("expected a Binance kline event")
        candle = event.get("k")
        if not isinstance(candle, Mapping):
            raise ValueError("missing WebSocket kline object")
        try:
            if event["s"] != candle["s"]:
                raise ValueError("event and kline symbols disagree")
            if symbol is not None and candle["s"] != validate_symbol(symbol):
                raise ValueError("WebSocket symbol does not match configured market")
            result = cls(
                symbol=candle["s"], interval=candle["i"], open_time=candle["t"],
                close_time=candle["T"], open=candle["o"], high=candle["h"],
                low=candle["l"], close=candle["c"], volume=candle["v"],
                quote_volume=candle["q"], trade_count=candle["n"],
                taker_buy_base_volume=candle["V"], taker_buy_quote_volume=candle["Q"],
                is_closed=candle["x"], source="ws", ingested_at=utc_ms(), event_time=event["E"],
            )
        except KeyError as exc:
            raise ValueError(f"missing WebSocket kline field: {exc.args[0]}") from exc
        return result.validate()
