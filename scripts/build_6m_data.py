"""Assemble a 6-month SOLUSDT store and fill the tail from Binance.

The audited year file supplies history through 2026-11-10. This script copies
the warmup plus the six-month window, then appends closed candles through now.
It does not place orders or read secrets.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YEAR = ROOT / "data" / "solusdt_1y.sqlite"
OUT = ROOT / "data" / "solusdt_6m.sqlite"
SYMBOL = "SOLUSDT"
STEPS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
WINDOW_START = datetime(2026, 5, 13, tzinfo=timezone.utc)
HISTORY_START = datetime(2026, 3, 29, tzinfo=timezone.utc)
WINDOW_MS = int(WINDOW_START.timestamp() * 1000)
HISTORY_MS = int(HISTORY_START.timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "solbot-6m"})
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")


def klines(interval: str, start_ms: int, end_ms: int):
    step = STEPS[interval]
    cursor = start_ms
    rows = []
    while cursor <= end_ms:
        url = (
            "https://api.binance.com/api/v3/klines?symbol=SOLUSDT"
            f"&interval={interval}&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        batch = fetch(url)
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1000:
            break
        time.sleep(0.12)
    return rows


def main():
    now_ms = int(time.time() * 1000)
    if OUT.exists():
        OUT.unlink()
    src = sqlite3.connect(f"file:{YEAR.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(OUT)
    dst.execute(
        """CREATE TABLE candles (
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            open_time_ms INTEGER NOT NULL, close_time_ms INTEGER NOT NULL,
            o TEXT NOT NULL, h TEXT NOT NULL, l TEXT NOT NULL, c TEXT NOT NULL,
            volume_base TEXT NOT NULL, volume_quote TEXT NOT NULL, taker_buy_quote TEXT NOT NULL,
            closed INTEGER NOT NULL CHECK(closed=1),
            PRIMARY KEY(symbol, timeframe, open_time_ms)
        )"""
    )
    dst.execute(
        """CREATE TABLE kline_fetch (
            symbol TEXT PRIMARY KEY,
            window_start_ms INTEGER NOT NULL,
            history_start_ms INTEGER NOT NULL,
            window_days INTEGER NOT NULL,
            warmup_days INTEGER NOT NULL,
            fetched_at_ms INTEGER NOT NULL
        )"""
    )
    copied = {}
    for tf in STEPS:
        rows = src.execute(
            """SELECT symbol, timeframe, open_time_ms, close_time_ms, o, h, l, c,
                      volume_base, volume_quote, taker_buy_quote, closed
               FROM candles WHERE symbol=? AND timeframe=? AND open_time_ms>=?
               ORDER BY open_time_ms""",
            (SYMBOL, tf, HISTORY_MS),
        ).fetchall()
        dst.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        copied[tf] = len(rows)
        print(f"copied {tf} {len(rows)} last {iso(rows[-1][3]) if rows else 'none'}", flush=True)
    dst.commit()

    added = {}
    for tf, step in STEPS.items():
        last_open = dst.execute(
            "SELECT MAX(open_time_ms) FROM candles WHERE timeframe=?", (tf,)
        ).fetchone()[0]
        start = int(last_open) + step
        raw = klines(tf, start, now_ms)
        fresh = []
        for k in raw:
            close_time = int(k[6])
            if close_time >= now_ms or int(k[0]) < start:
                continue
            fresh.append((
                SYMBOL, tf, int(k[0]), close_time, k[1], k[2], k[3], k[4],
                k[5], k[7], k[10], 1,
            ))
        if fresh:
            dst.executemany("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", fresh)
        added[tf] = len(fresh)
        print(f"appended {tf} {len(fresh)} from {iso(start)}", flush=True)
    dst.commit()

    # Three stored bars must still match Binance before the tail is trusted.
    checks = []
    anchors = {
        "1m": int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1000),
        "5m": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
        "1h": int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000),
    }
    for tf, open_ms in anchors.items():
        local = dst.execute(
            "SELECT o,h,l,c,volume_base,volume_quote FROM candles WHERE timeframe=? AND open_time_ms=?",
            (tf, open_ms),
        ).fetchone()
        remote = klines(tf, open_ms, open_ms + STEPS[tf] - 1)
        if not local or not remote:
            checks.append({"tf": tf, "open": iso(open_ms), "ok": False, "reason": "missing"})
            continue
        got = remote[0]
        same = [local[0], local[1], local[2], local[3], local[4], local[5]] == [
            got[1], got[2], got[3], got[4], got[5], got[7]
        ]
        checks.append({"tf": tf, "open": iso(open_ms), "ok": same, "local_close": local[3], "binance_close": got[4]})
        print("spot", checks[-1], flush=True)

    audit = {"copied": copied, "appended": added, "spot": checks, "gaps": {}, "span": {}}
    failed = False
    for tf, step in STEPS.items():
        times = [r[0] for r in dst.execute(
            "SELECT open_time_ms, close_time_ms FROM candles WHERE timeframe=? ORDER BY open_time_ms",
            (tf,),
        )]
        closes = [r[0] for r in dst.execute(
            "SELECT close_time_ms FROM candles WHERE timeframe=? ORDER BY open_time_ms", (tf,)
        )]
        gaps = 0
        for a, b in zip(times, times[1:]):
            if b - a != step:
                gaps += 1
                if gaps <= 3:
                    print(f"gap {tf} {iso(a)} -> {iso(b)}", flush=True)
        bad_close = sum(c != o + step - 1 for o, c in zip(times, closes))
        audit["gaps"][tf] = gaps + bad_close
        audit["span"][tf] = {"n": len(times), "start": iso(times[0]), "end": iso(closes[-1])}
        if gaps or bad_close:
            failed = True
    end_ms = dst.execute("SELECT MAX(close_time_ms) FROM candles WHERE timeframe='1m'").fetchone()[0]
    window_days = round((end_ms - WINDOW_MS) / 86_400_000, 2)
    dst.execute(
        "INSERT INTO kline_fetch VALUES (?,?,?,?,?,?)",
        (SYMBOL, WINDOW_MS, HISTORY_MS, int(round(window_days)), 45, now_ms),
    )
    dst.commit()
    audit["window_start"] = iso(WINDOW_MS)
    audit["history_start"] = iso(HISTORY_MS)
    audit["window_end"] = iso(end_ms)
    audit["window_days"] = window_days
    audit["spot_ok"] = all(row["ok"] for row in checks)
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)
    src.close()
    dst.close()
    if failed or not audit["spot_ok"]:
        raise SystemExit("6m data audit failed")


if __name__ == "__main__":
    main()
