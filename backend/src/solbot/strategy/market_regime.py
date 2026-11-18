"""Initial research regime thresholds; no calibration or profitability claim."""


def classify(candles: dict, metrics: dict) -> tuple[str, list[str]]:
    h4, h1, m15 = (metrics[key] for key in ("4h", "1h", "15m"))
    m5 = metrics.get("5m") or {}
    latest = lambda data, key: data[key][-1]
    up4 = latest(h4, "ema20") > latest(h4, "ema50")
    up1 = latest(h1, "ema20") > latest(h1, "ema50")
    up15 = (latest(m15, "ema20") >= latest(m15, "ema50")
            and float(candles["15m"][-1]["close"]) >= latest(m15, "ema50")
            and min(float(row["low"]) for row in candles["15m"][-3:])
            >= min(float(row["low"]) for row in candles["15m"][-6:-3]))
    strength = latest(h1, "adx14")
    if up4 and up1 and up15 and strength >= 20:
        return "UPTREND", []

    # Intraday / post-correction recovery uptrend: when 15m EMA20 > EMA50 with
    # structural higher lows over the 3-hour window, 5m has reclaimed EMA20 &
    # VWAP20, and 1h price holds above a non-falling 1h EMA20.
    c15 = float(candles["15m"][-1]["close"])
    c5 = float(candles["5m"][-1]["close"]) if candles.get("5m") else c15
    c1h = float(candles["1h"][-1]["close"])
    e20_15 = float(latest(m15, "ema20"))
    e50_15 = float(latest(m15, "ema50"))
    e20_1h = float(latest(h1, "ema20"))
    e20_5m = float(m5["ema20"][-1]) if (m5.get("ema20") and m5["ema20"][-1] is not None) else None
    vwap_5m = float(m5["vwap20"][-1]) if (m5.get("vwap20") and m5["vwap20"][-1] is not None) else None
    rows15 = candles.get("15m") or []
    prior_15m_slice = rows15[-12:-3] if len(rows15) >= 12 else rows15[:-3]
    floor_15m_ok = bool(prior_15m_slice) and (
        min(float(row["low"]) for row in rows15[-3:])
        >= min(float(row["low"]) for row in prior_15m_slice)
    )
    h1_ema_rising = len(h1.get("ema20") or []) >= 4 and e20_1h >= float(h1["ema20"][-4]) * 0.9995
    adx_4h_list = h4.get("adx14") or []
    rsi_4h_list = h4.get("rsi6") or []
    vwap_1h_list = h1.get("vwap20") or []
    adx_4h_ok = not adx_4h_list or adx_4h_list[-1] is None or float(adx_4h_list[-1]) >= 25.0
    rsi_4h_ok = not rsi_4h_list or rsi_4h_list[-1] is None or float(rsi_4h_list[-1]) >= 42.0
    vwap_1h_ok = not vwap_1h_list or vwap_1h_list[-1] is None or max(c1h, c5) >= float(vwap_1h_list[-1])
    adx_1h_ok = strength is None or float(strength) <= 16.0
    recovery_uptrend = (
        e20_15 > e50_15 * 1.0003
        and max(c15, c5) >= e20_15
        and c5 >= e50_15
        and e20_5m is not None
        and vwap_5m is not None
        and c5 >= e20_5m
        and c5 >= vwap_5m
        and floor_15m_ok
        and max(c1h, c5) >= e20_1h
        and h1_ema_rising
        and adx_4h_ok
        and rsi_4h_ok
        and vwap_1h_ok
        and adx_1h_ok
    )
    if recovery_uptrend:
        return "UPTREND", []

    if not up4 and not up1 and strength >= 25:
        return "DOWNTREND", ["HIGHER_TIMEFRAME_DOWNTREND"]
    slope = abs(latest(h1, "ema20") - h1["ema20"][-4]) / float(candles["1h"][-1]["close"])
    if strength < 20 and slope <= 0.003:
        return "RANGE", []
    return "UNCERTAIN", ["HIGHER_TIMEFRAME_DISAGREEMENT"]
