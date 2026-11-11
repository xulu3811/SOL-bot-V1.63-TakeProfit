#!/usr/bin/env python3
"""Bounded, offline P3 replay against preserved SQLite backups; never executes orders."""
import argparse
from collections import Counter
from contextlib import closing
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
from solbot.strategy import VERSION as STRATEGY_VERSION, prepare, provenance
from solbot.strategy.scheduler import Scheduler, StrategyStore
from solbot.mathematics import VERSION as MATH_VERSION
from solbot.mathematics.comparison import compare_path
from solbot.mathematics.contracts import ACCOUNTS, make_context
from solbot.mathematics.decimal_utils import D
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.execution_cost import estimate_cost
from solbot.mathematics.expected_return import estimate_return
from solbot.mathematics.fill_probability import estimate_fill
from solbot.mathematics.market_features import input_hash
from solbot.mathematics.position_sizing import size_position
from solbot.mathematics.trend_exhaustion import from_closed_inputs
from solbot.mathematics.volatility_regime import decimal_atr

VERSION = "p3-real-replay-1"
HOUR = 3_600_000
SOURCES = {"SOLUSDT": ("market.sqlite", "sol-micro.sqlite"),
           "SUIUSDT": ("sui-market.sqlite", "sui-micro.sqlite")}


def encoded(value):
    # P2 strategy payloads contain legacy floating indicators. Preserve their
    # exact serialized values; all newly calculated P3 prices remain Decimal.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def freeze_sources(data_dir, archive_dir):
    """Online backup includes committed WAL rows; each file is a separate snapshot."""
    data_dir, archive_dir = Path(data_dir).resolve(), Path(archive_dir).resolve()
    if data_dir == archive_dir or data_dir in archive_dir.parents:
        raise ValueError("archive must be outside the live database directory")
    manifest_path = archive_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != VERSION:
            raise ValueError("archive version differs")
        for name, item in manifest["files"].items():
            if sha_file(archive_dir / "source" / name) != item["sha256"]:
                raise ValueError("frozen source hash mismatch: " + name)
        for name, digest in manifest["code_sha256"].items():
            if sha_file(archive_dir / "code" / name) != digest or sha_file(ROOT / name) != digest:
                raise ValueError("archive/current implementation hash mismatch: " + name)
        return manifest
    if archive_dir.exists() and any(archive_dir.iterdir()):
        raise ValueError("new archive directory must be empty")
    source_dir = archive_dir / "source"
    source_dir.mkdir(parents=True)
    files, missing = {}, []
    for symbol, names in SOURCES.items():
        for index, name in enumerate(names):
            source, target = data_dir / name, source_dir / name
            if not source.exists():
                if index == 0:
                    raise FileNotFoundError(source)
                missing.append(name)
                continue
            with closing(read_db(source)) as origin, closing(sqlite3.connect(target)) as backup:
                origin.backup(backup, pages=256, sleep=0.01)
                # Only the isolated destination is changed to DELETE mode.
                backup.execute("PRAGMA journal_mode=DELETE")
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("backup quick_check failed")
            with closing(read_db(target)) as frozen:
                if index:
                    owner = frozen.execute("SELECT symbol,kind FROM micro_owner WHERE singleton=1").fetchone()
                    if tuple(owner or ()) != (symbol, "PUBLIC_MICRO_RESEARCH"):
                        raise ValueError("micro source ownership mismatch")
                    stats = frozen.execute("SELECT COUNT(*),MIN(generated_at),MAX(generated_at) FROM micro_samples").fetchone()
                else:
                    stats = frozen.execute("SELECT COUNT(*),MIN(close_time),MAX(close_time) FROM market_klines WHERE symbol=? AND is_closed=1", (symbol,)).fetchone()
            files[name] = {"symbol": symbol, "kind": "micro" if index else "market",
                           "sha256": sha_file(target), "bytes": target.stat().st_size,
                           "rows": stats[0], "first_time_ms": stats[1], "last_time_ms": stats[2],
                           "captured_at_ms": time.time_ns() // 1_000_000}
    code_hashes = {}
    paths = [Path(__file__).resolve()]
    for directory in ("strategy", "mathematics", "storage"):
        paths.extend(sorted((ROOT / "backend" / "src" / "solbot" / directory).rglob("*.py")))
    for source in paths:
        relative = source.relative_to(ROOT).as_posix()
        target = archive_dir / "code" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        code_hashes[relative] = sha_file(target)
    manifest = {"version": VERSION, "strategy_version": STRATEGY_VERSION,
                "math_version": MATH_VERSION, "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version, "files": files, "missing_optional": missing,
                "code_sha256": code_hashes,
                "capture_semantics": "Independent consistent SQLite online backups, not a cross-file atomic snapshot"}
    manifest_path.write_text(encoded(manifest) + "\n", encoding="utf-8")
    return manifest


class HistoricalMicro:
    """As-of joins use persisted feature availability, never today's latest row."""
    def __init__(self, path, symbol):
        self.symbol = symbol
        self.db = read_db(path) if Path(path).exists() else None
        if self.db:
            owner = self.db.execute("SELECT symbol,kind FROM micro_owner WHERE singleton=1").fetchone()
            if tuple(owner or ()) != (symbol, "PUBLIC_MICRO_RESEARCH"):
                self.close()
                raise ValueError("foreign historical micro source")

    def close(self):
        if self.db:
            self.db.close()

    def select(self, decision_time):
        row = self.db.execute("""SELECT generated_at,input_hash,payload FROM micro_samples
            WHERE generated_at<=? ORDER BY generated_at DESC,id DESC LIMIT 1""", (decision_time,)).fetchone() if self.db else None
        result = {"symbol": self.symbol, "decision_time_ms": decision_time,
                  "status": "NO_HISTORICAL_DEPTH", "feature_hash": None}
        if row is None:
            return None, result
        features = json.loads(row["payload"])
        if (features.get("symbol") != self.symbol or features.get("feature_generated_time_ms") != row["generated_at"]
                or features.get("input_hash") != row["input_hash"] or input_hash(features) != row["input_hash"]):
            raise ValueError("historical feature identity/hash mismatch")
        result.update(feature_hash=row["input_hash"], generated_at_ms=row["generated_at"],
                      exchange_time_ms=features.get("exchange_time_ms"), received_time_ms=features.get("received_time_ms"))
        for key in ("exchange_time_ms", "received_time_ms", "trade_event_time_ms", "trade_received_time_ms",
                    "book_ticker_exchange_time_ms", "book_ticker_received_time_ms"):
            stamp = features.get(key)
            if stamp is not None and (type(stamp) is not int or stamp < 0 or stamp > decision_time):
                result["status"] = "FUTURE_OR_INVALID_SOURCE_TIME"
                return None, result
        if any(type(features.get(key)) is not int or decision_time - features[key] > 2000
               for key in ("exchange_time_ms", "received_time_ms")) or decision_time - row["generated_at"] > 2000:
            result["status"] = "STALE_HISTORICAL_DEPTH"
            return None, result
        for key, maximum_age in (("trade_event_time_ms", 15000), ("trade_received_time_ms", 15000),
                                 ("book_ticker_received_time_ms", 5000)):
            if type(features.get(key)) is not int or decision_time - features[key] > maximum_age:
                result["status"] = "STALE_HISTORICAL_TRADE_OR_TICKER"
                return None, result
        ticker_event = features.get("book_ticker_exchange_time_ms")
        if ticker_event is not None and decision_time - ticker_event > 5000:
            result["status"] = "STALE_HISTORICAL_TRADE_OR_TICKER"
            return None, result
        if features.get("ready") is not True or features.get("synced") is not True:
            result["status"] = "HISTORICAL_DEPTH_NOT_READY"
            return None, result
        result["status"] = "AVAILABLE"
        return features, result


class HistoricalScheduler(Scheduler):
    """Inherit P2 decisions unchanged; record a separately audited historical join."""
    def __init__(self, market, store, symbol, micro, join_output):
        super().__init__(market, store, symbol)
        self.micro, self.join_output = micro, join_output
        self.coverage = Counter()

    def _event(self, as_of_ms, now_ms):
        result = super()._event(as_of_ms, now_ms)
        _, reference = self.micro.select(as_of_ms)
        self.coverage[reference["status"]] += 1
        self.join_output.write(encoded(reference) + "\n")
        return result


def payload_digest(db, table):
    order = "symbol,strategy_id,signal_time,strategy_version" if table == "decisions" else "symbol,as_of_ms,strategy_version"
    if table not in ("decisions", "snapshots"):
        raise ValueError("unsupported payload table")
    digest, count = hashlib.sha256(), 0
    for row in db.execute(f"SELECT payload_json FROM {table} ORDER BY {order}"):
        digest.update(row[0].encode() + b"\n")
        count += 1
    return {"sha256": digest.hexdigest(), "payload_count": count}


def strategy_pass(market, micro_path, symbol, start, end, run_dir):
    run_dir.mkdir()
    store = StrategyStore(run_dir / "strategy.sqlite")
    micro = HistoricalMicro(micro_path, symbol)
    try:
        with (run_dir / "historical-micro-joins.jsonl").open("w", encoding="utf-8", newline="\n") as joins:
            scheduler = HistoricalScheduler(market, store, symbol, micro, joins)
            report = scheduler.replay(start, end)
        return {"report": report, "decisions": payload_digest(store.connection, "decisions"),
                "snapshots": payload_digest(store.connection, "snapshots"), "depth_coverage": dict(scheduler.coverage),
                "joins_sha256": sha_file(run_dir / "historical-micro-joins.jsonl")}
    finally:
        micro.close()
        store.close()


def math_payload(scheduler, features, now, strategy):
    """Fixed research probe, not a signal or calibrated trade recommendation."""
    cached = scheduler._cache.get("1m", [])
    if cached and now - cached[-1]["close_time"] > 120 * 60000:
        scheduler._cache.clear()
    with scheduler.reader() as conn:
        candles = scheduler._inputs(conn, now)
    selected, errors = prepare(scheduler.symbol, candles, now)
    inputs = {"closed_candles": provenance(selected), "research_probe": {
        "notional": "500", "score": 85, "equity": "2000", "cash": "2000", "win_probability": None}}
    context = make_context(symbol=scheduler.symbol, account_id=ACCOUNTS[scheduler.symbol], strategy_id=strategy,
        strategy_version=STRATEGY_VERSION, signal_id=f"probe-{strategy}-{now}", decision_time_ms=now,
        features=features, inputs=inputs, parameter_version=VERSION + "-" + scheduler.symbol)
    with localcontext() as decimal_context:
        decimal_context.prec = 40
        ask = D(features["ask"], positive=True)
        quantity = Decimal(500) / ask
        notional = quantity * ask
        atr = decimal_atr(selected.get("5m", [])) if not errors else Decimal(0)
        cost = estimate_cost(context, entry_price=ask, exit_price=ask * Decimal("1.02"), quantity=quantity)
        fill = estimate_fill(context, features, limit_price=ask, quantity=quantity, wait_ms=5000)
        expected = estimate_return(context, notional=notional, profit_fraction=".02", loss_fraction=".008", cost=cost, execution=fill)
        entry = price_entry(context, features, signal_max_price=ask, notional=notional, atr=atr,
                            expected=expected, cost=cost)
        size = size_position(context, features, score=85, entry_price=ask, equity="2000", high_watermark="2000", cash_balance="2000")
    return {"kind": "FIXED_PARAMETER_RESEARCH_PROBE_NOT_STRATEGY_ENTRY", "features": features, "inputs": inputs,
            "candle_errors": errors, "outputs": {"cost": cost.asdict(), "fill": fill.asdict(),
                "expected_return": expected.asdict(), "entry": entry.asdict(), "sizing": size.asdict()}}


def math_pass(market, micro_path, symbol, run_dir, hours=6):
    """At most one persisted sample per UTC minute for at most six hours."""
    micro = HistoricalMicro(micro_path, symbol)
    store = StrategyStore(run_dir / "math-reader.sqlite")
    scheduler = Scheduler(market, store, symbol)
    counts, statuses = Counter(), Counter()
    first = last = None
    output = run_dir / "math-payloads.jsonl"
    try:
        end = micro.db.execute("SELECT MAX(generated_at) FROM micro_samples").fetchone()[0] if micro.db else None
        with output.open("w", encoding="utf-8", newline="\n") as destination:
            stamps = micro.db.execute("""SELECT MIN(generated_at) FROM micro_samples WHERE generated_at>? AND generated_at<=?
                GROUP BY generated_at/60000 ORDER BY MIN(generated_at) LIMIT 360""", (end - hours * HOUR, end)) if end else []
            for row in stamps:
                now = row[0]
                first = now if first is None else first
                last = now
                features, join = micro.select(now)
                counts[join["status"]] += 1
                if features is None:
                    destination.write(encoded({"join": join}) + "\n")
                    continue
                for strategy in ("T1", "R1"):
                    payload = math_payload(scheduler, features, now, strategy)
                    for name, value in payload["outputs"].items():
                        statuses[f"{strategy}:{name}:{value['status']}"] += 1
                    destination.write(encoded(payload) + "\n")
        return {"sha256": sha_file(output), "coverage": dict(counts), "output_statuses": dict(statuses),
                "first_time_ms": first, "last_time_ms": last, "max_samples": 360,
                "sampling": "First persisted snapshot in each UTC minute; decision time equals feature generation time"}
    finally:
        store.close()
        micro.close()


def close_comparison(market, run_dir, start, end, every_hours=6, horizon_minutes=180):
    store = StrategyStore(run_dir / "comparison-reader.sqlite")
    scheduler = Scheduler(market, store, "SOLUSDT")
    scenarios, skipped = [], Counter()
    try:
        with scheduler.reader() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT * FROM market_klines
                WHERE symbol='SOLUSDT' AND interval='1m' AND is_closed=1 AND close_time>=? AND close_time<=?
                ORDER BY open_time LIMIT 4321""", (start, end))]
            for index, entry in enumerate(rows):
                if (entry["close_time"] + 1) % (every_hours * HOUR):
                    continue
                window = rows[index + 1:index + horizon_minutes + 1]
                if len(window) != horizon_minutes or any(row["close_time"] != entry["close_time"] + (i + 1) * 60000 for i, row in enumerate(window)):
                    skipped["INCOMPLETE_FORWARD_CLOSE_WINDOW"] += 1
                    continue
                # Scheduler's incremental cache is designed for contiguous 1m
                # events. Six-hour research entries can jump past its 120-bar
                # append limit, so start each independent window from SQLite.
                scheduler._cache.clear()
                observations, source_refs = [], []
                for row in window:
                    stamp = row["close_time"]
                    candles = scheduler._inputs(conn, stamp)
                    selected, errors = prepare("SOLUSDT", candles, stamp)
                    if errors:
                        skipped["INSUFFICIENT_CLOSED_CANDLE_WARMUP_OR_GAP"] += 1
                        break
                    five = selected["5m"][-1]
                    denominator = D(five["quote_volume"], nonnegative=True)
                    ratio = D(five["taker_buy_quote_volume"], nonnegative=True) / denominator if denominator else None
                    proxy = {"symbol": "SOLUSDT", "ready": ratio is not None, "buy_ratio": str(ratio) if ratio is not None else None}
                    evidence = from_closed_inputs("SOLUSDT", selected, proxy, stamp)
                    observations.append((stamp, row["close"], evidence))
                    source_refs.append({"time_ms": stamp, "candles": provenance(selected), "five_minute_flow_proxy": proxy})
                else:
                    name = "sol-real-" + str(entry["close_time"])
                    parameters = {"entry_price": entry["close"], "notional": "500", "fee_rate": ".001", "slippage_rate": ".001", "tick_size": ".01"}
                    left = compare_path(name, observations, **parameters)
                    right = compare_path(name, observations, **parameters)
                    if encoded(left) != encoded(right):
                        raise AssertionError("close comparison is nondeterministic")
                    scenarios.append({"entry_time_ms": entry["close_time"], "entry_candle": entry,
                        "source_references": source_refs, "comparison": left,
                        "complete_payload_sha256": hashlib.sha256(encoded(left).encode()).hexdigest()})
        return {"scenarios": scenarios, "count": len(scenarios), "skipped": dict(skipped), "repeat_equal": True,
            "conditions": {"entry_every_hours": every_hours, "horizon_minutes": horizon_minutes,
                "fee_per_side": ".001", "modeled_exit_slippage": ".001", "modeled_entry_slippage": "0", "notional": "500", "tick_size_research_assumption": ".01"},
            "limitations": ["Close-only hypothetical research entries, not T1/R1 realized strategy returns",
                "No historical depth is invented; 5m taker quote ratio is a candle flow proxy only",
                "compare_path assumes immediate exit at observed close with exit slippage; no historical order fills",
                "Future observations are used only for ex-post early-exit metrics, never protection decisions",
                "Fee and slippage are research assumptions, not account fee evidence; no superiority claim"]}
    finally:
        store.close()


def run(data_dir, output, archive_dir, *, hours=72, micro_hours=6, entry_every_hours=6, horizon_minutes=180):
    if not 1 <= hours <= 72 or not 1 <= micro_hours <= 6 or not 6 <= entry_every_hours <= 24 or not 1 <= horizon_minutes <= 180:
        raise ValueError("bounded replay requires hours<=72, micro-hours<=6, entry spacing>=6h, horizon<=180m")
    output, archive = Path(output).resolve(), Path(archive_dir).resolve()
    live = Path(data_dir).resolve()
    if output == live or live in output.parents or output == archive / "manifest.json" or archive / "source" in output.parents or archive / "code" in output.parents:
        raise ValueError("output must not overwrite source databases or frozen manifest/code")
    for name in [name for pair in SOURCES.values() for name in pair]:
        for protected in (live / name, archive / "source" / name):
            if output.exists() and protected.exists() and output.samefile(protected):
                raise ValueError("output aliases a source database")
    manifest = freeze_sources(live, archive)
    run_dir = Path(tempfile.mkdtemp(prefix="replay-", dir=archive))
    results, bounds = {}, {}
    for symbol, (market_name, micro_name) in SOURCES.items():
        market, micro = archive / "source" / market_name, archive / "source" / micro_name
        with closing(read_db(market)) as db:
            end = db.execute("SELECT MAX(close_time) FROM market_klines WHERE symbol=? AND interval='1m' AND is_closed=1", (symbol,)).fetchone()[0]
        if end is None:
            raise ValueError("no closed 1m history for " + symbol)
        start = max(0, end - hours * HOUR + 1)
        bounds[symbol] = {"start_ms": start, "end_ms": end}
        passes, maths = [], []
        for number in (1, 2):
            directory = run_dir / f"{symbol}-{number}"
            passes.append(strategy_pass(market, micro, symbol, start, end, directory))
            maths.append(math_pass(market, micro, symbol, directory, micro_hours))
        equal = passes[0] == passes[1] and maths[0] == maths[1]
        results[symbol] = {"deterministic": equal, "strategy_passes": passes, "math_passes": maths,
            "missing_historical_depth_decisions": passes[0]["depth_coverage"].get("NO_HISTORICAL_DEPTH", 0),
            "unavailable_historical_depth_decisions": sum(count for state, count in passes[0]["depth_coverage"].items() if state != "AVAILABLE")}
    comparison = close_comparison(archive / "source" / SOURCES["SOLUSDT"][0], run_dir,
        **{key: bounds["SOLUSDT"][key + "_ms"] for key in ("start", "end")},
        every_hours=entry_every_hours, horizon_minutes=horizon_minutes)
    result = {"version": VERSION, "status": "PASS" if all(row["deterministic"] for row in results.values()) else "FAIL",
        "status_scope": "Deterministic bounded offline replay only; not trading approval or empirical profitability validation",
        "manifest_sha256": sha_file(archive / "manifest.json"), "source_manifest": manifest,
        "archive_dir": str(archive), "run_dir": str(run_dir), "bounds": bounds, "symbols": results,
        "real_close_path_comparison": comparison, "live_authorized": False,
        "limitations": ["Historical candles are the current corrected closed history, not original ingestion-time snapshots",
            "P2 Scheduler T1/R1 decisions are unchanged; micro availability is an independent audit join, not new strategy permission",
            "Only micro_samples generated at or before the decision are eligible; all source and receipt timestamps must also be nonfuture",
            "Missing, stale or unready historical depth is counted explicitly; no backfilled historical order book",
            "Math probes use fixed hypothetical score/capital and unknown win probability; they are not actual accepted entries",
            "No keys, private APIs, orders or execution ledger writes; SUI R1 research remains permanently unauthorized for live",
            "Research interval is at most 72h, micro probes at most 360 per symbol; no long-term SLA or APTS superiority claim"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded(result) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--micro-hours", type=int, default=6)
    parser.add_argument("--entry-every-hours", type=int, default=6)
    parser.add_argument("--horizon-minutes", type=int, default=180)
    args = parser.parse_args()
    result = run(args.data_dir, args.output, args.archive_dir, hours=args.hours, micro_hours=args.micro_hours,
                 entry_every_hours=args.entry_every_hours, horizon_minutes=args.horizon_minutes)
    print(encoded({"status": result["status"], "output": str(args.output), "archive_dir": str(args.archive_dir),
                   "comparison_windows": result["real_close_path_comparison"]["count"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
