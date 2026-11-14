"""Comprehensive 6-month SOLUSDT historical backtest matching current production code.

Evaluates T1 (pullback), R1 (reversal), T2 (swing) using solbot.strategy,
simulates production/research books (including synced APTS from apts_exit.py &
trend_exhaustion.py, T2 shadow/p3 exits, and profit_trail), and also runs the
standalone S1 research short-term strategy on the exact same 6-month window.
Does not place orders or read secrets.
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

ROOT = Path(r"D:\SOL-Bot")
sys.path.insert(0, str(ROOT / "backend" / "src"))

from solbot.mathematics.decimal_utils import ceil_step, clip, floor_step
from solbot.mathematics.execution_cost import FeeSchedule
from solbot.mathematics.profit_trail import trail_line
from solbot.mathematics.protective_stop import initial_stop, profit_floor
from solbot.strategy import (
    evaluate,
    indicators,
    market_regime,
    r1_reversal,
    signal_scoring,
    t1_pullback,
    t2_swing,
)
from solbot.strategy.s1_execution import simulate as s1_simulate
from solbot.strategy.s1_short import blocked_at, downtrend_lookup, select_opportunities

DB = ROOT / "data" / "solusdt_6m.sqlite"
OUT = ROOT / "data" / "solusdt_6m_backtest_v2.json"
STEPS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
FEE = FeeSchedule(bnb_discount_enabled=True).rate("TAKER")  # 0.00075
TICK = Decimal("0.01")
STEP = Decimal("0.001")
APTS_SLIP = Decimal("0.0005")
START = Decimal("2000")
MODULES = (("T1", t1_pullback), ("R1", r1_reversal), ("T2", t2_swing))


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
        invalid = int(
            np.count_nonzero(
                (l > np.minimum(o, c))
                | (h < np.maximum(o, c))
                | (l <= 0)
                | (tb - q > 1e-6)
                | (q < 0)
            )
        )
        audit["gaps"][tf] = gaps + bad_close
        audit["off_cent"][tf] = off
        audit["invalid_ohlc"][tf] = invalid
        audit["zero_volume"][tf] = int(np.count_nonzero(v <= 0))
        frames[tf] = {
            "open_time": ot,
            "close_time": ct,
            "o": o,
            "h": h,
            "l": l,
            "c": c,
            "v": v,
            "q": q,
            "tb": tb,
            "co": oc[0],
            "ch": oc[1],
            "cl": oc[2],
            "cc": oc[3],
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
            rows.append(
                {
                    "open_time": int(fr["open_time"][j]),
                    "close_time": int(fr["close_time"][j]),
                    "open": float(fr["o"][j]),
                    "high": float(fr["h"][j]),
                    "low": float(fr["l"][j]),
                    "close": float(fr["c"][j]),
                    "volume": float(fr["v"][j]),
                    "quote_volume": float(fr["q"][j]),
                    "taker_buy_quote_volume": float(fr["tb"][j]),
                }
            )
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
            one.append(
                {
                    "open_time": int(fr["open_time"][j]),
                    "close_time": int(fr["close_time"][j]),
                    "open": float(fr["o"][j]),
                    "high": float(fr["h"][j]),
                    "low": float(fr["l"][j]),
                    "close": float(fr["c"][j]),
                }
            )
        candles["1m"] = one
        regime, _flags = market_regime.classify(candles, metrics)
        candidate = int(candles["5m"][-1]["close_time"])
        vol = metrics["5m"]["atr14"][-1] / candles["5m"][-1]["close"]
        cents = int(fr["cc"][i])
        px = Decimal(cents) / Decimal(100)

        out = {}
        for name, module in MODULES:
            factors, reasons = module.assess(candles, metrics, regime)
            factors = dict(factors)
            factors["execution"] = 0.5 if 0 < vol <= 0.03 else 0.0
            reasons = list(reasons)
            if not 0 < vol <= 0.03:
                reasons.append("VOLATILITY_OUTSIDE_RESEARCH_LIMIT")
            scored = signal_scoring.score(factors)
            if scored["score"] < 75:
                reasons.append("SCORE_BELOW_75")
            win_ms = STEPS["15m"] if name == "T2" else STEPS["5m"]
            expires = candidate + win_ms
            if as_of >= expires:
                reasons.append("CANDIDATE_EXPIRED")
            stop_dist = Decimal("0.008")
            if name == "T2":
                swing = min(Decimal(str(row["low"])) for row in candles["15m"][-3:])
                atr15 = Decimal(str(metrics["15m"]["atr14"][-1]))
                stop_dist = t2_swing.stop_distance(px, swing, atr15)
            out[name] = {
                "accepted": not reasons,
                "reasons": sorted(set(reasons)),
                "score": scored["score"],
                "vol": vol,
                "expires": expires,
                "stop_distance": stop_dist,
            }

        # Raw per-strategy accepted flags before cross-strategy arbitration
        raw_accepted = {name: out[name]["accepted"] for name, _ in MODULES}
        winners = sorted(
            (k for k, v in out.items() if v["accepted"]),
            key=lambda k: (-out[k]["score"], k),
        )
        for key in winners[1:]:
            out[key]["accepted"] = False
            out[key]["reasons"] = sorted(set(out[key]["reasons"] + ["SYMBOL_INTENT_CONFLICT"]))

        return {
            "ready": True,
            "as_of": as_of,
            "i": i,
            "regime": regime,
            "candidate": candidate,
            "close": one[-1]["close"],
            "px": px,
            "limit": px * Decimal("1.001"),
            "results": out,
            "raw_accepted": raw_accepted,
            "rsi": metrics["5m"]["rsi6"][-1],
            "rvol": metrics["5m"]["rvol20"][-1],
        }


def bundle_for_evaluate(conn, as_of):
    candles = {}
    for tf, step in STEPS.items():
        rows = conn.execute(
            """SELECT open_time_ms, close_time_ms, o, h, l, c, volume_base, volume_quote, taker_buy_quote
               FROM candles WHERE symbol='SOLUSDT' AND timeframe=? AND close_time_ms<=?
               ORDER BY open_time_ms DESC LIMIT 120""",
            (tf, as_of),
        ).fetchall()
        rows = rows[::-1]
        candles[tf] = [
            {
                "symbol": "SOLUSDT",
                "interval": tf,
                "open_time": r[0],
                "close_time": r[1],
                "open": r[2],
                "high": r[3],
                "low": r[4],
                "close": r[5],
                "volume": r[6],
                "quote_volume": r[7],
                "taker_buy_quote_volume": r[8],
                "is_closed": 1,
            }
            for r in rows
        ]
    return candles


def compare(conn, engine, i):
    mine = engine.decide(i)
    as_of = mine["as_of"] if mine["ready"] else int(engine.f["1m"]["close_time"][i])
    official = evaluate("SOLUSDT", bundle_for_evaluate(conn, as_of), as_of)
    problems = []
    if not mine["ready"]:
        if any(row["accepted"] for row in official) or not any(
            "STALE" in r or "WARMUP" in r or "GAP" in r or "INDICATOR" in r
            for row in official
            for r in row["reasons"]
        ):
            problems.append("ready")
        return problems
    by = {row["strategy_id"]: row for row in official}
    if by["T1"]["regime"] != mine["regime"]:
        problems.append(f"regime {mine['regime']} != {by['T1']['regime']}")
    for name in ("T1", "R1", "T2"):
        if bool(by[name]["accepted"]) != bool(mine["results"][name]["accepted"]):
            problems.append(
                f"{name} accepted {mine['results'][name]['accepted']} != {by[name]['accepted']}"
            )
        elif set(by[name]["reasons"]) != set(mine["results"][name]["reasons"]):
            problems.append(
                f"{name} reasons {mine['results'][name]['reasons']} != {by[name]['reasons']}"
            )
        if name == "T2" and Decimal(by[name]["stop_distance"]) != mine["results"][name]["stop_distance"]:
            problems.append(
                f"T2 stop_distance {mine['results'][name]['stop_distance']} != {by[name]['stop_distance']}"
            )
    return problems


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

    reasons = {k: Counter() for k in ("T1", "R1", "T2")}
    scores = {k: Counter() for k in ("T1", "R1", "T2")}
    regimes = Counter()
    seen_per_strategy = {k: set() for k in ("T1", "R1", "T2")}
    raw_signals = []
    checked = []
    t0 = time.perf_counter()
    ready = 0

    for i in range(start, end + 1):
        decision = engine.decide(i)
        if not decision["ready"]:
            continue
        ready += 1
        regimes[decision["regime"]] += 1

        for name in ("T1", "R1", "T2"):
            result = decision["results"][name]
            reasons[name].update(result["reasons"])
            scores[name][f"{result['score']:.4f}"] += 1
            if decision["raw_accepted"][name] and decision["candidate"] not in seen_per_strategy[name]:
                seen_per_strategy[name].add(decision["candidate"])
                horizons = {}
                for horizon in (15, 60, 240, 1440):
                    j = i + horizon
                    if j <= end:
                        horizons[str(horizon)] = {
                            "close": float(m1["c"][j] / decision["close"] - 1),
                            "limit": float(m1["c"][j] / float(decision["limit"]) - 1),
                        }
                raw_signals.append(
                    {
                        "i": i,
                        "t": decision["as_of"],
                        "strategy": name,
                        "regime": decision["regime"],
                        "candidate": decision["candidate"],
                        "expires": result["expires"],
                        "limit": str(decision["limit"]),
                        "px": str(decision["px"]),
                        "stop_distance": str(result["stop_distance"]),
                        "rsi": decision["rsi"],
                        "rvol": decision["rvol"],
                        "vol": result["vol"],
                        "score": result["score"],
                        "arbitrated_accepted": result["accepted"],
                        "horizons": horizons,
                    }
                )
        if len(checked) < 40 and (
            any(decision["results"][k]["accepted"] for k in ("T1", "R1", "T2"))
            or ready % 20000 == 0
        ):
            checked.append(i)
        if ready % 100000 == 0:
            print(
                f"scanned {ready} ready minutes, raw signals {len(raw_signals)}, {time.perf_counter()-t0:.0f}s",
                flush=True,
            )

    # Cross-check all accepted signal indices + periodic samples
    sig_indices = [s["i"] for s in raw_signals[:30]]
    sample = sorted(set(checked + sig_indices + [start + 1440, end - 10]))
    mismatches = []
    for i in sample:
        if start <= i <= end:
            problems = compare(conn, engine, i)
            if problems:
                mismatches.append({"i": i, "t": iso(int(m1["close_time"][i])), "problems": problems})
    print(
        f"scan done ready={ready} raw_signals={len(raw_signals)} cross={len(sample)} mismatches={len(mismatches)} {time.perf_counter()-t0:.0f}s",
        flush=True,
    )
    if mismatches:
        raise SystemExit(json.dumps(mismatches[:5], ensure_ascii=False))

    by_strategy_raw = Counter(s["strategy"] for s in raw_signals)
    by_strategy_arb = Counter(s["strategy"] for s in raw_signals if s["arbitrated_accepted"])
    by_month = defaultdict(Counter)
    for s in raw_signals:
        m = datetime.fromtimestamp(s["t"] / 1000, timezone.utc).strftime("%Y-%m")
        by_month[m][s["strategy"]] += 1

    signal_report = {
        "ready_minutes": ready,
        "regimes": dict(regimes),
        "raw_candidates_by_strategy": dict(by_strategy_raw),
        "arbitrated_candidates_by_strategy": dict(by_strategy_arb),
        "by_month_raw": {m: dict(c) for m, c in sorted(by_month.items())},
        "reasons_top5": {k: dict(v.most_common(8)) for k, v in reasons.items()},
        "crosscheck_samples": len(sample),
    }
    return raw_signals, signal_report, start, end


def summarize_horizons(raw_signals, strategy, fee):
    out = {}
    subset = [s for s in raw_signals if s["strategy"] == strategy]
    for horizon in ("15", "60", "240", "1440"):
        vals = [s["horizons"][horizon]["limit"] - fee for s in subset if horizon in s["horizons"]]
        if not vals:
            out[horizon] = None
            continue
        arr = np.asarray(vals, dtype=float)
        std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
        tstat = float(arr.mean() / (std / np.sqrt(len(arr)))) if std else None
        out[horizon] = {
            "n": int(len(arr)),
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "win": float(np.mean(arr > 0)),
            "std": std,
            "t": tstat,
        }
    return out


def trail_distance(regime, atr, price):
    if regime == "STRONG_BREAKOUT":
        raw = 2 * atr / price
        return min(Decimal("0.012"), max(Decimal("0.008"), raw))
    if regime == "WEAKENING":
        return Decimal("0.004")
    return Decimal("0.005")


def trend_name(ev):
    # Synced with backend/src/solbot/mathematics/trend_exhaustion.py
    selling = ev["buy"] < 0.4 and ev["buy"] < ev["prev_buy"]
    momentum = ev["rsi"] < ev["prev_rsi"] - 8
    severe = ev["prev_atr"] > 0 and ev["atr"] > ev["prev_atr"] * 2 and ev["adverse"] and selling
    if severe:
        return "SEVERE_ADVERSE_MOVE"
    if ev["structure_broken"] and selling and momentum:
        return "CONFIRMED_REVERSAL"
    if ((selling or momentum) and ev["failed_new_high"]) or ev["structure_broken"]:
        return "WEAKENING"
    if (
        ev["t4"]
        and ev["t1"]
        and ev["t15"]
        and ev["rvol"] >= 2
        and ev["buy"] >= 0.6
        and ev["buy"] >= ev["prev_buy"]
        and ev["hl1"]
        and ev["hl5"]
    ):
        return "STRONG_BREAKOUT"
    if ev["buy"] < ev["prev_buy"] - 0.1 or momentum:
        return "WEAKENING"
    return "NORMAL_UPTREND"


class Book:
    def __init__(self, name, strategies, mode_map, lock, one_per_candidate=True):
        """mode_map maps strategy -> 'apts' | 'price' | 't2_shadow' | 't2_apts' | 'fixed'."""
        self.name = name
        self.strategies = set(strategies)
        self.mode_map = mode_map
        self.lock_on = lock
        self.one_per_candidate = one_per_candidate
        self.seen_candidates = set()
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
        strat = signal["strategy"]
        if strat not in self.strategies:
            return
        if self.one_per_candidate:
            if signal["candidate"] in self.seen_candidates:
                self.skip["candidate_conflict"] += 1
                return
            self.seen_candidates.add(signal["candidate"])
        if self.locked:
            self.skip["lock"] += 1
            return
        if len(self.positions) + len(self.pending) >= 2:
            self.skip["slots"] += 1
            return
        equity = self.last_eq
        available = self.cash - self.reserved()
        stop_dist = Decimal(signal["stop_distance"])
        loss_fraction = stop_dist + FEE + FEE + Decimal("0.002")
        score = Decimal(str(signal["score"]))
        span = min(Decimal("1"), max(Decimal("0"), (score - Decimal("75")) / Decimal("25")))
        n_score = Decimal("300") + span * Decimal("400")
        budget = Decimal("0.004") if strat in ("T1", "T2") else Decimal("0.0025")
        permitted = min(n_score, Decimal("700"), equity * budget / loss_fraction, available / (1 + FEE))
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
        self.pending.append(
            {
                "strategy": strat,
                "regime": signal["regime"],
                "limit": limit,
                "qty": qty,
                "reserve": reserve,
                "expires": signal["expires"],
                "signal_t": signal["t"],
                "candidate": signal["candidate"],
                "stop_distance": stop_dist,
            }
        )

    def close(self, pos, price, reason, t):
        proceeds = pos["qty"] * price
        fee = proceeds * FEE
        pnl = proceeds - fee - pos["cost"] - pos["entry_fee"]
        self.cash += proceeds - fee
        self.fees += fee + pos["entry_fee"]
        risk = pos["qty"] * (pos["entry"] - pos["initial_stop"])
        self.trades.append(
            {
                "strategy": pos["strategy"],
                "regime": pos["regime"],
                "reason": reason,
                "entry_t": pos["entry_t"],
                "exit_t": t,
                "hold_min": (t - pos["entry_t"]) / 60_000,
                "pnl": float(pnl),
                "ret": float(pnl / pos["cost"]),
                "r": float(pnl / risk) if risk else None,
                "mfe": pos["mfe"],
                "mae": pos["mae"],
                "terminal": reason == "DATA_END",
            }
        )
        self.positions.remove(pos)


def evidence_at(frames, rows5, mets, i):
    closes = np.array([row["close"] for row in rows5])
    highs = np.array([row["high"] for row in rows5])
    lows = np.array([row["low"] for row in rows5])
    rsi = wilder_rsi(closes)
    prev_rsi = wilder_rsi(closes[:-1])
    atr = wilder_atr(highs, lows, closes)
    prev_atr = wilder_atr(highs[:-1], lows[:-1], closes[:-1])
    quote, prev_quote = rows5[-1]["quote_volume"], rows5[-2]["quote_volume"]
    buy = rows5[-1]["taker_buy_quote_volume"] / quote if quote else 0.5
    prev_buy = rows5[-2]["taker_buy_quote_volume"] / prev_quote if prev_quote else 0.5
    base = float(np.mean([row["volume"] for row in rows5[-21:-1]]))
    one_l = frames["1m"]["l"]
    broken_low = min(row["low"] for row in rows5[-4:-1])
    ev = {
        "t4": mets["4h"]["ema20"][-1] > mets["4h"]["ema50"][-1],
        "t1": mets["1h"]["ema20"][-1] > mets["1h"]["ema50"][-1],
        "t15": mets["15m"]["ema20"][-1] > mets["15m"]["ema50"][-1],
        "hl1": bool(one_l[i] >= one_l[i - 1] > one_l[i - 2]),
        "hl5": bool(rows5[-1]["low"] >= rows5[-2]["low"] > rows5[-3]["low"]),
        "structure_broken": rows5[-1]["close"] < broken_low - 0.5 * atr,
        "failed_new_high": rows5[-1]["high"] >= max(row["high"] for row in rows5[-4:-1])
        and rows5[-1]["close"] <= rows5[-2]["close"],
        "adverse": rows5[-1]["close"] < rows5[-2]["close"],
        "rvol": rows5[-1]["volume"] / base if base else 0.0,
        "buy": buy,
        "prev_buy": prev_buy,
        "rsi": rsi,
        "prev_rsi": prev_rsi,
        "atr": atr,
        "prev_atr": prev_atr,
    }
    ev["name"] = trend_name(ev)
    return ev


def step_exit(book, pos, o, h, l, c, ev, as_of):
    mode = book.mode_map[pos["strategy"]]
    if mode == "price":
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

    if mode == "t2_shadow":
        # Matches shadow.py _manage_t2 (stop_distance + research trail >= 1.5% + 24h time stop)
        if l <= pos["stop"]:
            price = o if o <= pos["stop"] else pos["stop"]
            return price, "t2_stop"
        high = max(pos["high"], c)
        pos["high"] = high
        pos["mfe"] = max(pos["mfe"], float(h / pos["entry"] - 1))
        pos["mae"] = min(pos["mae"], float(l / pos["entry"] - 1))
        trail = max(pos["entry"] * Decimal("1.01"), high * Decimal("0.995")) if high >= pos["entry"] * Decimal("1.015") else None
        if trail is not None and c <= trail:
            return c, "t2_trail"
        if as_of - pos["entry_t"] >= 86_400_000:
            return c, "t2_time_stop"
        return None, None

    # Fixed or APTS modes
    if l <= pos["stop"]:
        price = o if o <= pos["stop"] else pos["stop"]
        if o < pos["stop"] * Decimal("0.997"):
            book.gap_exit += 1
        return price, "PROTECTIVE_STOP_TOUCHED"
    if mode == "fixed":
        pos["mfe"] = max(pos["mfe"], float(h / pos["entry"] - 1))
        pos["mae"] = min(pos["mae"], float(l / pos["entry"] - 1))
        return None, None

    # APTS (apts or t2_apts)
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
        # Synced with apts_exit.py line 114: S0 keeps resting stop
        if stage != "S0_INITIAL_PROTECTION":
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
    if mode == "t2_apts" and as_of - pos["entry_t"] >= 86_400_000:
        return bid, "t2_time_stop"
    return None, None


def simulate(frames, raw_signals, start, end):
    books = [
        Book("T1_APTS", ("T1",), {"T1": "apts"}, True),
        Book("T1_PRICE", ("T1",), {"T1": "price"}, True),
        Book("T2_SHADOW", ("T2",), {"T2": "t2_shadow"}, True),
        Book("T2_APTS", ("T2",), {"T2": "t2_apts"}, True),
        Book("T1_T2_PROD_SHADOW", ("T1", "T2"), {"T1": "apts", "T2": "t2_shadow"}, True),
        Book("T1_T2_PROD_APTS", ("T1", "T2"), {"T1": "apts", "T2": "t2_apts"}, True),
        Book("T1R1_APTS", ("T1", "R1"), {"T1": "apts", "R1": "apts"}, True),
        Book("T1R1T2_APTS", ("T1", "R1", "T2"), {"T1": "apts", "R1": "apts", "T2": "t2_apts"}, True),
        Book("T1_FIXED", ("T1",), {"T1": "fixed"}, True),
        Book("T2_SHADOW_NOLOCK", ("T2",), {"T2": "t2_shadow"}, False),
    ]
    by_i = defaultdict(list)
    for signal in raw_signals:
        by_i[signal["i"]].append(signal)
    # Deterministic order per bar: higher score, then alphabetical R1 < T1 < T2
    for i in by_i:
        by_i[i].sort(key=lambda s: (-s["score"], s["strategy"]))

    m1 = frames["1m"]
    ev = None
    ev_key = None
    engine_rows = None
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
        rows = [
            {
                "close": float(closes[k]),
                "high": float(fr["h"][sl][k]),
                "low": float(fr["l"][sl][k]),
                "volume": float(fr["v"][sl][k]),
                "quote_volume": float(fr["q"][sl][k]),
                "taker_buy_quote_volume": float(fr["tb"][sl][k]),
            }
            for k in range(120)
        ]
        mets = {
            "ema20": indicators.ema([row["close"] for row in rows], 20),
            "ema50": indicators.ema([row["close"] for row in rows], 50),
        }
        latest_rows[tf] = (idx, rows, mets)
        return rows, mets

    for i in range(start, end + 1):
        as_of = int(m1["close_time"][i])
        o = Decimal(int(m1["co"][i])) / 100
        h = Decimal(int(m1["ch"][i])) / 100
        l = Decimal(int(m1["cl"][i])) / 100
        c = Decimal(int(m1["cc"][i])) / 100

        need = any(
            book.positions and book.mode_map[p["strategy"]] in ("apts", "t2_apts")
            for book in books
            for p in book.positions
        )
        if need:
            five_idx = ((as_of + 1) // STEPS["5m"]) * STEPS["5m"] - 1
            if ev_key != five_idx:
                packet, mets = {}, {}
                for tf in ("5m", "15m", "1h", "4h"):
                    packet[tf], mets[tf] = rows_at(tf, as_of)
                ev = evidence_at(frames, packet["5m"], mets, i)
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
                    if order["strategy"] == "T2" and book.mode_map["T2"] == "t2_shadow":
                        stop = order["limit"] * (1 - order["stop_distance"])
                    elif order["strategy"] == "T2":
                        stop = ceil_step(order["limit"] * (1 - order["stop_distance"]), TICK)
                    else:
                        stop = initial_stop(order["limit"], TICK)
                    if l <= stop:
                        book.pierce += 1
                    book.positions.append(
                        {
                            "strategy": order["strategy"],
                            "regime": order["regime"],
                            "qty": order["qty"],
                            "entry": order["limit"],
                            "cost": order["qty"] * order["limit"],
                            "entry_fee": fee,
                            "stop": stop,
                            "initial_stop": stop,
                            "floor": stop,
                            "high": order["limit"],
                            "stage": "S0_INITIAL_PROTECTION",
                            "entry_t": as_of,
                            "born": i,
                            "mfe": 0.0,
                            "mae": 0.0,
                        }
                    )
                    book.filled += 1
                    continue
                still.append(order)
            book.pending = still

            for pos in list(book.positions):
                if pos["born"] == i:
                    continue
                price, reason = step_exit(book, pos, o, h, l, c, ev, as_of)
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
    return {
        "qty": str(qty),
        "final": float(final),
        "return": float(final / START - 1),
        "max_dd": max_dd,
        "price_return": price_ret,
        "start_px": float(px0),
        "end_px": float(last),
        "days": days,
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


def run_s1(conn, window_start_ms, window_end_ms):
    def load_tf(tf):
        rows = conn.execute(
            """SELECT open_time_ms, close_time_ms, o, h, l, c
               FROM candles WHERE symbol='SOLUSDT' AND timeframe=? ORDER BY open_time_ms""",
            (tf,),
        ).fetchall()
        return [
            {"open_time": r[0], "close_time": r[1], "open": r[2], "high": r[3], "low": r[4], "close": r[5]}
            for r in rows
        ]

    one_h = load_tf("1h")
    four_h = load_tf("4h")
    times, flags = downtrend_lookup(one_h, four_h)
    five = [r for r in load_tf("5m") if window_start_ms <= int(r["close_time"]) <= window_end_ms]
    for r in five:
        r["blocked"] = blocked_at(int(r["close_time"]), times, flags)
    opportunities = select_opportunities(five)
    minutes = [r for r in load_tf("1m") if window_start_ms <= int(r["close_time"]) <= window_end_ms]
    book = s1_simulate(minutes, opportunities, "2000")
    exits = book["exits"]
    pnls = np.array([float(e["pnl"]) for e in exits], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    return {
        "opportunities": len(opportunities),
        "fills": len(book["fills"]),
        "closed_trades": len(exits),
        "final": float(book["final_equity"]),
        "return": float(book["final_equity"] / 2000 - 1),
        "win": float(np.mean(pnls > 0)) if len(pnls) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() else None,
        "exit_reasons": dict(Counter(e["reason"] for e in exits)),
    }


def main():
    t0 = time.perf_counter()
    conn, frames, window_start, audit = load()
    print("loaded", audit["counts"], "window", audit["window_start"], flush=True)
    if (
        any(audit["gaps"].values())
        or any(audit["invalid_ohlc"].values())
        or any(v > 1e-6 for v in audit["off_cent"].values())
    ):
        raise SystemExit(f"data audit failed: {audit}")

    raw_signals, signal_report, start, end = scan(conn, frames, window_start)
    print("simulating", len(raw_signals), "raw signals across books", flush=True)
    books = simulate(frames, raw_signals, start, end)
    hold = bh_path(frames, start, end)
    dates = [row["date"] for row in hold["days"]]

    round_trip = float(FEE * 2)
    event_study = {k: summarize_horizons(raw_signals, k, round_trip) for k in ("T1", "R1", "T2")}

    book_report = {}
    for book in books:
        stats = trade_stats(book)
        stats.update(sharpe_block([row["equity"] for row in book.equity_day]))
        aligned = {row["date"]: row["equity"] for row in book.equity_day}
        stats["monthly"] = monthly_returns(
            [{"date": d, "equity": aligned[d]} for d in dates if d in aligned], "equity"
        )
        book_report[book.name] = stats

    s1_report = run_s1(conn, int(frames["1m"]["close_time"][start]), int(frames["1m"]["close_time"][end]))
    conn.close()

    payload = {
        "strategy_version": "p2-research-1+t2",
        "fee_rate": str(FEE),
        "window": {
            "start": iso(int(frames["1m"]["close_time"][start])),
            "end": iso(int(frames["1m"]["close_time"][end])),
            "start_ms": int(frames["1m"]["close_time"][start]),
            "end_ms": int(frames["1m"]["close_time"][end]),
        },
        "audit": audit,
        "signals": signal_report,
        "event_study": event_study,
        "market": {
            "start_px": hold["start_px"],
            "end_px": hold["end_px"],
            "price_return": hold["price_return"],
            "buyhold_return": hold["return"],
            "buyhold_max_dd": hold["max_dd"],
            "buyhold_final": hold["final"],
            **sharpe_block([row["equity"] for row in hold["days"]]),
        },
        "books": book_report,
        "s1_standalone": s1_report,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "window": payload["window"],
        "fee": str(FEE),
        "signals": signal_report["raw_candidates_by_strategy"],
        "arbitrated": signal_report["arbitrated_candidates_by_strategy"],
        "market": {
            "start_px": hold["start_px"],
            "end_px": hold["end_px"],
            "price_ret": round(hold["price_return"] * 100, 2),
            "bh_ret": round(hold["return"] * 100, 2),
            "bh_dd": round(hold["max_dd"] * 100, 2),
        },
        "books": {
            k: {
                "ret_pct": round(v["return"] * 100, 2),
                "final": round(v["final"], 2),
                "dd_pct": round(v["max_dd"] * 100, 2),
                "trades": v["closed_trades"],
                "win_pct": round(v["win"] * 100, 2) if v["win"] is not None else None,
                "pf": round(v["profit_factor"], 3) if v["profit_factor"] is not None else None,
                "fill_pct": round(v["fill_rate"] * 100, 1) if v["fill_rate"] is not None else None,
                "sharpe": round(v["sharpe"], 2) if v["sharpe"] is not None else None,
                "locked": v["lock_at"],
                "reasons": v["exit_reasons"],
                "by_strategy": v["pnl_by_strategy"],
            }
            for k, v in book_report.items()
        },
        "s1_standalone": s1_report,
        "seconds": payload["seconds"],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
