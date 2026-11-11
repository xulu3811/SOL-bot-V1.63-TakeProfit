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
    recovery = any(32 <= value <= 48 for value in rsi[-4:-1]) and rsi[-1] > rsi[-2] and rsi[-1] > 48
    participation = m["rvol20"][-1] >= 1.2
    last, previous = rows[-1], rows[-2]
    reclaim = (float(last["close"]) >= m["vwap20"][-1]
               or (float(last["low"]) >= min(float(row["low"]) for row in rows[-5:-1])
                   and float(last["close"]) > float(previous["high"])))
    confirmed = confirm_1m(candles["1m"], last["close_time"])
    checks = {"REGIME_NOT_UPTREND": regime == "UPTREND", "RSI_PULLBACK_NOT_RECOVERED": recovery,
              "RVOL_BELOW_1_2": participation, "VWAP_OR_SUPPORT_NOT_RECLAIMED": reclaim,
              "ONE_MINUTE_BREAKOUT_UNCONFIRMED": confirmed}
    factors = {"trend": float(regime == "UPTREND"), "momentum": float(recovery),
               "participation": float(participation), "structure": (float(reclaim) + float(confirmed)) / 2}
    return factors, [reason for reason, ok in checks.items() if not ok]
