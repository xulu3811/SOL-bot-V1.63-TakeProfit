"""Independent R2 paper worker, sharing public data and no exchange credentials."""
import argparse
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import time

from solbot.execution.r2_shadow import R2ShadowExecutor
from solbot.execution.shadow import ShadowRejected
from solbot.mathematics.contracts import SymbolRules
from solbot.mathematics.decimal_utils import json_safe
from solbot.mathematics.trend_exhaustion import FlowSmoother, TrendEvidence, from_closed_inputs, smooth_micro_features
from solbot.p2_runtime import atomic_json
from solbot.p3_runtime import (
    acquire_cross_shadow_entry_guard,
    attach_reference_price,
    load_shared_trading_config,
    refresh_features,
    release_cross_shadow_entry_guard,
)
from solbot.storage.microstructure import MicrostructureStore
from solbot.storage.r2_store import R2Store
from solbot.storage.read_snapshot import publish
from solbot.strategy import prepare, indicators, market_regime


def load_inputs(db, now):
    frames = {}
    for interval in ('1m', '5m', '15m', '1h', '4h'):
        frames[interval] = list(reversed([dict(row) for row in db.execute(
            'SELECT * FROM market_klines WHERE interval=? AND is_closed=1 AND close_time<=? ORDER BY close_time DESC LIMIT 120',
            (interval, now))]))
    frames, errors = prepare('SOLUSDT', frames, now)
    if errors:
        raise ValueError(','.join(errors))
    metrics = {key: indicators.series(rows) for key, rows in frames.items()}
    return frames, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('market-db', 'micro-db', 'state-db', 'shadow-db', 'status-path'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    paths = [Path(value).resolve() for value in vars(args).values()]
    if len(set(paths)) != len(paths):
        raise ValueError('R2 paths must be distinct')
    for path in (Path(args.state_db), Path(args.shadow_db)):
        marker = path.with_suffix('.r2-owner')
        if path.exists() and not marker.exists():
            raise ValueError('R2 requires an isolated owned store')
        if marker.exists():
            if marker.read_text() != 'R2-SHADOW-1':
                raise ValueError('R2 owner mismatch')
        else:
            with marker.open('x') as handle:
                handle.write('R2-SHADOW-1')
    store = R2Store(args.state_db)
    micro = MicrostructureStore(args.micro_db, 'SOLUSDT', read_only=True)
    ledger = None
    candles = None
    last_error = None
    last_snapshot_ms = 0
    flow_smoother = FlowSmoother()
    while True:
        now = time.time_ns() // 1000000
        status = {'symbol': 'SOLUSDT', 'mode': 'SHADOW', 'strategy_id': 'R2', 'updated_at': now,
                  'status': 'unavailable', 'live_authorized': False}
        try:
            features = micro.latest()
            if features is None:
                raise ValueError('public market snapshot unavailable')
            features = refresh_features(features, now)
            if ledger is None:
                ledger = R2ShadowExecutor(args.shadow_db, rules=SymbolRules.from_exchange_info(features['rules'], 'SOLUSDT'), fee_rate='0.00075')
            shared_cfg = load_shared_trading_config(args.market_db, args.shadow_db)
            if shared_cfg:
                ledger.apply_trading_config(shared_cfg['min_order_usdt'], shared_cfg['max_order_usdt'], shared_cfg['max_slots'])
            evidence = TrendEvidence()
            with closing(sqlite3.connect(Path(args.market_db).resolve().as_uri()+'?mode=ro', uri=True)) as db:
                latest = db.execute("SELECT close FROM market_klines WHERE interval='1m' AND is_closed=1 AND close_time<? ORDER BY close_time DESC LIMIT 1", (now,)).fetchone()
                if latest:
                    ledger.invalidate_pending(latest[0], now)
            if candles:
                event = candles['1m'][-1]['close_time']
                smoothed_features = smooth_micro_features(features, flow_smoother)
                evidence = from_closed_inputs('SOLUSDT', candles, smoothed_features, event)
                ledger.invalidate_pending(candles['1m'][-1]['close'], now)
            ledger.chase_pending(features, now)
            status['execution'] = ledger.process_market(features, evidence, now_ms=now)
            outbox_changed = bool(status['execution'].get('fills'))
            with closing(sqlite3.connect(Path(args.market_db).resolve().as_uri()+'?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                row = store.connection.execute('SELECT as_of_ms FROM r2_state WHERE singleton=1').fetchone()
                checkpoint = row[0] if row else now-120000
                events = [r[0] for r in db.execute("SELECT close_time FROM market_klines WHERE interval='1m' AND is_closed=1 AND close_time>? AND close_time<? ORDER BY close_time LIMIT 300", (checkpoint, now))]
                for event in events:
                    candles, metrics = load_inputs(db, event)
                    regime, _ = market_regime.classify(candles, metrics)
                    state, new_intent = store.step(candles, metrics, regime, event)
                    status['candidate'] = {k: (str(v) if isinstance(v, float) else v) for k, v in state.items()} if isinstance(state, dict) else state
                    if new_intent is not None:
                        outbox_changed = True
            for row in store.connection.execute("SELECT payload FROM r2_outbox WHERE status='PENDING'").fetchall():
                intent = json.loads(row[0])
                # Ledger retries are idempotent. Freshness gates apply before
                # the initial submission and existing protection runs above.
                if not features.get('ready'):
                    continue
                claimed = False
                try:
                    attach_reference_price(intent, ledger.rules, features, args.market_db, now)
                    claimed = acquire_cross_shadow_entry_guard(
                        args.shadow_db,
                        args.market_db,
                        strategy_id='R2',
                        signal_id=intent['signal_id'],
                        signal_time=int(intent.get('signal_time') or now),
                        now_ms=now,
                    )
                    order = ledger.submit_intent(
                        intent,
                        now,
                        bid=features.get('bid'),
                        ask=features.get('ask'),
                    )
                    outcome = {'order_id': order['order_id'], 'status': order['status']}
                except ShadowRejected as exc:
                    if claimed:
                        release_cross_shadow_entry_guard(args.market_db, intent['signal_id'], own_shadow_db=args.shadow_db)
                    outcome = {'rejected': str(exc), 'code': exc.code}
                store.acknowledge(intent['signal_id'], outcome)
                outbox_changed = True
            if outbox_changed or now - last_snapshot_ms >= 5000:
                try:
                    publish(Path(args.shadow_db), Path(args.shadow_db).with_name('r2-shadow-read.sqlite'))
                    publish(Path(args.state_db), Path(args.state_db).with_name('r2-state-read.sqlite'))
                    last_snapshot_ms = now
                except Exception:
                    pass
            status.update(status='ready' if features.get('ready') else 'protection_only', shadow=ledger.snapshot())
            current = store.connection.execute('SELECT payload FROM r2_state WHERE singleton=1').fetchone()
            if current:
                cand = json.loads(current[0])
                status['candidate'] = {k: (str(v) if isinstance(v, float) else v) for k, v in cand.items()} if isinstance(cand, dict) else cand
            last_error = None
        except Exception as exc:
            status['error'] = f'{type(exc).__name__}: {exc}'[:240]
            if status['error'] != last_error:
                logging.exception('R2 worker failure')
            last_error = status['error']
        atomic_json(Path(args.status_path), json_safe(status))
        time.sleep(1)


if __name__ == '__main__':
    main()
