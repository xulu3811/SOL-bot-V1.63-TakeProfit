"""T2 swing entry: 4h bias, 1h pullback, 15m reclaim, 5m RSI turn.

A 4h EMA20 below EMA50 vetoes every new long. Stop distance stays inside
0.8%–2.0%. Sizing uses that distance so a wider stop buys fewer coins.
"""
from decimal import Decimal

from .t1_pullback import confirm_1m


def assess(candles: dict, metrics: dict, regime: str) -> tuple[dict, list[str]]:
    four_fast = metrics["4h"]["ema20"][-1]
    four_slow = metrics["4h"]["ema50"][-1]
    four_up = four_fast > four_slow

    rows5 = candles.get("5m") or []
    rows15 = candles.get("15m") or []
    rows1h = candles.get("1h") or []
    m5 = metrics.get("5m") or {}
    m15 = metrics.get("15m") or {}
    m1h = metrics.get("1h") or {}

    c5 = float(rows5[-1]["close"]) if rows5 else 0.0
    c15 = float(rows15[-1]["close"]) if rows15 else c5
    c1h = float(rows1h[-1]["close"]) if rows1h else c5
    e20_15 = float((m15.get("ema20") or [0.0])[-1] or 0.0)
    e50_15_list = m15.get("ema50") or []
    e50_15 = float(e50_15_list[-1] or 0.0) if e50_15_list else 0.0
    e20_1h_list = m1h.get("ema20") or []
    e20_1h = float(e20_1h_list[-1] or 0.0) if e20_1h_list else 0.0
    e20_5m = float((m5.get("ema20") or [0.0])[-1] or 0.0)
    vwap_5m = float((m5.get("vwap20") or [0.0])[-1] or 0.0)

    quote_vol5 = float(rows5[-1].get("quote_volume") or 0.0) if rows5 else 0.0
    taker_buy5 = float(rows5[-1].get("taker_buy_quote_volume") or 0.0) if rows5 else 0.0
    buy_ratio5 = (taker_buy5 / quote_vol5) if quote_vol5 > 0 else 0.0
    rvol5_recent = [float(v) for v in (m5.get("rvol20") or [])[-2:] if v is not None]
    max_rvol5_2 = max(rvol5_recent) if rvol5_recent else 0.0

    prior_15m = rows15[-12:-3] if len(rows15) >= 12 else rows15[:-3]
    floor_15m = bool(prior_15m) and (
        min(float(r["low"]) for r in rows15[-3:] if "low" in r)
        >= min(float(r["low"]) for r in prior_15m if "low" in r)
    )
    h1_rising = len(e20_1h_list) >= 4 and e20_1h >= float(e20_1h_list[-4] or 0.0) * 0.9995
    m4h = metrics.get("4h") or {}
    adx_4h_list = m4h.get("adx14") or []
    rsi_4h_list = m4h.get("rsi6") or []
    vwap_1h_list = m1h.get("vwap20") or []
    adx_1h_list = m1h.get("adx14") or []
    adx_4h_ok = not adx_4h_list or adx_4h_list[-1] is None or float(adx_4h_list[-1]) >= 40.0
    rsi_4h_ok = not rsi_4h_list or rsi_4h_list[-1] is None or float(rsi_4h_list[-1]) >= 42.0
    vwap_1h_ok = not vwap_1h_list or vwap_1h_list[-1] is None or max(c1h, c5) >= float(vwap_1h_list[-1])
    adx_1h_ok = not adx_1h_list or adx_1h_list[-1] is None or float(adx_1h_list[-1]) <= 16.0
    recovery_trend = (
        regime != "DOWNTREND"
        and e50_15 > 0.0
        and e20_15 > e50_15 * 1.0003
        and max(c15, c5) >= e20_15
        and c5 >= e50_15
        and e20_5m > 0.0
        and vwap_5m > 0.0
        and c5 >= e20_5m
        and c5 >= vwap_5m
        and floor_15m
        and max(c1h, c5) >= e20_1h
        and h1_rising
        and adx_4h_ok
        and rsi_4h_ok
        and vwap_1h_ok
        and adx_1h_ok
    )

    pulled_back = (
        c1h < e20_1h
        or (
            recovery_trend
            and bool(rows15)
            and min(float(r["low"]) for r in rows15[-3:] if "low" in r) <= e20_15 * 1.001
        )
    )
    reclaimed = (
        c15 >= e20_15
        or (recovery_trend and c5 >= e20_15 and c5 >= e20_5m and c5 >= vwap_5m)
    )
    participated = (
        float(m15["rvol20"][-1]) >= 1.0
        or (recovery_trend and max_rvol5_2 >= 1.2 and buy_ratio5 >= 0.50)
    )
    rsi = m5["rsi6"]
    turned = (
        (len(rsi) >= 2 and rsi[-2] < 40 and rsi[-1] > rsi[-2])
        or (
            recovery_trend
            and len(rsi) >= 4
            and any(v is not None and float(v) < 40.0 for v in rsi[-4:-1])
            and float(rsi[-1]) > float(rsi[-2])
            and 48.0 <= float(rsi[-1]) <= 70.0
            and buy_ratio5 >= 0.50
        )
    )
    confirmed = confirm_1m(candles["1m"], candles["5m"][-1]["close_time"])
    if four_fast < four_slow and not recovery_trend:
        bias = "FOUR_HOUR_DOWNTREND"
    elif not (four_up or recovery_trend):
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

    rsi5_win = [float(v) for v in rsi[-6:] if v is not None]
    min_rsi5 = min(rsi5_win) if rsi5_win else 50.0
    cur_rsi5 = float(rsi[-1])
    prev_rsi5 = float(rsi[-2])
    rsi15_win = [float(v) for v in (m15.get("rsi6") or [])[-3:] if v is not None]
    min_rsi15 = min(rsi15_win) if rsi15_win else 50.0
    rvol15_win = [float(v) for v in (m15.get("rvol20") or [])[-3:] if v is not None]
    max_rvol15 = max(rvol15_win) if rvol15_win else 0.0
    rvol5_win = [float(v) for v in (m5.get("rvol20") or [])[-6:] if v is not None]
    max_rvol5 = max(rvol5_win) if rvol5_win else 0.0
    green5 = False
    floor5 = False
    break5 = False
    if len(rows5) >= 2 and "low" in rows5[-1] and "low" in rows5[-2]:
        last5, prev5 = rows5[-1], rows5[-2]
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
        # Require 1m breakout confirmation on extreme oversold reversals to avoid catching falling knives.
        return {
            "trend": round(min(1.0, 0.85 + 0.15 * max(0.0, (22.0 - min_rsi15) / 10.0)), 4),
            "momentum": round(min(1.0, 0.85 + 0.15 * max(0.0, (cur_rsi5 - min_rsi5 - 12.0) / 10.0)), 4),
            "participation": round(min(1.0, 0.85 + 0.15 * max(0.0, (max_rvol5 - 1.8) / 1.0)), 4),
            "structure": 1.0 if confirmed else 0.5,
        }, ([] if confirmed else ["ONE_MINUTE_BREAKOUT_UNCONFIRMED"])

    factors = {
        "trend": 1.0 if (four_up or recovery_trend) else round(max(0.0, min(0.85, (35.0 - min_rsi15) / 20.0)), 4),
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
