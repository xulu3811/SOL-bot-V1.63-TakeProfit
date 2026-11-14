"""Count frozen S1 opportunities and research fills once. Does not retune."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from solbot.storage.s1_store import S1Store
from solbot.strategy.s1_execution import ASSUMPTION, simulate
from solbot.strategy.s1_short import (
    BLOCK_MS,
    MIN_RANGE,
    VERSION,
    blocked_at,
    downtrend_lookup,
    select_opportunities,
)

DB = ROOT / "data" / "solusdt_1y.sqlite"
OUT = ROOT / "data" / "s1_frequency.json"
STORE = ROOT / "data" / "s1_research.sqlite"
WINDOW_START = 1762759559999
WINDOW_END = 1794295499999
START_EQUITY = "2000"


def load(conn, timeframe):
    rows = conn.execute(
        """SELECT open_time_ms, close_time_ms, o, h, l, c
           FROM candles WHERE symbol='SOLUSDT' AND timeframe=? ORDER BY open_time_ms""",
        (timeframe,),
    ).fetchall()
    return [
        {"open_time": row[0], "close_time": row[1], "open": row[2], "high": row[3], "low": row[4], "close": row[5]}
        for row in rows
    ]


def day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def week_key(ms: int) -> str:
    year, week, _ = datetime.fromtimestamp(ms / 1000, timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


def median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return float(ordered[mid - 1] + ordered[mid]) / 2


def main():
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    one_h = load(conn, "1h")
    four_h = load(conn, "4h")
    times, flags = downtrend_lookup(one_h, four_h)
    five = [
        row for row in load(conn, "5m")
        if WINDOW_START <= int(row["close_time"]) <= WINDOW_END
    ]
    for row in five:
        row["blocked"] = blocked_at(int(row["close_time"]), times, flags)
    opportunities = select_opportunities(five)
    minutes = [
        row for row in load(conn, "1m")
        if WINDOW_START <= int(row["close_time"]) <= WINDOW_END
    ]
    conn.close()
    book = simulate(minutes, opportunities, START_EQUITY)

    days = []
    cursor = datetime.fromtimestamp(WINDOW_START / 1000, timezone.utc).date()
    end = datetime.fromtimestamp(WINDOW_END / 1000, timezone.utc).date()
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
    by_day = Counter(day_key(int(row["close_time"])) for row in opportunities)
    day_counts = [by_day[day] for day in days]
    by_week = Counter(week_key(int(row["entry_ms"])) for row in book["fills"])
    weeks = sorted({week_key(int(row["close_time"])) for row in minutes})
    week_counts = [by_week[week] for week in weeks]
    status_counts = Counter(row["status"] for row in book["orders"])
    share = sum(count >= 1 for count in day_counts) / len(day_counts)
    opportunity_median = median(day_counts)
    fill_median = median(week_counts)
    opportunity_ok = 3 <= opportunity_median <= 5 and share >= 0.80
    fill_ok = 7 <= fill_median <= 10
    pnl = sum((row["pnl"] for row in book["exits"]), start=0)
    payload = {
        "strategy_version": VERSION,
        "thresholds_frozen_before_count": {
            "min_range": str(MIN_RANGE),
            "block_ms": BLOCK_MS,
            "hold_ms": 24 * 60 * 60 * 1000,
            "limit_ms": 5 * 60 * 1000,
            "slots": 1,
            "downtrend": "existing 4h/1h EMA and 1h ADX>=25",
        },
        "opportunity_days": len(days),
        "opportunity_total": len(opportunities),
        "opportunity_median": opportunity_median,
        "days_with_at_least_one": share,
        "opportunity_in_band": opportunity_ok,
        "fill_weeks": len(weeks),
        "fill_total": len(book["fills"]),
        "fill_median": fill_median,
        "fill_in_band": fill_ok,
        "frequency_accepted": opportunity_ok and fill_ok,
        "promotion": "NOT_ELIGIBLE_SEALED_WINDOW",
        "order_status": dict(status_counts),
        "pierce": book["pierce"],
        "locked": book["locked"],
        "final_equity": str(book["final_equity"]),
        "diagnostic_pnl_not_for_promotion": str(pnl),
        "assumption": ASSUMPTION,
        "daily_counts": day_counts,
        "weekly_counts": [{"week": week, "fills": by_week[week]} for week in weeks],
    }
    minute_closes = {int(row["close_time"]) for row in minutes}
    missing = [row for row in opportunities if int(row["close_time"]) not in minute_closes]
    if missing:
        raise SystemExit(f"missing 1m close for {len(missing)} opportunities")
    for suffix in ("", "-wal", "-shm"):
        side = Path(str(STORE) + suffix) if suffix else STORE
        if side.exists():
            side.unlink()
    store = S1Store(STORE)
    try:
        store.record(opportunities, book["orders"], book["fills"], book["exits"])
        store.record(opportunities, book["orders"], book["fills"], book["exits"])
        payload["store_counts_after_replay"] = store.counts()
    finally:
        store.close()
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "opportunity_total", "opportunity_median", "days_with_at_least_one", "opportunity_in_band",
        "fill_total", "fill_median", "fill_in_band", "frequency_accepted", "promotion",
        "order_status", "locked", "store_counts_after_replay",
    )}, indent=2))


if __name__ == "__main__":
    main()
