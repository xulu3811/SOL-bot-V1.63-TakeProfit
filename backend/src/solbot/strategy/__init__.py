"""Pure, symbol-isolated P2 research evaluations; no execution dependency."""

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

from solbot.storage.models import INTERVAL_MS, timestamp_ms
from . import indicators, market_regime, r1_reversal, signal_scoring, t1_pullback, t2_swing

VERSION = "p2-research-1"
WINDOW = 120
WARMUP = 60
SYMBOLS = frozenset({"SOLUSDT", "SUIUSDT"})


def prepare(symbol: str, candles_by_interval: dict, as_of_ms: int) -> tuple[dict, list[str]]:
    if symbol not in SYMBOLS:
        raise ValueError("unsupported strategy symbol")
    timestamp_ms(as_of_ms)
    selected, errors = {}, []
    for interval, step in INTERVAL_MS.items():
        eligible = []
        for row in candles_by_interval.get(interval, []):
            if row.get("symbol") != symbol or row.get("interval") != interval:
                errors.append(f"SYMBOL_OR_INTERVAL_MISMATCH:{interval}")
                continue
            if row.get("is_closed") not in (True, 1) or row.get("close_time", as_of_ms + 1) > as_of_ms:
                continue
            eligible.append(row)
        eligible.sort(key=lambda row: row["open_time"])
        selected[interval] = rows = eligible[-WINDOW:]
        if len(rows) < WARMUP:
            errors.append(f"WARMUP:{interval}")
        expected_close = ((as_of_ms + 1) // step) * step - 1
        if not rows or rows[-1]["close_time"] != expected_close:
            errors.append(f"STALE_OR_MISSING_CLOSE:{interval}")
        previous = None
        for row in rows:
            try:
                opening = timestamp_ms(row["open_time"])
                if opening % step or row["close_time"] != opening + step - 1:
                    raise ValueError("unaligned")
                if previous is not None and opening != previous + step:
                    errors.append(f"GAP_OR_DUPLICATE:{interval}")
                previous = opening
                prices = [Decimal(row[key]) for key in ("open", "high", "low", "close")]
                volumes = [Decimal(row[key]) for key in ("volume", "quote_volume", "taker_buy_quote_volume")]
                if any(not v.is_finite() or v <= 0 or not -30 <= v.adjusted() <= 30 for v in prices):
                    raise ValueError("invalid price")
                if any(not v.is_finite() or v < 0 or (v and not -30 <= v.adjusted() <= 30) for v in volumes):
                    raise ValueError("invalid volume")
                o, h, low, c = prices
                if low > min(o, c) or h < max(o, c) or volumes[2] > volumes[1]:
                    raise ValueError("inconsistent candle")
            except (ValueError, TypeError, InvalidOperation, KeyError):
                errors.append(f"INVALID_CANDLE:{interval}")
    return selected, sorted(set(errors))


def _execution_factor(volatility: float, micro_features: dict | None, reasons: list[str]) -> tuple[float, bool]:
    """Score OHLCV volatility ceiling (0..0.5) plus optional live microstructure (0..0.5)."""
    vol_ok = 0 < volatility <= 0.03
    if not vol_ok:
        reasons.append("VOLATILITY_OUTSIDE_RESEARCH_LIMIT")
    vol_half = 0.5 if vol_ok else 0.0
    if not isinstance(micro_features, dict) or not micro_features.get("ready"):
        return vol_half, False
    try:
        spread_bps = float(micro_features["spread_bps"])
        imbalance = float(micro_features.get("imbalance") or 0.0)
    except (KeyError, TypeError, ValueError):
        return vol_half, False
    if spread_bps > 20.0:
        reasons.append("SPREAD_TOO_WIDE")
        spread_score = 0.0
    elif spread_bps <= 5.0:
        spread_score = 0.25
    elif spread_bps <= 15.0:
        spread_score = 0.25 * (15.0 - spread_bps) / 10.0
    else:
        spread_score = 0.0
    imb_clamped = max(-1.0, min(1.0, imbalance))
    imb_score = 0.25 * (imb_clamped + 1.0) / 2.0
    return round(vol_half + spread_score + imb_score, 6), True


def _strategy_stop_distance(name: str, price: Decimal, candles: dict, metrics: dict) -> Decimal | None:
    if price <= 0:
        return None
    if name == "T2" and candles.get("15m"):
        swing = min(Decimal(str(row["low"])) for row in candles["15m"][-3:])
        atr = Decimal(str(metrics["15m"]["atr14"][-1]))
        return t2_swing.stop_distance(price, swing, atr)
    return Decimal("0.010")


def evaluate(symbol: str, candles_by_interval: dict, as_of_ms: int, micro_features: dict | None = None) -> list[dict]:
    candles, errors = prepare(symbol, candles_by_interval, as_of_ms)
    metrics = {interval: indicators.series(rows) for interval, rows in candles.items()} if not errors else {}
    if not errors:
        for interval, values in metrics.items():
            if any(series[-1] is None for series in values.values()):
                errors.append(f"INDICATOR_NOT_READY:{interval}")
    regime, regime_flags = market_regime.classify(candles, metrics) if not errors else ("DATA_NOT_READY", [])
    snapshot = {interval: {name: indicators.number(values[-1]) for name, values in series.items()}
                for interval, series in metrics.items()}
    for interval in snapshot:
        snapshot[interval]["close_time"] = candles[interval][-1]["close_time"]
        snapshot[interval]["warmup_bars"] = len(candles[interval])
    candidate_time = candles["5m"][-1]["close_time"] if candles["5m"] else as_of_ms
    results = []
    for name, module in (("T1", t1_pullback), ("R1", r1_reversal), ("T2", t2_swing)):
        reasons = list(errors)
        factors = dict.fromkeys(("trend", "momentum", "participation", "structure", "execution"), 0.0)
        micro_observed = False
        if not errors:
            factors, reasons = module.assess(candles, metrics, regime)
            volatility = metrics["5m"]["atr14"][-1] / float(candles["5m"][-1]["close"])
            # When live microstructure is absent, only the volatility half (0.5)
            # is observable in OHLCV; live spread/imbalance unlocks the upper half.
            factors["execution"], micro_observed = _execution_factor(volatility, micro_features, reasons)
        scored = signal_scoring.score(factors)
        if scored["score"] < 75:
            reasons.append("SCORE_BELOW_75")
        window = INTERVAL_MS["15m"] if name == "T2" else INTERVAL_MS["5m"]
        expires_at = candidate_time + window
        if as_of_ms >= expires_at:
            reasons.append("CANDIDATE_EXPIRED")
        price = Decimal(candles["1m"][-1]["close"]) if candles["1m"] and not errors else Decimal(0)
        identity = f"{VERSION}:{symbol}:{name}:{as_of_ms}"
        flags = list(regime_flags) + ["RESEARCH_PARAMETERS_UNVALIDATED"]
        if not micro_observed:
            flags.append("EXECUTION_QUALITY_UNOBSERVED")
        if name == "R1":
            flags.append("R1_LIVE_PERMANENTLY_DISABLED")
        result = {"symbol": symbol, "strategy_id": name, "signal_time": as_of_ms,
                  "candidate_time": candidate_time, "expires_at": expires_at,
                  "regime": regime, **scored, "entry_low": str(price),
                  "entry_high": str(price * Decimal("1.001")), "side": "BUY",
                  "indicators": snapshot, "risk_flags": sorted(set(flags)),
                  "strategy_version": VERSION, "accepted": not reasons,
                  "reasons": sorted(set(reasons)), "signal_id": sha256(identity.encode()).hexdigest()}
        if not errors:
            distance = _strategy_stop_distance(name, price, candles, metrics)
            if distance is not None:
                result["stop_distance"] = format(distance, "f")
                result["stop_price"] = format(price * (1 - distance), "f")
        results.append(result)
    # One directional intent per symbol/bar. The two regimes are currently
    # exclusive, but preserve deterministic arbitration if research changes.
    accepted = sorted(
        (r for r in results if r["accepted"]),
        key=lambda r: (0 if r["strategy_id"] == "T1" else 1, -r["score"], r["strategy_id"]),
    )
    for result in accepted[1:]:
        result["accepted"] = False
        result["reasons"].append("SYMBOL_INTENT_CONFLICT")
    return results


def provenance(candles: dict) -> dict:
    """Source values exclude ingestion timing, which is irrelevant to OHLC replay."""
    fields = ("symbol", "interval", "open_time", "close_time", "open", "high", "low", "close",
              "volume", "quote_volume", "taker_buy_quote_volume", "is_closed")
    return {interval: {"bars": len(rows), "first_open": rows[0]["open_time"] if rows else None,
                       "last_close": rows[-1]["close_time"] if rows else None,
                       "sha256": sha256(json.dumps([{key: row.get(key) for key in fields} for row in rows],
                                                  sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            for interval, rows in candles.items()}
