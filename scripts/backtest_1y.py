"""One-year SOLUSDT research replay of p2-research-1, plus a labeled account translation.

Signal decisions call the production strategy modules. The account books are not
the production executor: APTS is applied to one-minute OHLC under the assumptions
printed in the output. This file does not place orders or read secrets.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from solbot.mathematics.decimal_utils import ceil_step, floor_step
from solbot.mathematics.execution_cost import FeeSchedule
from solbot.mathematics.profit_trail import trail_line
from solbot.mathematics.protective_stop import initial_stop, profit_floor
from solbot.strategy import evaluate, indicators, market_regime, r1_reversal, signal_scoring, t1_pullback

DB = ROOT / "data" / "solusdt_1y.sqlite"
OUT = ROOT / "data" / "solusdt_1y_backtest_bnb.json"
STEPS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
FIELDS = ("open_time", "high", "low", "close", "volume", "quote_volume")
FEE = FeeSchedule(bnb_discount_enabled=True).rate("TAKER")
TICK = Decimal("0.01")
STEP = Decimal("0.001")
APTS_SLIP = Decimal("0.0005")
START = Decimal("2000")
LOSS_FRACTION = Decimal("0.008") + FEE + FEE + Decimal("0.002")
CUTOFF_DAY = "2026-05-10"
N_SCORE = Decimal("580")  # 300 + 400 * (92.5 - 75) / 25
MODULES = (("T1", t1_pullback), ("R1", r1_reversal))
BINDING = {
    "T1": [
        "REGIME_NOT_UPTREND", "RSI_PULLBACK_NOT_RECOVERED", "RVOL_BELOW_1_2",
        "VWAP_OR_SUPPORT_NOT_RECLAIMED", "ONE_MINUTE_BREAKOUT_UNCONFIRMED",
        "VOLATILITY_OUTSIDE_RESEARCH_LIMIT", "SCORE_BELOW_75", "CANDIDATE_EXPIRED",
        "SYMBOL_INTENT_CONFLICT",
    ],
    "R1": [
        "R1_REQUIRES_RANGE", "ACCELERATING_DECLINE", "RSI_OVERSOLD_NOT_RECOVERED",
        "RVOL_BELOW_1_5", "TAKER_BUY_NOT_RECOVERED", "FIVE_MINUTE_FLOOR_UNCONFIRMED",
        "ONE_MINUTE_BREAKOUT_UNCONFIRMED", "VOLATILITY_OUTSIDE_RESEARCH_LIMIT",
        "SCORE_BELOW_75", "CANDIDATE_EXPIRED", "SYMBOL_INTENT_CONFLICT",
    ],
}


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def load():
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    window_start, history_start, window_days, warmup_days = conn.execute(
        "SELECT window_start_ms, history_start_ms, window_days, warmup_days FROM kline_fetch"
    ).fetchone()
    frames = {}
    audit = {"invalid_ohlc": {}, "zero_volume": {}, "off_cent": {}, "gaps": {}}
    for tf, step in STEPS.items():
        rows = conn.execute(
            """SELECT open_time_ms, close_time_ms, o, h, l, c, volume_base, volume_quote, taker_buy_quote
               FROM candles WHERE symbol='SOLUSDT' AND timeframe=? ORDER BY open_time_ms""",
            (tf,),
        ).fetchall()
        ot = np.array([r[0] for r in rows], dtype=np.int64)
        ct = np.array([r[1] for r in rows], dtype=np.int64)
        o = np.array([float(r[2]) for r in rows])
        h = np.array([float(r[3]) for r in rows])
        l = np.array([float(r[4]) for r in rows])
        c = np.array([float(r[5]) for r in rows])
        v = np.array([float(r[6]) for r in rows])
        q = np.array([float(r[7]) for r in rows])
        tb = np.array([float(r[8]) for r in rows])
        gaps = int(np.count_nonzero(np.diff(ot) != step)) if len(ot) else 0
        bad_close = int(np.count_nonzero(ct != ot + step - 1))
        oc = np.rint(np.stack([o, h, l, c]) * 100).astype(np.int64)
        off = float(np.max(np.abs(oc / 100 - np.stack([o, h, l, c])))) if len(o) else 0.0
        invalid = int(np.count_nonzero((l > np.minimum(o, c)) | (h < np.maximum(o, c)) | (l <= 0) | (tb - q > 1e-6) | (q < 0)))
        audit["gaps"][tf] = gaps + bad_close
        audit["off_cent"][tf] = off
        audit["invalid_ohlc"][tf] = invalid
        audit["zero_volume"][tf] = int(np.count_nonzero(v <= 0))
        frames[tf] = {
            "open_time": ot, "close_time": ct, "o": o, "h": h, "l": l, "c": c, "v": v, "q": q, "tb": tb,
            "co": oc[0], "ch": oc[1], "cl": oc[2], "cc": oc[3],
        }
    audit["window_start"] = iso(window_start)
    audit["history_start"] = iso(history_start)
    audit["window_days"] = window_days
    audit["warmup_days"] = warmup_days
    audit["counts"] = {tf: int(len(frames[tf]["c"])) for tf in STEPS}
    return conn, frames, int(window_start), audit


def wilder_rsi(closes, period=6):
    ch = np.diff(closes)
    gain = float(np.maximum(ch[:period], 0).sum() / period)
    loss = float(np.maximum(-ch[:period], 0).sum() / period)

    def value(g, l):
        if g == 0 and l == 0:
            return 50.0
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    for x in ch[period:]:
        gain = (gain * (period - 1) + max(float(x), 0.0)) / period
        loss = (loss * (period - 1) + max(-float(x), 0.0)) / period
    return value(gain, loss)


def wilder_atr(h, l, c, period=14):
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    val = float(tr[:period].sum() / period)
    for x in tr[period:]:
        val = (val * (period - 1) + float(x)) / period
    return val


class Engine:
    def __init__(self, frames):
        self.f = frames
        self.cache = {}
        self.ev_cache = None

    def end_index(self, tf, as_of):
        step = STEPS[tf]
        expected = ((as_of + 1) // step) * step - 1
        open_t = expected - step + 1
        first = int(self.f[tf]["open_time"][0])
        if (open_t - first) % step:
            return None
        idx = (open_t - first) // step
        fr = self.f[tf]
        if idx < 119 or idx >= len(fr["open_time"]):
            return None
        if int(fr["open_time"][idx]) != open_t or int(fr["close_time"][idx]) != expected:
            return None
        return idx

    def window(self, tf, idx):
        slot = self.cache.get(tf)
        if slot and slot[0] == idx:
            return slot[1], slot[2]
        fr = self.f[tf]
        rows = []
        for j in range(idx - 119, idx + 1):
            rows.append({
                "open_time": int(fr["open_time"][j]), "close_time": int(fr["close_time"][j]),
                "open": float(fr["o"][j]), "high": float(fr["h"][j]), "low": float(fr["l"][j]),
                "close": float(fr["c"][j]), "volume": float(fr["v"][j]),
                "quote_volume": float(fr["q"][j]), "taker_buy_quote_volume": float(fr["tb"][j]),
            })
        met = indicators.series(rows)
        self.cache[tf] = (idx, rows, met)
        return rows, met

    def decide(self, i):
        fr = self.f["1m"]
        as_of = int(fr["close_time"][i])
        idxs = {}
        for tf in STEPS:
            idx = self.end_index(tf, as_of)
            if idx is None:
                return {"ready": False, "as_of": as_of}
            idxs[tf] = idx
        candles, metrics = {}, {}
        for tf in ("5m", "15m", "1h", "4h"):
            rows, met = self.window(tf, idxs[tf])
            candles[tf] = rows
            metrics[tf] = met
            if any(met[name][-1] is None for name in met):
                return {"ready": False, "as_of": as_of}
        one = []
        for j in range(i - 2, i + 1):
            one.append({
                "open_time": int(fr["open_time"][j]), "close_time": int(fr["close_time"][j]),
                "open": float(fr["o"][j]), "high": float(fr["h"][j]), "low": float(fr["l"][j]),
                "close": float(fr["c"][j]),
            })
        candles["1m"] = one
        regime, _flags = market_regime.classify(candles, metrics)
        candidate = int(candles["5m"][-1]["close_time"])
        expires = candidate + STEPS["5m"]
        vol = metrics["5m"]["atr14"][-1] / candles["5m"][-1]["close"]
        packet = {"5m": candles["5m"], "1m": one}
        metric_packet = {"5m": metrics["5m"]}
        out = {}
        for name, module in MODULES:
            factors, reasons = module.assess(packet, metric_packet, regime)
            factors = dict(factors)
            factors["execution"] = 0.5 if 0 < vol <= 0.03 else 0.0
            reasons = list(reasons)
            if not 0 < vol <= 0.03:
                reasons.append("VOLATILITY_OUTSIDE_RESEARCH_LIMIT")
            scored = signal_scoring.score(factors)
            if scored["score"] < 75:
                reasons.append("SCORE_BELOW_75")
            if as_of >= expires:
                reasons.append("CANDIDATE_EXPIRED")
            out[name] = {"accepted": not reasons, "reasons": sorted(set(reasons)),
                         "score": scored["score"], "vol": vol}
        winners = sorted((k for k, v in out.items() if v["accepted"]), key=lambda k: (-out[k]["score"], k))
        for key in winners[1:]:
            out[key]["accepted"] = False
            out[key]["reasons"] = sorted(set(out[key]["reasons"] + ["SYMBOL_INTENT_CONFLICT"]))
        close = one[-1]["close"]
        cents = int(fr["cc"][i])
        px = Decimal(cents) / Decimal(100)
        return {"ready": True, "as_of": as_of, "i": i, "regime": regime, "candidate": candidate,
                "expires": expires, "close": close, "px": px, "limit": px * Decimal("1.001"),
                "results": out, "rsi": metrics["5m"]["rsi6"][-1], "rvol": metrics["5m"]["rvol20"][-1],
                "rows5": candles["5m"], "metrics": metrics, "idxs": idxs}


def bundle_for_evaluate(conn, as_of):
    candles = {}
    for tf, step in STEPS.items():
        expected = ((as_of + 1) // step) * step - 1
        rows = conn.execute(
            """SELECT open_time_ms, close_time_ms, o, h, l, c, volume_base, volume_quote, taker_buy_quote
               FROM candles WHERE symbol='SOLUSDT' AND timeframe=? AND close_time_ms<=?
               ORDER BY open_time_ms DESC LIMIT 120""",
            (tf, as_of),
        ).fetchall()
        rows = rows[::-1]
        candles[tf] = [{
            "symbol": "SOLUSDT", "interval": tf, "open_time": r[0], "close_time": r[1],
            "open": r[2], "high": r[3], "low": r[4], "close": r[5], "volume": r[6],
            "quote_volume": r[7], "taker_buy_quote_volume": r[8], "is_closed": 1,
        } for r in rows]
    return candles


def compare(conn, engine, i):
    mine = engine.decide(i)
    official = evaluate("SOLUSDT", bundle_for_evaluate(conn, mine["as_of"] if mine["ready"] else int(engine.f["1m"]["close_time"][i])), int(engine.f["1m"]["close_time"][i]))
    problems = []
    if not mine["ready"]:
        if any(row["accepted"] or "STALE_OR_MISSING_CLOSE" not in "".join(row["reasons"]) and "WARMUP" not in "".join(row["reasons"]) for row in official):
            if any(row["accepted"] for row in official) or not any("STALE" in r or "WARMUP" in r or "GAP" in r or "INDICATOR" in r for row in official for r in row["reasons"]):
                problems.append("ready")
        return problems
    by = {row["strategy_id"]: row for row in official}
    if by["T1"]["regime"] != mine["regime"]:
        problems.append(f"regime {mine['regime']} != {by['T1']['regime']}")
    for name in ("T1", "R1"):
        if bool(by[name]["accepted"]) != bool(mine["results"][name]["accepted"]):
            problems.append(f"{name} accepted {mine['results'][name]['accepted']} != {by[name]['accepted']} mine={mine['results'][name]['reasons']} official={by[name]['reasons']}")
        elif set(by[name]["reasons"]) != set(mine["results"][name]["reasons"]):
            problems.append(f"{name} reasons {mine['results'][name]['reasons']} != {by[name]['reasons']}")
        if mine["results"][name]["accepted"] and Decimal(by[name]["entry_high"]) != mine["limit"]:
            problems.append(f"{name} limit {mine['limit']} != {by[name]['entry_high']}")
    return problems


def binding(strategy, reasons):
    for reason in BINDING[strategy]:
        if reason in reasons:
            return reason
    return reasons[0] if reasons else "ACCEPTED"


def scan(conn, frames, window_start):
    engine = Engine(frames)
    m1 = frames["1m"]
    start = int(np.searchsorted(m1["close_time"], window_start, side="left"))
    end = len(m1["close_time"]) - 1
    warmup = []
    for i in (start, start + 7, start + 60, start + 500):
        warmup.extend(compare(conn, engine, i))
    if warmup:
        raise SystemExit("early cross-check failed: " + " | ".join(warmup))
    print(f"early cross-check passed at {iso(int(m1['close_time'][start]))}", flush=True)

    reasons = {k: Counter() for k in ("T1", "R1")}
    binding_counts = {k: Counter() for k in ("T1", "R1")}
    scores = {k: Counter() for k in ("T1", "R1")}
    regimes = Counter()
    hours = Counter()
    weekdays = Counter()
    months = Counter()
    seen = set()
    setups = {"T1": set(), "R1": set()}
    signals = []
    baseline = defaultdict(list)  # name -> list[(index, gross return)]
    checked = []
    t0 = time.perf_counter()
    ready = 0
    for i in range(start, end + 1):
        decision = engine.decide(i)
        if not decision["ready"]:
            continue
        ready += 1
        regimes[decision["regime"]] += 1
        stamp = datetime.fromtimestamp(decision["as_of"] / 1000, timezone.utc)
        five_close = int(decision["candidate"]) == decision["as_of"]
        if five_close:
            j = i + 60
            if j <= end:
                gross = float(m1["c"][j] / decision["close"] - 1)
                baseline["all"].append((i, gross))
                baseline[decision["regime"]].append((i, gross))
        for name in ("T1", "R1"):
            result = decision["results"][name]
            reasons[name].update(result["reasons"])
            binding_counts[name][binding(name, result["reasons"]) if result["reasons"] else "ACCEPTED"] += 1
            scores[name][f"{result['score']:.4f}"] += 1
            only_confirm = set(result["reasons"]) == {"ONE_MINUTE_BREAKOUT_UNCONFIRMED"}
            if result["accepted"] or only_confirm:
                setups[name].add(decision["candidate"])
            if result["accepted"] and abs(result["score"] - 92.5) > 1e-6:
                raise SystemExit(f"accepted score is not 92.5: {result}")
            if result["accepted"] and decision["candidate"] not in seen:
                seen.add(decision["candidate"])
                horizons = {}
                for horizon in (15, 60, 240, 1440):
                    j = i + horizon
                    if j <= end:
                        horizons[str(horizon)] = {
                            "close": float(m1["c"][j] / decision["close"] - 1),
                            "limit": float(m1["c"][j] / float(decision["limit"]) - 1),
                        }
                signals.append({
                    "i": i, "t": decision["as_of"], "strategy": name, "regime": decision["regime"],
                    "candidate": decision["candidate"], "expires": decision["expires"],
                    "limit": str(decision["limit"]), "px": str(decision["px"]),
                    "rsi": decision["rsi"], "rvol": decision["rvol"], "vol": result["vol"],
                    "score": result["score"], "horizons": horizons,
                })
                hours[stamp.hour] += 1
                weekdays[stamp.weekday()] += 1
                months[stamp.strftime("%Y-%m")] += 1
                if "60" in horizons:
                    baseline[name].append((i, horizons["60"]["close"]))
        if len(checked) < 24 and (decision["results"]["T1"]["accepted"] or decision["results"]["R1"]["accepted"] or ready % 20000 == 0):
            checked.append(i)
        if ready % 100000 == 0:
            print(f"scanned {ready} ready minutes, signals {len(signals)}, {time.perf_counter()-t0:.0f}s", flush=True)

    mismatches = []
    sample = sorted(set(checked + [start + 1440, end - 10]))
    for i in sample:
        if i < start or i > end:
            continue
        problems = compare(conn, engine, i)
        if problems:
            mismatches.append({"i": i, "t": iso(int(m1["close_time"][i])), "problems": problems})
    print(f"scan done ready={ready} signals={len(signals)} cross={len(sample)} mismatches={len(mismatches)} {time.perf_counter()-t0:.0f}s", flush=True)
    if mismatches:
        raise SystemExit(json.dumps(mismatches[:5], ensure_ascii=False))
    signal_report = {
        "ready_minutes": ready,
        "regimes": dict(regimes),
        "reasons": {k: dict(v) for k, v in reasons.items()},
        "binding": {k: dict(v) for k, v in binding_counts.items()},
        "scores": {k: dict(v) for k, v in scores.items()},
        "setup_candidates": {k: len(v) for k, v in setups.items()},
        "accepted_candidates": len(signals),
        "by_strategy": dict(Counter(row["strategy"] for row in signals)),
        "by_hour": [hours[h] for h in range(24)],
        "by_weekday": [weekdays[d] for d in range(7)],
        "by_month": dict(sorted(months.items())),
        "crosscheck_samples": len(sample),
    }
    return signals, signal_report, baseline, start, end


def summarize(pairs, fee=0.0, gap=None):
    if gap is not None:
        chosen, nxt = [], -1
        for i, value in pairs:
            if i >= nxt:
                chosen.append(value)
                nxt = i + gap
        values = np.asarray(chosen, dtype=float)
    else:
        values = np.asarray([value for _, value in pairs], dtype=float)
    if len(values) == 0:
        return None
    net = values - fee
    std = float(net.std(ddof=1)) if len(net) > 1 else 0.0
    tstat = float(net.mean() / (std / np.sqrt(len(net)))) if std else None
    return {"n": int(len(net)), "mean": float(net.mean()), "median": float(np.median(net)),
            "win": float(np.mean(net > 0)), "std": std, "t": tstat}


def trail_distance(regime, atr, price):
    if regime == "STRONG_BREAKOUT":
        raw = 2 * atr / price
        return min(Decimal("0.012"), max(Decimal("0.008"), raw))
    if regime == "WEAKENING":
        return Decimal("0.004")
    return Decimal("0.005")


def trend_name(ev):
    selling = ev["buy"] < 0.4 and ev["buy"] < ev["prev_buy"]
    momentum = ev["rsi"] < ev["prev_rsi"] - 8
    severe = ev["prev_atr"] > 0 and ev["atr"] > ev["prev_atr"] * 2 and ev["adverse"] and selling
    if severe:
        return "SEVERE_ADVERSE_MOVE"
    if ev["structure_broken"] and (selling or momentum):
        return "CONFIRMED_REVERSAL"
    if ((selling or momentum) and ev["failed_new_high"]) or ev["structure_broken"]:
        return "WEAKENING"
    if (ev["t4"] and ev["t1"] and ev["t15"] and ev["rvol"] >= 2 and ev["buy"] >= 0.6
            and ev["buy"] >= ev["prev_buy"] and ev["hl1"] and ev["hl5"]):
        return "STRONG_BREAKOUT"
    if ev["buy"] < ev["prev_buy"] - 0.1 or momentum:
        return "WEAKENING"
    return "NORMAL_UPTREND"


class Book:
    def __init__(self, name, strategies, mode, lock):
        self.name, self.strategies, self.mode, self.lock_on = name, set(strategies), mode, lock
        self.cash = START
        self.peak = START
        self.locked = False
        self.lock_t = None
        self.positions = []
        self.pending = []
        self.trades = []
        self.fees = Decimal(0)
        self.skip = Counter()
        self.filled = 0
        self.expired = 0
        self.pierce = 0
        self.gap_exit = 0
        self.exposure = 0
        self.bars = 0
        self.max_dd = 0.0
        self.dd_t = None
        self.equity_day = []
        self.day = None
        self.last_eq = START

    def reserved(self):
        return sum((row["reserve"] for row in self.pending), Decimal(0))

    def equity(self, close_px):
        gross = sum((p["qty"] * close_px * (1 - FEE) for p in self.positions), Decimal(0))
        return self.cash + gross

    def mark(self, close_px, t):
        eq = self.equity(close_px)
        if eq > self.peak:
            self.peak = eq
        dd = float((self.peak - eq) / self.peak)
        if dd > self.max_dd:
            self.max_dd = dd
            self.dd_t = t
        if self.lock_on and not self.locked and eq <= self.peak * Decimal("0.95"):
            self.locked = True
            self.lock_t = t
            self.pending.clear()
        self.last_eq = eq
        key = day_key(t)
        if self.day != key:
            self.day = key
            self.equity_day.append({"date": key, "equity": float(eq)})
        else:
            self.equity_day[-1]["equity"] = float(eq)
        self.bars += 1
        if self.positions:
            self.exposure += 1

    def submit(self, signal):
        if signal["strategy"] not in self.strategies:
            return
        if self.locked:
            self.skip["lock"] += 1
            return
        if len(self.positions) + len(self.pending) >= 2:
            self.skip["slots"] += 1
            return
        equity = self.last_eq
        available = self.cash - self.reserved()
        budget = Decimal("0.004") if signal["strategy"] == "T1" else Decimal("0.0025")
        permitted = min(N_SCORE, Decimal("700"), equity * budget / LOSS_FRACTION, available / (1 + FEE))
        if permitted < 300:
            self.skip["size"] += 1
            return
        limit = Decimal(signal["limit"])
        qty = floor_step(permitted / limit, STEP)
        actual = qty * limit
        if actual < 300 or qty <= 0:
            self.skip["rounded"] += 1
            return
        reserve = actual * (1 + FEE)
        if reserve > available:
            self.skip["cash"] += 1
            return
        self.pending.append({
            "strategy": signal["strategy"], "regime": signal["regime"], "limit": limit, "qty": qty,
            "reserve": reserve, "expires": signal["expires"], "signal_t": signal["t"],
            "candidate": signal["candidate"], "rsi": signal["rsi"], "rvol": signal["rvol"],
        })

    def close(self, pos, price, reason, t):
        proceeds = pos["qty"] * price
        fee = proceeds * FEE
        pnl = proceeds - fee - pos["cost"] - pos["entry_fee"]
        self.cash += proceeds - fee
        self.fees += fee + pos["entry_fee"]
        risk = pos["qty"] * (pos["entry"] - pos["initial_stop"])
        self.trades.append({
            "strategy": pos["strategy"], "regime": pos["regime"], "reason": reason,
            "entry_t": pos["entry_t"], "exit_t": t, "hold_min": (t - pos["entry_t"]) / 60_000,
            "pnl": float(pnl), "ret": float(pnl / pos["cost"]), "r": float(pnl / risk) if risk else None,
            "mfe": pos["mfe"], "mae": pos["mae"], "terminal": reason == "DATA_END",
        })
        self.positions.remove(pos)


def evidence_at(frames, decision):
    rows = decision["rows5"]
    m = decision["metrics"]
    closes = np.array([row["close"] for row in rows])
    highs = np.array([row["high"] for row in rows])
    lows = np.array([row["low"] for row in rows])
    rsi = wilder_rsi(closes)
    prev_rsi = wilder_rsi(closes[:-1])
    atr = wilder_atr(highs, lows, closes)
    prev_atr = wilder_atr(highs[:-1], lows[:-1], closes[:-1])
    quote, prev_quote = rows[-1]["quote_volume"], rows[-2]["quote_volume"]
    buy = rows[-1]["taker_buy_quote_volume"] / quote if quote else 0.5
    prev_buy = rows[-2]["taker_buy_quote_volume"] / prev_quote if prev_quote else 0.5
    base = float(np.mean([row["volume"] for row in rows[-21:-1]]))
    i = decision["i"]
    one_l = frames["1m"]["l"]
    ev = {
        "t4": m["4h"]["ema20"][-1] > m["4h"]["ema50"][-1],
        "t1": m["1h"]["ema20"][-1] > m["1h"]["ema50"][-1],
        "t15": m["15m"]["ema20"][-1] > m["15m"]["ema50"][-1],
        "hl1": bool(one_l[i] >= one_l[i - 1] > one_l[i - 2]),
        "hl5": bool(rows[-1]["low"] >= rows[-2]["low"] > rows[-3]["low"]),
        "structure_broken": rows[-1]["close"] < min(row["low"] for row in rows[-4:-1]),
        "failed_new_high": rows[-1]["high"] >= max(row["high"] for row in rows[-4:-1]) and rows[-1]["close"] <= rows[-2]["close"],
        "adverse": rows[-1]["close"] < rows[-2]["close"],
        "rvol": rows[-1]["volume"] / base if base else 0.0,
        "buy": buy, "prev_buy": prev_buy, "rsi": rsi, "prev_rsi": prev_rsi,
        "atr": atr, "prev_atr": prev_atr,
    }
    ev["name"] = trend_name(ev)
    return ev


def step_price(book, pos, o, h, l):
    """0.8% protective stop, then the research profit line. No RVOL input."""
    if l <= pos["stop"]:
        price = o if o <= pos["stop"] else pos["stop"]
        return price, "PROTECTIVE_STOP_TOUCHED"
    high = max(pos["high"], h)
    pos["high"] = high
    pos["mfe"] = max(pos["mfe"], float(h / pos["entry"] - 1))
    pos["mae"] = min(pos["mae"], float(l / pos["entry"] - 1))
    line = trail_line(pos["entry"], high)
    if line is not None and l <= line:
        return (o if o <= line else line), "PROFIT_TRAIL"
    return None, None


def step_exit(book, pos, o, h, l, c, ev):
    if book.mode == "price":
        return step_price(book, pos, o, h, l)
    if l <= pos["stop"]:
        price = o if o <= pos["stop"] else pos["stop"]
        if o < pos["stop"] * Decimal("0.997"):
            book.gap_exit += 1
        return price, "PROTECTIVE_STOP_TOUCHED"
    if book.mode != "apts":
        pos["mfe"] = max(pos["mfe"], float(h / pos["entry"] - 1))
        pos["mae"] = min(pos["mae"], float(l / pos["entry"] - 1))
        return None, None
    bid = c
    high = max(pos["high"], bid)
    gross = bid / pos["entry"] - 1
    stage = pos["stage"]
    if gross > Decimal("0.015") and stage != "S3_TREND_EXHAUSTION":
        stage = "S2_ADAPTIVE_TRAILING"
    elif gross >= Decimal("0.01") and stage == "S0_INITIAL_PROTECTION":
        stage = "S1_PROFIT_PROTECTION"
    floor = pos["floor"]
    candidate = pos["stop"]
    reason = None
    if stage != "S0_INITIAL_PROTECTION":
        floor = max(floor, profit_floor(pos["entry"], FEE, FEE, APTS_SLIP, TICK))
        if floor >= bid:
            reason = "PROFIT_FLOOR_NOT_EXECUTABLE"
        else:
            candidate = max(candidate, floor)
    regime = ev["name"]
    if stage in ("S2_ADAPTIVE_TRAILING", "S3_TREND_EXHAUSTION"):
        dist = trail_distance(regime, Decimal(str(ev["atr"])), bid)
        candidate = max(candidate, high * (1 - dist))
    if regime in ("CONFIRMED_REVERSAL", "SEVERE_ADVERSE_MOVE"):
        stage = "S3_TREND_EXHAUSTION"
        reason = regime
    candidate = ceil_step(candidate, TICK)
    if reason is None and candidate >= bid:
        reason = "TRAILING_STOP_TOUCHED"
    if reason:
        pos["stage"] = stage
        return bid, reason
    pos["stop"] = max(pos["stop"], candidate) if candidate < bid else pos["stop"]
    pos["floor"] = floor
    pos["high"] = high
    pos["stage"] = stage
    pos["mfe"] = max(pos["mfe"], float(h / pos["entry"] - 1))
    pos["mae"] = min(pos["mae"], float(l / pos["entry"] - 1))
    return None, None


def simulate(frames, signals, start, end):
    books = [
        Book("T1_APTS", ("T1",), "apts", True),
        Book("T1_PRICE", ("T1",), "price", True),
        Book("T1R1_APTS", ("T1", "R1"), "apts", True),
        Book("T1_FIXED", ("T1",), "fixed", True),
        Book("T1_APTS_NOLOCK", ("T1",), "apts", False),
    ]
    by_i = defaultdict(list)
    for signal in signals:
        by_i[signal["i"]].append(signal)
    m1 = frames["1m"]
    ev = None
    ev_key = None
    engine_rows = None
    # Only the latest closed window per timeframe is reused. A year of in-position
    # bars must not retain every historical window.
    latest_rows = {}

    def rows_at(tf, as_of):
        step = STEPS[tf]
        expected = ((as_of + 1) // step) * step - 1
        open_t = expected - step + 1
        first = int(frames[tf]["open_time"][0])
        idx = (open_t - first) // step
        slot = latest_rows.get(tf)
        if slot and slot[0] == idx:
            return slot[1], slot[2]
        fr = frames[tf]
        sl = slice(idx - 119, idx + 1)
        closes = fr["c"][sl]
        rows = [{
            "close": float(closes[k]), "high": float(fr["h"][sl][k]), "low": float(fr["l"][sl][k]),
            "volume": float(fr["v"][sl][k]), "quote_volume": float(fr["q"][sl][k]),
            "taker_buy_quote_volume": float(fr["tb"][sl][k]),
        } for k in range(120)]
        metrics = {"ema20": indicators.ema([row["close"] for row in rows], 20),
                   "ema50": indicators.ema([row["close"] for row in rows], 50)}
        latest_rows[tf] = (idx, rows, metrics)
        return rows, metrics

    for i in range(start, end + 1):
        as_of = int(m1["close_time"][i])
        o = Decimal(int(m1["co"][i])) / 100
        h = Decimal(int(m1["ch"][i])) / 100
        l = Decimal(int(m1["cl"][i])) / 100
        c = Decimal(int(m1["cc"][i])) / 100
        need = any(book.positions and book.mode == "apts" for book in books)
        if need:
            five_idx = (((as_of + 1) // STEPS["5m"]) * STEPS["5m"] - 1)
            if ev_key != five_idx:
                packet = {}
                mets = {}
                for tf in ("5m", "15m", "1h", "4h"):
                    packet[tf], mets[tf] = rows_at(tf, as_of)
                fake = {
                    "rows5": packet["5m"],
                    "metrics": mets,
                    "i": i,
                }
                # hl1 is minute-specific; refresh below.
                ev = evidence_at(frames, fake)
                ev_key = five_idx
                engine_rows = ev
            else:
                ev = dict(engine_rows)
            one_l = m1["l"]
            ev["hl1"] = bool(one_l[i] >= one_l[i - 1] > one_l[i - 2])
            ev["name"] = trend_name(ev)
        for book in books:
            still = []
            for order in book.pending:
                if as_of >= order["expires"]:
                    book.expired += 1
                    continue
                if int(m1["open_time"][i]) > order["signal_t"] and l <= order["limit"]:
                    fee = order["qty"] * order["limit"] * FEE
                    book.cash -= order["qty"] * order["limit"] + fee
                    stop = initial_stop(order["limit"], TICK)
                    if l <= stop:
                        book.pierce += 1
                    book.positions.append({
                        "strategy": order["strategy"], "regime": order["regime"], "qty": order["qty"],
                        "entry": order["limit"], "cost": order["qty"] * order["limit"], "entry_fee": fee,
                        "stop": stop, "initial_stop": stop, "floor": stop, "high": order["limit"],
                        "stage": "S0_INITIAL_PROTECTION", "entry_t": as_of, "born": i,
                        "mfe": 0.0, "mae": 0.0, "rsi": order["rsi"], "rvol": order["rvol"],
                    })
                    book.filled += 1
                    continue
                still.append(order)
            book.pending = still
            for pos in list(book.positions):
                if pos["born"] == i:
                    continue
                price, reason = step_exit(book, pos, o, h, l, c, ev)
                if reason:
                    book.close(pos, price, reason, as_of)
            book.mark(c, as_of)
            for signal in by_i.get(i, []):
                book.submit(signal)
    last_t = int(m1["close_time"][end])
    last_c = Decimal(int(m1["cc"][end])) / 100
    for book in books:
        for pos in list(book.positions):
            book.close(pos, last_c, "DATA_END", last_t)
        book.pending.clear()
        book.last_eq = book.cash
        if book.equity_day:
            book.equity_day[-1]["equity"] = float(book.cash)
    return books


def bh_path(frames, start, end):
    m1 = frames["1m"]
    px0 = Decimal(int(m1["cc"][start])) / 100
    qty = floor_step(START / (px0 * (1 + FEE)), STEP)
    cost = qty * px0
    entry_fee = cost * FEE
    cash = START - cost - entry_fee
    peak = START
    max_dd = 0.0
    days = []
    day = None
    for i in range(start, end + 1):
        px = Decimal(int(m1["cc"][i])) / 100
        eq = cash + qty * px * (1 - FEE)
        if eq > peak:
            peak = eq
        max_dd = max(max_dd, float((peak - eq) / peak))
        key = day_key(int(m1["close_time"][i]))
        if day != key:
            day = key
            days.append({"date": key, "equity": float(eq), "sol": float(px)})
        else:
            days[-1]["equity"] = float(eq)
            days[-1]["sol"] = float(px)
    last = Decimal(int(m1["cc"][end])) / 100
    proceeds = qty * last
    exit_fee = proceeds * FEE
    final = cash + proceeds - exit_fee
    days[-1]["equity"] = float(final)
    price_ret = float(last / px0 - 1)
    return {"qty": str(qty), "final": float(final), "return": float(final / START - 1),
            "max_dd": max_dd, "price_return": price_ret, "start_px": float(px0), "end_px": float(last),
            "days": days}


def trade_stats(book):
    done = [row for row in book.trades if not row["terminal"]]
    pnls = np.array([row["pnl"] for row in done], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    streak = worst = 0
    for value in pnls:
        streak = streak + 1 if value <= 0 else 0
        worst = max(worst, streak)
    holds = np.array([row["hold_min"] for row in done], dtype=float)
    rs = np.array([row["r"] for row in done if row["r"] is not None], dtype=float)
    by_reason = Counter(row["reason"] for row in book.trades)
    by_strategy = Counter(row["strategy"] for row in done)
    return {
        "final": float(book.cash),
        "return": float(book.cash / START - 1),
        "max_dd": book.max_dd,
        "max_dd_at": iso(book.dd_t) if book.dd_t else None,
        "fees": float(book.fees),
        "filled": book.filled,
        "expired_unfilled": book.expired,
        "fill_rate": book.filled / (book.filled + book.expired) if book.filled + book.expired else None,
        "skips": dict(book.skip),
        "same_bar_pierce": book.pierce,
        "gap_beyond_0_3pct": book.gap_exit,
        "exposure": book.exposure / book.bars if book.bars else 0,
        "locked": book.locked,
        "lock_at": iso(book.lock_t) if book.lock_t else None,
        "closed_trades": int(len(done)),
        "terminal_marks": int(sum(row["terminal"] for row in book.trades)),
        "win": float(np.mean(pnls > 0)) if len(pnls) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() else None,
        "avg_pnl": float(pnls.mean()) if len(pnls) else None,
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
        "best": float(pnls.max()) if len(pnls) else None,
        "worst": float(pnls.min()) if len(pnls) else None,
        "mean_r": float(rs.mean()) if len(rs) else None,
        "median_hold_min": float(np.median(holds)) if len(holds) else None,
        "p90_hold_min": float(np.quantile(holds, 0.9)) if len(holds) else None,
        "losing_streak": int(worst),
        "exit_reasons": dict(by_reason),
        "by_strategy": dict(by_strategy),
        "pnl_by_strategy": {k: float(sum(row["pnl"] for row in done if row["strategy"] == k)) for k in by_strategy},
    }


def sharpe_block(equities):
    vals = np.array(equities, dtype=float)
    rets = np.diff(vals) / vals[:-1]
    if len(rets) < 2 or float(rets.std(ddof=1)) == 0:
        return {"sharpe": None, "sortino": None, "days": int(len(vals))}
    sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(365))
    downside = np.minimum(rets, 0)
    dstd = float(np.sqrt(np.mean(downside ** 2)))
    sortino = float(rets.mean() / dstd * np.sqrt(365)) if dstd else None
    return {"sharpe": sharpe, "sortino": sortino, "days": int(len(vals))}


def weekly(dates, values):
    out_d, out_v = [], []
    for i, date in enumerate(dates):
        year, week, _ = datetime.strptime(date, "%Y-%m-%d").isocalendar()
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        boundary = nxt is None or datetime.strptime(nxt, "%Y-%m-%d").isocalendar()[:2] != (year, week)
        if boundary:
            out_d.append(date[5:])
            out_v.append(round(values[i] / 2000 * 100, 2))
    return out_d, out_v


def monthly_returns(days, key):
    buckets = defaultdict(list)
    for row in days:
        buckets[row["date"][:7]].append(row[key])
    out = []
    prev = 2000.0
    for month in sorted(buckets):
        last = buckets[month][-1]
        out.append({"month": month, "return": last / prev - 1})
        prev = last
    return out


def main():
    t0 = time.perf_counter()
    conn, frames, window_start, audit = load()
    print("loaded", audit["counts"], "window", audit["window_start"], flush=True)
    if any(audit["gaps"].values()) or any(audit["invalid_ohlc"].values()) or any(v > 1e-6 for v in audit["off_cent"].values()):
        raise SystemExit(f"data audit failed: {audit}")
    signals, signal_report, baseline, start, end = scan(conn, frames, window_start)
    print("simulating", len(signals), "signals", flush=True)
    books = simulate(frames, signals, start, end)
    hold = bh_path(frames, start, end)
    m1 = frames["1m"]
    sol_closes = np.array([row["sol"] for row in hold["days"]])
    sol_ret = np.diff(np.log(sol_closes))
    vol = float(sol_ret.std(ddof=1) * np.sqrt(365)) if len(sol_ret) > 1 else None
    round_trip = float(FEE * 2)
    event = {
        "all_5m_60m": summarize(baseline["all"], round_trip),
        "all_5m_60m_nonoverlap": summarize(baseline["all"], round_trip, 60),
        "uptrend_60m": summarize(baseline["UPTREND"], round_trip),
        "uptrend_60m_nonoverlap": summarize(baseline["UPTREND"], round_trip, 60),
        "range_60m": summarize(baseline["RANGE"], round_trip),
        "range_60m_nonoverlap": summarize(baseline["RANGE"], round_trip, 60),
        "T1_60m_close": summarize(baseline["T1"], round_trip),
        "T1_60m_nonoverlap": summarize(baseline["T1"], round_trip, 60),
        "R1_60m_close": summarize(baseline["R1"], round_trip),
        "R1_60m_nonoverlap": summarize(baseline["R1"], round_trip, 60),
    }
    for label, key in (("T1", "T1"), ("R1", "R1")):
        for horizon in ("15", "60", "240", "1440"):
            pairs = [(n, row["horizons"][horizon]["limit"]) for n, row in enumerate(signals)
                     if row["strategy"] == label and horizon in row["horizons"]]
            event[f"{label}_limit_{horizon}m"] = summarize([(i, v) for i, v in pairs], round_trip)
    book_report = {}
    dates = [row["date"] for row in hold["days"]]
    series = {"SOL price": [], "Buy and hold": []}
    sol0 = hold["days"][0]["sol"]
    for row in hold["days"]:
        series["SOL price"].append(round(row["sol"] / sol0 * 100, 2))
        series["Buy and hold"].append(round(row["equity"] / 2000 * 100, 2))
    for book in books:
        stats = trade_stats(book)
        stats.update(sharpe_block([row["equity"] for row in book.equity_day]))
        aligned = {row["date"]: row["equity"] for row in book.equity_day}
        stats["monthly"] = monthly_returns([{"date": d, "equity": aligned[d]} for d in dates if d in aligned], "equity")
        book_report[book.name] = stats
        series[book.name] = [round(aligned[d] / 2000 * 100, 2) if d in aligned else None for d in dates]
    week_dates, _ = weekly(dates, [2000.0] * len(dates))
    # Rebuild weekly from the actual series rather than the dummy scale above.
    weekly_series = {}
    for name, values in series.items():
        labels, points = [], []
        for i, date in enumerate(dates):
            year, week, _ = datetime.strptime(date, "%Y-%m-%d").isocalendar()
            nxt = dates[i + 1] if i + 1 < len(dates) else None
            if nxt is None or datetime.strptime(nxt, "%Y-%m-%d").isocalendar()[:2] != (year, week):
                labels.append(date)
                points.append(values[i])
        weekly_series[name] = points
        week_dates = labels

    def half(book):
        prior = [row["equity"] for row in book.equity_day if row["date"] < CUTOFF_DAY]
        cutoff_equity = prior[-1] if prior else float(START)
        final = float(book.cash)
        return {"cutoff": CUTOFF_DAY, "equity_at_cutoff": cutoff_equity,
                "first_return": cutoff_equity / float(START) - 1,
                "second_return": final / cutoff_equity - 1}

    halves = {book.name: half(book) for book in books}
    price_second = halves["T1_PRICE"]["second_return"]
    apts_second = halves["T1_APTS"]["second_return"]
    accepted = price_second > 0 and price_second > apts_second
    payload = {
        "strategy_version": "p2-research-1",
        "fee_rate": str(FEE),
        "profit_trail_accepted": accepted,
        "promotion": "SECOND_HALF_BEAT_CASH_AND_APTS" if accepted else "RESEARCH_ONLY",
        "window": {"start": iso(int(m1["close_time"][start])), "end": iso(int(m1["close_time"][end])),
                   "start_ms": int(m1["close_time"][start]), "end_ms": int(m1["close_time"][end])},
        "audit": audit,
        "signals": signal_report,
        "event_study": event,
        "market": {"start_px": hold["start_px"], "end_px": hold["end_px"], "price_return": hold["price_return"],
                   "annual_vol": vol, "buyhold_return": hold["return"], "buyhold_max_dd": hold["max_dd"],
                   "buyhold_final": hold["final"], **sharpe_block([row["equity"] for row in hold["days"]])},
        "books": book_report,
        "walk_forward": halves,
        "weekly": {"dates": week_dates, "series": weekly_series},
        "months": {
            "sol": monthly_returns(hold["days"], "sol"),
            "buyhold": monthly_returns(hold["days"], "equity"),
        },
        "assumptions": [
            "Signals are the production evaluate gates: T1 pullback and R1 reversal, version p2-research-1.",
            "One accepted intent per 5m candidate. R1 remains live-disabled in the product; T1_APTS is the relevant book.",
            "Limit buy at close*1.001, filled only on a later 1m bar whose low trades at or below the limit and whose close is before expiry. No price improvement.",
            "Size follows size_position at score 92.5: notional min(580, equity*risk/loss_fraction, cash), floor 300, cap 700. T1 risk budget 0.4%, R1 0.25%. Step 0.001, tick 0.01.",
            "Fee is the BNB taker rate 0.075% per side from FeeSchedule(bnb_discount_enabled=True). See docs/回测准则.md.",
            "T1_PRICE uses profit_trail.trail_line. It does not read RVOL. It is not wired into update_protection.",
            "APTS is translated from update_protection onto 1m OHLC. Intrabar low can touch the stop. Trail ratchets on the close. Flow buy_ratio is the 5m taker-buy quote ratio because the file has no trade tape.",
            "A 5% peak-equity lock cancels new entries and stays on. It does not flatten positions. T1_APTS_NOLOCK removes only that lock.",
            "T1_FIXED keeps the initial 0.8% stop and never trails.",
            "Positions still open at the last bar are sold at that close and excluded from win rate.",
            "Event-study t statistics on overlapping minutes overstate significance. Nonoverlap versions require a 60-minute gap.",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "window": payload["window"], "signals": signal_report["by_strategy"],
        "setups": signal_report["setup_candidates"], "regimes": signal_report["regimes"],
        "fee": str(FEE),
        "books": {k: {"ret": v["return"], "dd": v["max_dd"], "trades": v["closed_trades"], "win": v["win"],
                      "pf": v["profit_factor"], "fill": v["fill_rate"], "lock": v["lock_at"], "final": v["final"],
                      "reasons": v["exit_reasons"]}
                  for k, v in book_report.items()},
        "walk_forward": halves, "promotion": payload["promotion"],
        "bh": hold["return"], "sol": hold["price_return"], "seconds": round(time.perf_counter() - t0, 1),
    }, indent=2))


if __name__ == "__main__":
    main()
