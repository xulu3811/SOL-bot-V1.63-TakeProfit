"""Bounded research indicators with explicitly defined initialization.

EMA starts at the first period SMA. RSI/ATR/ADX use Wilder smoothing;
ADX's first point averages the first period DX values. VWAP is a rolling
20-bar sum of exchange quote volume / base volume (not typical-price VWAP).
RVOL compares this bar with the preceding 20 bars, excluding itself.
"""

from functools import lru_cache
from math import isfinite
from typing import Sequence


def _period(period: int) -> None:
    if type(period) is not int or period < 1:
        raise ValueError("period must be a positive integer")


def ema(values: Sequence[float], period: int) -> list[float | None]:
    _period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        value += alpha * (values[i] - value)
        out[i] = value
    return out


def rsi(values: Sequence[float], period: int = 6) -> list[float | None]:
    _period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [b - a for a, b in zip(values, values[1:])]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    def value() -> float:
        return 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    out[period] = value()
    for i in range(period + 1, len(values)):
        gain = (gain * (period - 1) + max(changes[i - 1], 0)) / period
        loss = (loss * (period - 1) + max(-changes[i - 1], 0)) / period
        out[i] = value()
    return out


def true_range(candles: Sequence[dict]) -> list[float]:
    return [max(float(row["high"]) - float(row["low"]),
                abs(float(row["high"]) - float(candles[i - 1]["close"])),
                abs(float(row["low"]) - float(candles[i - 1]["close"])))
            for i, row in enumerate(candles) if i]


def atr(candles: Sequence[dict], period: int = 14) -> list[float | None]:
    _period(period)
    out: list[float | None] = [None] * len(candles)
    ranges = true_range(candles)
    if len(ranges) < period:
        return out
    value = sum(ranges[:period]) / period
    out[period] = value
    for i in range(period + 1, len(candles)):
        value = (value * (period - 1) + ranges[i - 1]) / period
        out[i] = value
    return out


def adx(candles: Sequence[dict], period: int = 14) -> list[float | None]:
    _period(period)
    out: list[float | None] = [None] * len(candles)
    if len(candles) < 2 * period:
        return out
    ranges = true_range(candles)
    plus, minus = [], []
    for previous, current in zip(candles, candles[1:]):
        up = float(current["high"]) - float(previous["high"])
        down = float(previous["low"]) - float(current["low"])
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    tr, pos, neg = (sum(seq[:period]) for seq in (ranges, plus, minus))
    dx = []
    for i in range(period, len(candles)):
        if i > period:
            tr, pos, neg = (old - old / period + seq[i - 1]
                            for old, seq in ((tr, ranges), (pos, plus), (neg, minus)))
        pdi = 100 * pos / tr if tr else 0.0
        mdi = 100 * neg / tr if tr else 0.0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
        if len(dx) == period:
            value = sum(dx) / period
            out[i] = value
        elif len(dx) > period:
            value = (value * (period - 1) + dx[-1]) / period
            out[i] = value
    return out


def rvol(candles: Sequence[dict], period: int = 20) -> list[float | None]:
    _period(period)
    values = [float(row["volume"]) for row in candles]
    out: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        base = sum(values[i - period:i]) / period
        out[i] = values[i] / base if base > 0 else None
    return out


def vwap(candles: Sequence[dict], period: int = 20) -> list[float | None]:
    _period(period)
    out: list[float | None] = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1:i + 1]
        volume = sum(float(row["volume"]) for row in window)
        out[i] = sum(float(row["quote_volume"]) for row in window) / volume if volume > 0 else None
    return out


def series(candles: Sequence[dict]) -> dict[str, list[float | None]]:
    # At most 96 rolling windows; closed higher timeframes reuse their numeric
    # computation while the 1m confirmation advances. Never cache open bars.
    return _series_cached(tuple(tuple(row[name] for name in
                         ("open_time", "high", "low", "close", "volume", "quote_volume")) for row in candles))


@lru_cache(maxsize=96)
def _series_cached(values: tuple) -> dict[str, list[float | None]]:
    candles = [dict(zip(("open_time", "high", "low", "close", "volume", "quote_volume"), row)) for row in values]
    closes = [float(row["close"]) for row in candles]
    return {"ema20": ema(closes, 20), "ema50": ema(closes, 50),
            "rsi6": rsi(closes), "atr14": atr(candles), "adx14": adx(candles),
            "rvol20": rvol(candles), "vwap20": vwap(candles)}


def number(value: float | None) -> str | None:
    if value is None or not isfinite(value):
        return None
    return format(value, ".10f").rstrip("0").rstrip(".") or "0"
