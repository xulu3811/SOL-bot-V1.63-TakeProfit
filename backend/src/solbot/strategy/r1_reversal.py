"""Research-only reversal. No live permission is issued by this package."""

from .t1_pullback import confirm_1m


def assess(candles: dict, metrics: dict, regime: str) -> tuple[dict, list[str]]:
    rows, m = candles["5m"], metrics["5m"]
    rsi = m["rsi6"]
    recovery = min(rsi[-5:-1]) <= 20 and rsi[-1] > 28 and rsi[-1] > rsi[-2]
    participation = m["rvol20"][-1] >= 1.5
    ratios = [float(row["taker_buy_quote_volume"]) / float(row["quote_volume"])
              if float(row["quote_volume"]) > 0 else 0 for row in rows[-3:]]
    buyers = ratios[-1] >= 0.5 and ratios[-1] > sum(ratios[:-1]) / 2
    last, previous = rows[-1], rows[-2]
    stabilizing = (float(last["low"]) >= float(previous["low"])
                   and float(last["close"]) > float(previous["high"]))
    accelerating = (float(rows[-1]["close"]) < float(rows[-2]["close"]) < float(rows[-3]["close"])
                    and m["adx14"][-1] > m["adx14"][-2])
    confirmed = confirm_1m(candles["1m"], last["close_time"])
    macro_ok = float(candles["4h"][-1]["close"]) >= float(metrics["4h"]["ema50"][-1]) * 0.99
    checks = {"R1_REQUIRES_RANGE": regime == "RANGE", "MACRO_DOWNTREND_GUARD": macro_ok,
              "ACCELERATING_DECLINE": not accelerating,
              "RSI_OVERSOLD_NOT_RECOVERED": recovery, "RVOL_BELOW_1_5": participation,
              "TAKER_BUY_NOT_RECOVERED": buyers, "FIVE_MINUTE_FLOOR_UNCONFIRMED": stabilizing,
              "ONE_MINUTE_BREAKOUT_UNCONFIRMED": confirmed}
    factors = {"trend": float(regime == "RANGE"), "momentum": float(recovery),
               "participation": (float(participation) + float(buyers)) / 2,
               "structure": (float(stabilizing) + float(confirmed)) / 2}
    return factors, [reason for reason, ok in checks.items() if not ok]
