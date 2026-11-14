"""S1 short-term research entries. Not wired into ``evaluate``.

Frozen before the frequency count. The 0.45% floor is three times the 0.15%
round trip. Each UTC day has three 8-hour blocks, and only the first bullish
wide bar outside an existing downtrend is an opportunity. A second pass does
not change these numbers to chase 3-5 or 7-10.
"""

from decimal import Decimal

from solbot.mathematics.decimal_utils import D
from solbot.mathematics.execution_cost import FeeSchedule
from solbot.strategy.indicators import adx, ema

VERSION = "s1-short-1"
FEE = FeeSchedule(bnb_discount_enabled=True).rate("TAKER")
ROUND_TRIP = FEE + FEE
MIN_RANGE = ROUND_TRIP * 3
BLOCK_MS = 8 * 60 * 60 * 1000
DOWNTREND_ADX = 25


def block_id(close_time_ms: int) -> int:
    if type(close_time_ms) is not int or close_time_ms < 0:
        raise ValueError("close time required")
    return close_time_ms // BLOCK_MS


def is_downtrend(ema20_4h, ema50_4h, ema20_1h, ema50_1h, adx_1h) -> bool:
    """Same direction test as ``market_regime`` downtrend. Missing inputs block longs."""
    if None in (ema20_4h, ema50_4h, ema20_1h, ema50_1h, adx_1h):
        return True
    up4 = ema20_4h > ema50_4h
    up1 = ema20_1h > ema50_1h
    return (not up4) and (not up1) and adx_1h >= DOWNTREND_ADX


def qualifies(open_, high, low, close, blocked: bool) -> bool:
    if blocked:
        return False
    opening, high_, low_, close_ = (D(value, positive=True) for value in (open_, high, low, close))
    if high_ < low_:
        return False
    if close_ <= opening:
        return False
    return (high_ - low_) / close_ >= MIN_RANGE


def select_opportunities(bars: list[dict]) -> list[dict]:
    """First qualifying 5m close in each UTC 8-hour block. Input order is time order."""
    seen: set[int] = set()
    chosen = []
    for bar in bars:
        if not qualifies(bar["open"], bar["high"], bar["low"], bar["close"], bool(bar["blocked"])):
            continue
        block = block_id(int(bar["close_time"]))
        if block in seen:
            continue
        seen.add(block)
        chosen.append({**bar, "block_id": block})
    return chosen


def downtrend_lookup(candles_1h: list[dict], candles_4h: list[dict]) -> tuple[list[int], list[bool]]:
    """Flags aligned to 1h closes. True means new longs are blocked."""
    closes_1h = [float(row["close"]) for row in candles_1h]
    closes_4h = [float(row["close"]) for row in candles_4h]
    ema20_1h, ema50_1h = ema(closes_1h, 20), ema(closes_1h, 50)
    ema20_4h, ema50_4h = ema(closes_4h, 20), ema(closes_4h, 50)
    adx_1h = adx(
        [{"high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])} for row in candles_1h],
        14,
    )
    times_4h = [int(row["close_time"]) for row in candles_4h]
    times, flags = [], []
    for index, row in enumerate(candles_1h):
        close_time = int(row["close_time"])
        times.append(close_time)
        cursor = _right_index(times_4h, close_time)
        ready = (
            cursor >= 0
            and ema20_1h[index] is not None
            and ema50_1h[index] is not None
            and adx_1h[index] is not None
            and ema20_4h[cursor] is not None
            and ema50_4h[cursor] is not None
        )
        flags.append(
            True
            if not ready
            else is_downtrend(ema20_4h[cursor], ema50_4h[cursor], ema20_1h[index], ema50_1h[index], adx_1h[index])
        )
    return times, flags


def blocked_at(close_time_ms: int, times_1h: list[int], flags: list[bool]) -> bool:
    cursor = _right_index(times_1h, close_time_ms)
    if cursor < 0:
        return True
    return flags[cursor]


def _right_index(times: list[int], stamp: int) -> int:
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] <= stamp:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1
