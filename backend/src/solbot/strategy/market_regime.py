"""Initial research regime thresholds; no calibration or profitability claim."""


def classify(candles: dict, metrics: dict) -> tuple[str, list[str]]:
    h4, h1, m15 = (metrics[key] for key in ("4h", "1h", "15m"))
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
    if not up4 and not up1 and strength >= 25:
        return "DOWNTREND", ["HIGHER_TIMEFRAME_DOWNTREND"]
    slope = abs(latest(h1, "ema20") - h1["ema20"][-4]) / float(candles["1h"][-1]["close"])
    if strength < 20 and slope <= 0.003:
        return "RANGE", []
    return "UNCERTAIN", ["HIGHER_TIMEFRAME_DISAGREEMENT"]
