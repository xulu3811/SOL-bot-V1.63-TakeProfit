"""Trend pullback candidate on finalized 5m, confirmation on later 1m."""


def confirm_1m(rows: list[dict], candidate_time: int) -> bool:
    a, b, c = rows[-3:]
    return (c["close_time"] > candidate_time
            and float(b["low"]) > float(a["low"])
            and float(c["low"]) >= float(b["low"])
            and float(c["close"]) > max(float(a["high"]), float(b["high"]))
            and float(c["close"]) > float(c["open"]))


def assess(candles: dict, metrics: dict, regime: str) -> tuple[dict, list[str]]:
    rows, m = candles["5m"], metrics["5m"]
    rsi = m["rsi6"]
    last, previous = rows[-1], rows[-2]
    close_5m = float(last["close"])
    close_1m = float(candles["1m"][-1]["close"]) if candles.get("1m") else close_5m
    quote_vol = float(last.get("quote_volume") or 0.0)
    taker_buy = float(last.get("taker_buy_quote_volume") or 0.0)
    buy_ratio = (taker_buy / quote_vol) if quote_vol > 0 else 0.0

    cur_rvol5 = float(m["rvol20"][-1])
    prev_rvol5 = float(m["rvol20"][-2]) if len(m["rvol20"]) >= 2 and m["rvol20"][-2] is not None else 0.0
    eff_rvol5 = max(cur_rvol5, prev_rvol5) if (cur_rvol5 >= 1.0 and buy_ratio >= 0.52) else cur_rvol5

    # Standard pullback recovery (20..48 -> >48) OR tight consolidation coil breakout
    pullback_recovery = any(20 <= value <= 48 for value in rsi[-4:-1]) and rsi[-1] > rsi[-2] and rsi[-1] > 48
    prior_highs = [float(r["high"]) for r in rows[-7:-1] if "high" in r]
    coil_breakout = (
        bool(prior_highs)
        and any(40 <= value <= 55 for value in rsi[-4:-1] if value is not None)
        and float(rsi[-1]) >= 60.0
        and float(rsi[-1]) > float(rsi[-2])
        and cur_rvol5 >= 1.5
        and buy_ratio >= 0.55
        and close_5m > max(prior_highs)
    )
    recovery = pullback_recovery or coil_breakout
    participation = cur_rvol5 >= 1.2 or (eff_rvol5 >= 1.2 and cur_rvol5 >= 1.0 and buy_ratio >= 0.52)
    reclaim = (close_5m >= m["vwap20"][-1]
               or (float(last["low"]) >= min(float(row["low"]) for row in rows[-5:-1])
                   and close_5m > float(previous["high"])))
    one_rows = candles.get("1m") or []
    confirmed_classic = confirm_1m(one_rows, last["close_time"])
    confirmed_coil = (
        coil_breakout
        and len(one_rows) >= 2
        and one_rows[-1]["close_time"] > last["close_time"]
        and float(one_rows[-1]["low"]) >= float(last["open"])
        and close_1m >= close_5m
        and float(one_rows[-1]["close"]) > float(one_rows[-1]["open"])
    )
    confirmed = confirmed_classic or confirmed_coil
    chase_1m = (close_1m - close_5m) / close_5m if close_5m > 0 else 0.0
    no_chase_1m = -0.006 <= chase_1m <= 0.006
    h4_close = float(candles["4h"][-1]["close"])
    h4_ema20 = float(metrics["4h"]["ema20"][-1])
    h1_adx = float(metrics["1h"]["adx14"][-1])
    macro_htf = h4_close >= h4_ema20 and h1_adx >= 23.0

    # Intraday / post-correction recovery uptrend alignment
    m15_metrics = metrics.get("15m") or {}
    h1_metrics = metrics.get("1h") or {}
    rows15 = candles.get("15m") or []
    rows1h = candles.get("1h") or []
    e20_15 = float((m15_metrics.get("ema20") or [0.0])[-1] or 0.0)
    e50_15 = float((m15_metrics.get("ema50") or [0.0])[-1] or 0.0)
    e20_1h_list = h1_metrics.get("ema20") or []
    e20_1h = float(e20_1h_list[-1] or 0.0) if e20_1h_list else 0.0
    c15 = float(rows15[-1].get("close") or close_5m) if rows15 else close_5m
    c1h = float(rows1h[-1].get("close") or close_5m) if rows1h else close_5m
    e20_5m = float((m.get("ema20") or [0.0])[-1] or 0.0)
    prior_15m = rows15[-12:-3] if len(rows15) >= 12 else rows15[:-3]
    floor_15m = bool(prior_15m) and (
        min(float(r["low"]) for r in rows15[-3:] if "low" in r)
        >= min(float(r["low"]) for r in prior_15m if "low" in r)
    )
    h1_rising = len(e20_1h_list) >= 4 and e20_1h >= float(e20_1h_list[-4] or 0.0) * 0.9995
    h4_metrics = metrics.get("4h") or {}
    adx_4h_list = h4_metrics.get("adx14") or []
    rsi_4h_list = h4_metrics.get("rsi6") or []
    vwap_1h_list = h1_metrics.get("vwap20") or []
    adx_4h_ok = not adx_4h_list or adx_4h_list[-1] is None or float(adx_4h_list[-1]) >= 25.0
    rsi_4h_ok = not rsi_4h_list or rsi_4h_list[-1] is None or float(rsi_4h_list[-1]) >= 42.0
    vwap_1h_ok = not vwap_1h_list or vwap_1h_list[-1] is None or max(c1h, close_5m) >= float(vwap_1h_list[-1])
    adx_1h_ok = h1_adx <= 16.0
    recovery_htf = (
        e20_15 > 0.0
        and e50_15 > 0.0
        and e20_15 > e50_15 * 1.0003
        and max(c15, close_5m) >= e20_15
        and close_5m >= e50_15
        and (e20_5m <= 0.0 or close_5m >= e20_5m)
        and close_5m >= float(m["vwap20"][-1])
        and floor_15m
        and max(c1h, close_5m) >= e20_1h
        and h1_rising
        and adx_4h_ok
        and rsi_4h_ok
        and vwap_1h_ok
        and adx_1h_ok
    )
    strong_htf = macro_htf or recovery_htf

    # Multi-Timeframe RSI(6) Oversold + RVOL Climax -> Selling Exhaustion Rebound
    rsi_window = [float(v) for v in rsi[-6:] if v is not None]
    min_rsi5 = min(rsi_window) if rsi_window else 50.0
    cur_rsi5 = float(rsi[-1])
    prev_rsi5 = float(rsi[-2])
    rvol_window = [float(v) for v in m["rvol20"][-6:] if v is not None]
    max_rvol5 = max(rvol_window) if rvol_window else 0.0
    rsi15_vals = [float(v) for v in ((metrics.get("15m") or {}).get("rsi6") or [])[-3:] if v is not None]
    rsi1h_vals = [float(v) for v in ((metrics.get("1h") or {}).get("rsi6") or [])[-2:] if v is not None]
    min_rsi15 = min(rsi15_vals) if rsi15_vals else 50.0
    min_rsi1h = min(rsi1h_vals) if rsi1h_vals else 50.0

    mtf_oversold = min_rsi5 <= 18.0 and (min_rsi15 <= 24.0 or min_rsi1h <= 28.0 or min_rsi5 <= 14.0)
    climax_rvol = max_rvol5 >= 1.6
    rsi_exhaustion_turn = cur_rsi5 > prev_rsi5 and cur_rsi5 >= 22.0 and (cur_rsi5 - min_rsi5) >= 10.0

    green_5m = close_5m > float(last["open"])
    recent_low = min(float(row["low"]) for row in rows[-6:])
    floor_ok = float(last["low"]) >= float(previous["low"]) and float(last["low"]) >= recent_low
    step_up_breakout = float(last["low"]) > float(previous["low"]) and close_5m > float(previous["high"]) and buy_ratio >= 0.50
    climax_reclaim = (
        floor_ok
        and close_5m >= max(float(previous["open"]), float(previous["close"]))
        and cur_rvol5 < max_rvol5
        and buy_ratio >= 0.52
        and cur_rsi5 >= 24.0
    )
    rebound_structure = green_5m and floor_ok and (step_up_breakout or climax_reclaim)
    confirm_1m_rebound = confirmed or (bool(one_rows) and float(one_rows[-1]["low"]) >= recent_low and rebound_structure)

    if regime == "UPTREND" and mtf_oversold and climax_rvol and rsi_exhaustion_turn and rebound_structure:
        rebound_checks = {
            "REGIME_NOT_UPTREND": regime == "UPTREND",
            "ONE_MINUTE_BREAKOUT_UNCONFIRMED": confirm_1m_rebound,
            "ONE_MINUTE_CHASE_EXCEEDED": no_chase_1m,
        }
        factors = {
            "trend": round(min(1.0, 0.85 + 0.15 * max(0.0, (18.0 - min_rsi5) / 8.0)), 4),
            "momentum": round(min(1.0, 0.85 + 0.15 * max(0.0, (cur_rsi5 - min_rsi5 - 10.0) / 10.0)), 4),
            "participation": round(min(1.0, 0.85 + 0.15 * max(0.0, (max_rvol5 - 1.6) / 1.0)), 4),
            "structure": (1.0 if step_up_breakout else 0.90) if (confirm_1m_rebound and no_chase_1m) else 0.0,
        }
        return factors, [reason for reason, ok in rebound_checks.items() if not ok]

    checks = {
        "REGIME_NOT_UPTREND": regime == "UPTREND",
        "HIGHER_TIMEFRAME_WEAK_TREND": strong_htf,
        "RSI_PULLBACK_NOT_RECOVERED": recovery,
        "RVOL_BELOW_1_2": participation,
        "VWAP_OR_SUPPORT_NOT_RECLAIMED": reclaim,
        "ONE_MINUTE_BREAKOUT_UNCONFIRMED": confirmed,
        "ONE_MINUTE_CHASE_EXCEEDED": no_chase_1m,
    }
    if regime == "UPTREND" and macro_htf:
        trend_score = min(1.0, 0.75 + 0.25 * max(0.0, (h1_adx - 23.0) / 5.0))
    elif regime == "UPTREND" and recovery_htf:
        spread_15m = (e20_15 - e50_15) / e50_15 if e50_15 > 0 else 0.0
        trend_score = round(min(1.0, 0.80 + 0.20 * min(1.0, spread_15m / 0.003)), 4)
    else:
        trend_score = 0.0
    momentum_score = (
        min(1.0, 0.8 + 0.2 * max(0.0, (float(rsi[-1]) - 48.0) / 2.0))
        if recovery
        else 0.0
    )
    participation_score = (
        min(1.0, 0.8 + 0.2 * max(0.0, (eff_rvol5 - 1.2) / 0.3))
        if participation
        else 0.0
    )
    structure_score = ((float(reclaim) + float(confirmed)) / 2.0) if no_chase_1m else 0.0

    # When in developing oversold or non-uptrend conditions, surface graduated
    # proximity scores so the 5-dimensional dashboard reflects live readiness.
    if regime != "UPTREND" or min_rsi5 <= 28.0:
        oversold_depth = max(0.0, min(0.85, (36.0 - min_rsi5) / 22.0))
        htf_oversold = max(0.0, min(0.85, (36.0 - min(min_rsi15, min_rsi1h)) / 20.0))
        trend_score = round(max(trend_score, 0.55 * oversold_depth + 0.45 * htf_oversold), 4)
        rsi_bounce = max(0.0, min(0.85, (cur_rsi5 - min_rsi5) / 12.0)) if cur_rsi5 > min_rsi5 else 0.20 * oversold_depth
        momentum_score = round(max(momentum_score, rsi_bounce), 4)
        rvol_part = max(0.0, min(0.88, max_rvol5 / 2.0))
        participation_score = round(max(participation_score, rvol_part), 4)
        if no_chase_1m:
            struct_part = (0.35 if floor_ok else 0.15) + (0.35 if green_5m else 0.0) + (0.15 if buy_ratio >= 0.50 else 0.0)
            structure_score = round(max(structure_score, min(0.85, struct_part)), 4)

    factors = {
        "trend": trend_score,
        "momentum": momentum_score,
        "participation": participation_score,
        "structure": structure_score,
    }
    return factors, [reason for reason, ok in checks.items() if not ok]
