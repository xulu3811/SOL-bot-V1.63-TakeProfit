"""T2 swing entry: 4h bias, 1h pullback, 15m reclaim, 5m RSI turn.

A 4h EMA20 below EMA50 vetoes every new long. Stop distance stays inside
0.8%–2.0%. Sizing uses that distance so a wider stop buys fewer coins.
"""
from decimal import Decimal

from .t1_pullback import confirm_1m


def assess(candles: dict, metrics: dict, regime: str) -> tuple[dict, list[str]]:
    del regime
    four_fast = metrics["4h"]["ema20"][-1]
    four_slow = metrics["4h"]["ema50"][-1]
    four_up = four_fast > four_slow
    pulled_back = float(candles["1h"][-1]["close"]) < metrics["1h"]["ema20"][-1]
    reclaimed = float(candles["15m"][-1]["close"]) >= metrics["15m"]["ema20"][-1]
    participated = metrics["15m"]["rvol20"][-1] >= 1.0
    rsi = metrics["5m"]["rsi6"]
    turned = len(rsi) >= 2 and rsi[-2] < 40 and rsi[-1] > rsi[-2]
    confirmed = confirm_1m(candles["1m"], candles["5m"][-1]["close_time"])
    if four_fast < four_slow:
        bias = "FOUR_HOUR_DOWNTREND"
    elif not four_up:
        bias = "FOUR_HOUR_EMA_NOT_UP"
    else:
        bias = None
    checks = {
        "ONE_HOUR_NOT_PULLED_BACK": pulled_back,
        "FIFTEEN_MINUTE_NOT_RECLAIMED": reclaimed,
        "RVOL_15M_BELOW_1": participated,
        "RSI_TURN_NOT_CONFIRMED": turned,
        "ONE_MINUTE_BREAKOUT_UNCONFIRMED": confirmed,
    }
    reasons = [reason for reason, ok in checks.items() if not ok]
    if bias:
        reasons.append(bias)

    rows5 = candles.get("5m") or []
    rsi5_win = [float(v) for v in rsi[-6:] if v is not None]
    min_rsi5 = min(rsi5_win) if rsi5_win else 50.0
    cur_rsi5 = float(rsi[-1])
    prev_rsi5 = float(rsi[-2])
    rsi15_win = [float(v) for v in (metrics["15m"].get("rsi6") or [])[-3:] if v is not None]
    min_rsi15 = min(rsi15_win) if rsi15_win else 50.0
    rvol15_win = [float(v) for v in (metrics["15m"].get("rvol20") or [])[-3:] if v is not None]
    max_rvol15 = max(rvol15_win) if rvol15_win else 0.0
    rvol5_win = [float(v) for v in ((metrics.get("5m") or {}).get("rvol20") or [])[-6:] if v is not None]
    max_rvol5 = max(rvol5_win) if rvol5_win else 0.0
    green5 = False
    floor5 = False
    break5 = False
    if len(rows5) >= 2 and "low" in rows5[-1] and "low" in rows5[-2]:
        last5, prev5 = rows5[-1], rows5[-2]
        quote_vol5 = float(last5.get("quote_volume") or 0.0)
        taker_buy5 = float(last5.get("taker_buy_quote_volume") or 0.0)
        buy_ratio5 = (taker_buy5 / quote_vol5) if quote_vol5 > 0 else 0.0
        green5 = float(last5["close"]) > float(last5["open"])
        recent_low5 = min(float(r["low"]) for r in rows5[-6:] if "low" in r)
        floor5 = float(last5["low"]) > float(prev5["low"]) >= recent_low5
        break5 = float(last5["close"]) > float(prev5["high"]) and buy_ratio5 >= 0.50

    if (
        min_rsi15 <= 22.0
        and min_rsi5 <= 16.0
        and max_rvol15 >= 1.4
        and max_rvol5 >= 1.8
        and green5
        and floor5
        and break5
        and cur_rsi5 > prev_rsi5
        and cur_rsi5 >= 24.0
        and (cur_rsi5 - min_rsi5) >= 12.0
    ):
        return {
            "trend": round(min(1.0, 0.85 + 0.15 * max(0.0, (22.0 - min_rsi15) / 10.0)), 4),
            "momentum": round(min(1.0, 0.85 + 0.15 * max(0.0, (cur_rsi5 - min_rsi5 - 12.0) / 10.0)), 4),
            "participation": round(min(1.0, 0.85 + 0.15 * max(0.0, (max_rvol5 - 1.8) / 1.0)), 4),
            "structure": 1.0,
        }, []

    factors = {
        "trend": float(four_up) if four_up else round(max(0.0, min(0.85, (35.0 - min_rsi15) / 20.0)), 4),
        "momentum": float(turned) if turned else round(max(0.0, min(0.85, (cur_rsi5 - min_rsi5) / 14.0)), 4),
        "participation": float(participated) if participated else round(max(0.0, min(0.85, max_rvol15)), 4),
        "structure": (float(reclaimed) + float(confirmed)) / 2 if (reclaimed or confirmed) else round((0.35 if green5 else 0.1), 4),
    }
    return factors, reasons


def stop_distance(entry, swing_low, atr) -> Decimal:
    """Tighter of the 15m swing and one ATR, then clamped to 0.8%–2.0%."""
    entry = Decimal(str(entry))
    if entry <= 0:
        return Decimal("0.008")
    swing = Decimal(str(swing_low))
    atr_stop = entry - Decimal(str(atr))
    below = [price for price in (swing, atr_stop) if price < entry]
    raw = max(below) if below else entry * Decimal("0.992")
    distance = (entry - raw) / entry
    if distance < Decimal("0.008"):
        return Decimal("0.008")
    if distance > Decimal("0.020"):
        return Decimal("0.020")
    return distance


def risk_notional(equity, stop_distance, fee_rate, score):
    """USDT size (500-1000) scaled by score and bounded by 0.6% equity risk."""
    try:
        equity = Decimal(str(equity))
        stop = Decimal(str(stop_distance))
        fee = Decimal(str(fee_rate))
        score = Decimal(str(score))
    except Exception:
        return None
    if equity <= 0 or stop < Decimal("0.008") or stop > Decimal("0.020"):
        return None
    if fee < 0 or fee > Decimal("0.01"):
        return None
    loss = stop + fee + fee + Decimal("0.002")
    if loss <= 0:
        return None
    span = min(Decimal("1"), max(Decimal("0"), (score - Decimal("75")) / Decimal("25")))
    score_cap = Decimal("500") + span * Decimal("500")
    risk_cap = equity * Decimal("0.006") / loss
    sized = min(Decimal("1000"), score_cap, risk_cap)
    if sized < 500:
        return None
    return sized
