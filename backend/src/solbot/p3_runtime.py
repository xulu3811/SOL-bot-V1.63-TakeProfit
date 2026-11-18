"""P2 closed-candle scheduling plus P3 math proposals and protected simulation."""
import argparse,json,sqlite3,time,urllib.request,os,logging
from contextlib import closing
from pathlib import Path
from decimal import Decimal
from solbot.p2_runtime import atomic_json
from solbot.strategy.scheduler import StrategyStore,Scheduler
from solbot.storage.microstructure import MicrostructureStore
from solbot.storage.math_audit import MathAudit,normalize_signal
from solbot.mathematics.contracts import ACCOUNTS,SymbolRules,make_context,feature_reasons
from solbot.mathematics.decimal_utils import D,floor_step,json_safe
from solbot.mathematics.trend_exhaustion import FlowSmoother, TrendEvidence, from_closed_inputs, smooth_micro_features
from solbot.mathematics.execution_cost import FeeSchedule, estimate_cost
from solbot.mathematics.expected_return import estimate_return
from solbot.mathematics.fill_probability import estimate_fill
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.position_sizing import ResearchRiskParameters, size_position
from solbot.execution.shadow import ShadowRejected
from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.execution.live_policy import (
    DEFAULT_FEE_RATE,
    LIVE_MAX_ORDER_USDT,
    LIVE_MIN_ORDER_USDT,
    compute_entry_limit_price,
    compute_live_order_size,
    is_custom_trading_config,
    parse_allowed_strategies,
    resolve_stop_distance,
    strategy_entry_allowed,
)
from solbot.storage.read_snapshot import publish

SHADOW_RISK_PARAMETERS=ResearchRiskParameters(symbol='SOLUSDT',version='p3-shadow-500-1000-v1',t1_budget=Decimal('0.012'),r1_budget=Decimal('0.008'),min_notional=Decimal('500'),max_notional=Decimal('1000'),allow_cash_fallback=True)
CROSS_STRATEGY_COOLDOWN_MS=900_000
MAX_COMBINED_SHADOW_SLOTS=1


def load_shared_trading_config(market_db_path=None, shadow_db_path=None):
    candidates = []
    env_path = os.environ.get('SOLBOT_TRADING_CONFIG_PATH', '').strip()
    if env_path:
        candidates.append(Path(env_path))
    for base in (shadow_db_path, market_db_path):
        if base:
            try:
                candidates.append(Path(base).resolve().parent / 'trading-config.json')
            except Exception:
                pass
    for path in candidates:
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding='utf-8'))
                min_u = D(raw.get('min_order_usdt') or '500', positive=True)
                max_u = D(raw.get('max_order_usdt') or str(min_u), positive=True)
                if max_u < min_u:
                    max_u = min_u
                slots = max(1, min(10, int(raw.get('max_slots') or 2)))
                return {'min_order_usdt': min_u, 'max_order_usdt': max_u, 'max_slots': slots, 'custom': True}
        except Exception:
            continue
    return None


def _inspect_shadow_db_activity(db_path, now_ms):
    path = Path(db_path).resolve() if db_path else None
    if path is None or not path.exists():
        return 0, []
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            acc = db.execute("SELECT bid FROM shadow_account WHERE singleton=1").fetchone()
            bid = D(acc['bid'], positive=True) if acc and acc['bid'] and D(acc['bid']) > 0 else None
            pending_rows = db.execute(
                "SELECT order_id, created_at FROM shadow_orders WHERE side='BUY' AND status='PENDING' AND expires_at>?",
                (now_ms,),
            ).fetchall()
            pending_pids = {'p-' + r['order_id'] for r in pending_rows}
            slots = set(pending_pids)
            active_buy_times = [int(r['created_at'] or 0) for r in pending_rows if r['created_at']]
            for pos in db.execute(
                "SELECT position_id, signal_id, quantity, entry_price, entry_cost, opened_at FROM shadow_positions WHERE status='OPEN'"
            ).fetchall():
                pid = pos['position_id']
                if pid in pending_pids:
                    slots.add(pid)
                    continue
                ref = bid if bid is not None else D(pos['entry_price'])
                val = D(pos['quantity']) * ref if ref > 0 else D(pos['entry_cost'])
                if val >= Decimal('5'):
                    slots.add(pid)
                    if pos['opened_at']:
                        active_buy_times.append(int(pos['opened_at']))
            recent_buys = db.execute(
                "SELECT signal_id, created_at, quantity, limit_price, status FROM shadow_orders WHERE side='BUY' AND status IN ('PENDING','FILLED') ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            for r in recent_buys:
                ts = int(r['created_at'] or 0)
                if ts > 0 and D(r['quantity']) * D(r['limit_price']) >= Decimal('5'):
                    active_buy_times.append(ts)
            return len(slots), active_buy_times
    except Exception:
        return 0, []


def _is_claim_expired_unfilled(claim_signal_id, *db_paths):
    for raw_path in db_paths:
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                row = db.execute(
                    "SELECT status, COALESCE(executed_quantity, '0') FROM shadow_orders WHERE side='BUY' AND signal_id=? ORDER BY created_at DESC LIMIT 1",
                    (str(claim_signal_id),),
                ).fetchone()
                if row and row[0] in ('EXPIRED', 'CANCELLED') and D(row[1] or '0') <= 0:
                    return True
        except Exception:
            continue
    return False


def _resolve_peer_shadow_db(own_shadow_db, market_db):
    own = Path(own_shadow_db).resolve()
    db_dir = Path(market_db).resolve().parent if market_db else own.parent
    root_dir = db_dir.parent
    is_r2 = 'r2' in own.name.lower() or own.parent.name.lower() == 'r2'
    if is_r2:
        candidates = (db_dir / 'sol-shadow.sqlite', db_dir / 'sol-shadow-read.sqlite')
    else:
        candidates = (
            root_dir / 'r2' / 'r2-v2-shadow.sqlite',
            root_dir / 'r2' / 'r2-shadow-read.sqlite',
            db_dir / 'r2-v2-shadow.sqlite',
            db_dir / 'r2-shadow-read.sqlite',
        )
    for cand in candidates:
        try:
            resolved = cand.resolve()
            if resolved != own and resolved.exists():
                return resolved
        except Exception:
            continue
    return None


def _has_higher_priority_strategy_signal(strategy_db_path, signal_time, cooldown_ms):
    if not strategy_db_path:
        return False
    path = Path(strategy_db_path).resolve()
    if not path.exists():
        return False
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT signal_time, payload_json FROM decisions WHERE symbol='SOLUSDT' AND accepted=1 AND strategy_id IN ('T1','T2') ORDER BY id DESC LIMIT 10"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
            except Exception:
                payload = {}
            sid = payload.get('strategy_id')
            reg = str(payload.get('regime') or '').upper()
            if sid == 'T1' and reg == 'DOWNTREND':
                continue
            # In DOWNTREND/UNCERTAIN, R2 outranks T2 so T2 does not block R2.
            if sid == 'T2' and reg in ('DOWNTREND', 'UNCERTAIN'):
                continue
            st = int(row['signal_time'] or payload.get('signal_time') or 0)
            if st > 0 and signal_time > 0 and abs(st - int(signal_time)) < cooldown_ms:
                return True
    except Exception:
        return False
    return False


def _has_higher_priority_r2_signal(own_shadow_db, market_db, signal_time, cooldown_ms):
    own = Path(own_shadow_db).resolve()
    db_dir = Path(market_db).resolve().parent if market_db else own.parent
    root_dir = db_dir.parent
    for cand in (
        root_dir / 'r2' / 'r2-v2-state.sqlite',
        root_dir / 'r2' / 'r2-state-read.sqlite',
        root_dir / 'r2' / 'r2-state.sqlite',
        db_dir / 'r2-v2-state.sqlite',
        db_dir / 'r2-state-read.sqlite',
        db_dir / 'r2-state.sqlite',
    ):
        try:
            path = cand.resolve()
        except Exception:
            continue
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT payload, status FROM r2_outbox WHERE status='PENDING' ORDER BY rowid DESC LIMIT 10"
                ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row['payload'])
                except Exception:
                    continue
                st = int(payload.get('signal_time') or 0)
                if st > 0 and signal_time > 0 and abs(st - int(signal_time)) < cooldown_ms:
                    return True
        except Exception:
            continue
    return False


def acquire_cross_shadow_entry_guard(
    own_shadow_db,
    market_db,
    *,
    strategy_id,
    signal_id,
    signal_time,
    now_ms,
    peer_shadow_db=None,
    strategy_db=None,
    guard_db=None,
    cooldown_ms=CROSS_STRATEGY_COOLDOWN_MS,
    max_combined_slots=None,
    regime=None,
):
    """Enforce live-aligned cross-strategy cooldown, priority, and single-slot sync across P3 and R2 shadow processes."""
    if max_combined_slots is None:
        shared_cfg = load_shared_trading_config(market_db, own_shadow_db)
        max_combined_slots = int(shared_cfg['max_slots']) if shared_cfg else MAX_COMBINED_SHADOW_SLOTS
    own_path = Path(own_shadow_db).resolve()
    if own_path.exists():
        try:
            with closing(sqlite3.connect(own_path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
                if db.execute("SELECT 1 FROM shadow_orders WHERE request_key=?", ('BUY:' + str(signal_id),)).fetchone():
                    return False
        except Exception:
            pass
    db_dir = Path(market_db).resolve().parent if market_db else own_path.parent
    guard_path = Path(guard_db).resolve() if guard_db else (db_dir / 'shadow-entry-guard.sqlite')
    strat_path = Path(strategy_db).resolve() if strategy_db else (db_dir / 'sol-shadow-strategy.sqlite')
    peer_path = Path(peer_shadow_db).resolve() if peer_shadow_db else _resolve_peer_shadow_db(own_path, market_db)

    guard_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(guard_path, timeout=5, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS shadow_entry_claims (
                    signal_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    signal_time INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    status TEXT NOT NULL
                )"""
            )
            conn.execute(
                "DELETE FROM shadow_entry_claims WHERE status!='CLAIMED' OR claimed_at<=?",
                (int(now_ms) - int(cooldown_ms),),
            )
            if strategy_id == 'R2' and _has_higher_priority_strategy_signal(strat_path, int(signal_time or now_ms), cooldown_ms):
                raise ShadowRejected('CROSS_STRATEGY_COOLDOWN', 'higher-priority T1/T2 signal active within cooldown window')
            if (
                strategy_id == 'T2'
                and str(regime or '').upper() in ('DOWNTREND', 'UNCERTAIN')
                and _has_higher_priority_r2_signal(own_shadow_db, market_db, int(signal_time or now_ms), cooldown_ms)
            ):
                raise ShadowRejected('CROSS_STRATEGY_COOLDOWN', 'higher-priority R2 signal active within cooldown window')

            claims = conn.execute(
                "SELECT signal_id, strategy_id, signal_time, claimed_at FROM shadow_entry_claims WHERE status='CLAIMED' AND signal_id!=?",
                (str(signal_id),),
            ).fetchall()
            for claim in claims:
                if _is_claim_expired_unfilled(claim['signal_id'], own_path, peer_path):
                    conn.execute("DELETE FROM shadow_entry_claims WHERE signal_id=?", (str(claim['signal_id']),))
                    continue
                c_at = int(claim['claimed_at'] or 0)
                c_sig = int(claim['signal_time'] or 0)
                if (c_at > 0 and 0 <= int(now_ms) - c_at < cooldown_ms) or (
                    c_sig > 0 and int(signal_time or 0) > 0 and abs(int(signal_time) - c_sig) < cooldown_ms
                ):
                    raise ShadowRejected('CROSS_STRATEGY_COOLDOWN', f"cross-strategy shadow entry cooldown active ({claim['strategy_id']})")

            own_slots, own_times = _inspect_shadow_db_activity(own_path, now_ms)
            peer_slots, peer_times = _inspect_shadow_db_activity(peer_path, now_ms) if peer_path else (0, [])
            if own_slots + peer_slots >= max_combined_slots:
                raise ShadowRejected('CROSS_SHADOW_SLOTS_FULL', 'combined shadow position slot is already occupied')
            for ts in own_times + peer_times:
                if ts > 0 and (
                    0 <= int(now_ms) - ts < cooldown_ms
                    or (int(signal_time or 0) > 0 and abs(int(signal_time) - ts) < cooldown_ms)
                ):
                    raise ShadowRejected('CROSS_STRATEGY_COOLDOWN', 'recent shadow buy order within 15m cooldown')

            conn.execute(
                "INSERT OR REPLACE INTO shadow_entry_claims(signal_id, strategy_id, signal_time, claimed_at, status) VALUES (?,?,?,?,'CLAIMED')",
                (str(signal_id), str(strategy_id), int(signal_time or now_ms), int(now_ms)),
            )
            conn.commit()
            return True
        except BaseException:
            conn.rollback()
            raise


def release_cross_shadow_entry_guard(market_db, signal_id, *, guard_db=None, own_shadow_db=None):
    base_dir = Path(market_db).resolve().parent if market_db else (Path(own_shadow_db).resolve().parent if own_shadow_db else None)
    guard_path = Path(guard_db).resolve() if guard_db else (base_dir / 'shadow-entry-guard.sqlite' if base_dir else None)
    if guard_path is None or not guard_path.exists():
        return
    try:
        with closing(sqlite3.connect(guard_path, timeout=5, isolation_level=None)) as conn:
            conn.execute('PRAGMA busy_timeout=5000')
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM shadow_entry_claims WHERE signal_id=?', (str(signal_id),))
            conn.commit()
    except Exception:
        pass

def scheduler_status(report):
    """Normalize only legacy diagnostic floats, never monetary/model inputs."""
    return json.loads(json.dumps(report,allow_nan=False),parse_float=str)

def refresh_features(features,now):
    """Consumer-time freshness; leave synchronized depth available to protection."""
    value=dict(features,source_snapshot=dict(features));reasons=list(value.get('reasons',[]))
    for key,age in (('exchange_time_ms',2000),('received_time_ms',2000),
                    ('trade_received_time_ms',15000),('trade_event_time_ms',15000),
                    ('book_ticker_received_time_ms',5000)):
        timestamp=value.get(key)
        if not isinstance(timestamp,int) or not 0<=now-timestamp<=age:reasons.append('CONSUMER_STALE_'+key.upper())
    ticker_event=value.get('book_ticker_exchange_time_ms')
    if ticker_event is not None and not 0<=now-ticker_event<=5000:reasons.append('CONSUMER_STALE_TICKER_EVENT')
    value['ready']=bool(value.get('ready') and value.get('synced') and not reasons)
    value['reasons']=list(dict.fromkeys(reasons))
    return value


def attach_reference_price(candidate,rules,features,market_db_path=None,now_ms=None):
    """Populate p3_reference_price/minutes for PERCENT_PRICE_BY_SIDE validation."""
    if not rules or not rules.percent_filters:
        return candidate
    if candidate.get('p3_reference_price') is not None and candidate.get('p3_reference_minutes') is not None:
        return candidate
    minutes=rules.percent_filters[0][5]
    ref_price=None
    if minutes>0 and market_db_path is not None and now_ms is not None:
        try:
            with closing(sqlite3.connect(Path(market_db_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                rows=db.execute("SELECT close FROM market_klines WHERE interval='1m' AND is_closed=1 AND close_time<=? ORDER BY close_time DESC LIMIT ?",(now_ms,minutes)).fetchall()
            if len(rows)==minutes:
                ref_price=sum(D(r[0],positive=True) for r in rows)/Decimal(minutes)
        except Exception:
            ref_price=None
    if ref_price is None and features:
        mid=features.get('midpoint') or features.get('mid')
        if mid is not None:
            ref_price=D(mid,positive=True)
        elif features.get('bid') is not None and features.get('ask') is not None:
            ref_price=(D(features['bid'],positive=True)+D(features['ask'],positive=True))/Decimal(2)
    if ref_price is not None:
        candidate['p3_reference_price']=str(ref_price)
        candidate['p3_reference_minutes']=minutes
    return candidate

def plan_signal(payload,features,account,now,market_db_path=None):
    signal,original=normalize_signal(payload)
    context=make_context(symbol=signal['symbol'],account_id=ACCOUNTS[signal['symbol']],strategy_id=signal['strategy_id'],
        strategy_version=signal['strategy_version'],signal_id=signal['signal_id'],decision_time_ms=now,
        features=features,inputs={'signal':signal,'account':account},parameter_version='p3-runtime-1')
    reasons=list(feature_reasons(context,features))
    if not signal['accepted']:reasons.append('UPSTREAM_SIGNAL_REJECTED')
    if not signal['signal_time']<=now<signal['expires_at']:reasons.append('SIGNAL_EXPIRED_OR_FUTURE')
    result={'live_authorized':False,'gate_reasons':reasons,'account_evidence':'virtual_ledger' if signal['symbol']=='SOLUSDT' else 'planning_only_not_private_balance'}
    if feature_reasons(context,features):return signal,original,result
    if not signal.get('indicators',{}).get('5m',{}).get('atr14'):
        reasons.append('INDICATORS_UNAVAILABLE');return signal,original,result
    rules=SymbolRules.from_exchange_info(features['rules'],signal['symbol'])
    ref_doc=attach_reference_price({},rules,features,market_db_path,now) if rules.percent_filters else {}
    ref_kwargs=dict(reference_price=ref_doc.get('p3_reference_price'),reference_minutes=ref_doc.get('p3_reference_minutes')) if ref_doc else {}
    price=D(features['ask'])
    shared_cfg=load_shared_trading_config(market_db_path) if signal['symbol']=='SOLUSDT' else None
    base_notional=shared_cfg['min_order_usdt'] if shared_cfg else Decimal(500)
    quantity=floor_step(base_notional/price,rules.step_size)
    amount=quantity*price
    stop_dist=signal.get('stop_distance') or '0.008'
    bnb_fees=FeeSchedule(bnb_discount_enabled=True)
    cost=estimate_cost(context,entry_price=price,exit_price=price*Decimal('1.015'),quantity=quantity,fees=bnb_fees)
    execution=estimate_fill(context,features,limit_price=price,quantity=quantity,wait_ms=min(60000,max(1,signal['expires_at']-now)))
    expected=estimate_return(context,notional=amount,profit_fraction='.015',loss_fraction=stop_dist,cost=cost,execution=execution)
    if shared_cfg:
        risk_params=ResearchRiskParameters(
            symbol='SOLUSDT',version='p3-shadow-500-1000-v1',
            t1_budget=Decimal('0.012'),r1_budget=Decimal('0.008'),
            min_notional=shared_cfg['min_order_usdt'],max_notional=shared_cfg['max_order_usdt'],
            allow_cash_fallback=True,max_open_positions=shared_cfg['max_slots'],
        )
    else:
        risk_params=SHADOW_RISK_PARAMETERS if signal['symbol']=='SOLUSDT' else None
    sizing_args=dict(entry_fee_rate='0.00075',exit_fee_rate='0.00075',stop_distance=stop_dist,parameters=risk_params,**ref_kwargs)
    bid_ref=D(features['bid'],positive=True) if features.get('bid') else None
    non_dust_positions=sum(1 for pos in account['positions'] if D(pos['quantity'])*(bid_ref or D(pos['entry_price']))>=Decimal('5'))
    eff_equity=D(account['last_equity'])
    eff_peak=D(account['peak_equity'])
    if shared_cfg and not bool(account['hard_locked']):
        min_eq=shared_cfg['max_order_usdt']*Decimal('2.5')
        if eff_equity<min_eq:
            delta_eq=min_eq-eff_equity
            eff_equity=eff_equity+delta_eq
            eff_peak=eff_peak+delta_eq
    sizing=size_position(context,features,score=signal['score'],entry_price=price,equity=str(eff_equity),high_watermark=str(eff_peak),
        cash_balance=account['cash'],reserved_entry_notional=account['reserved'],open_positions=non_dust_positions,
        pending_entries=sum(1 for r in account['pending_orders'] if r['side']=='BUY' and not r.get('position_id')),hard_locked=bool(account['hard_locked']),**sizing_args)
    pricing=price_entry(context,features,signal_max_price=signal['entry_high'],notional=amount,
        atr=signal['indicators']['5m']['atr14'],cost=cost,expected=expected,stop_distance=stop_dist,**ref_kwargs)
    result.update(cost=cost.asdict(),execution=execution.asdict(),expected=expected.asdict(),sizing=sizing.asdict(),pricing=pricing.asdict())
    # T1 still requires the research pricing gate. T2 is an approved deployment
    # and submits when its own risk size is valid.
    if sizing.status!='RESEARCH_SIZED' or (signal['strategy_id']!='T2' and pricing.status!='RESEARCH_CANDIDATE'):reasons.append('MATH_NOT_APPROVED')
    return signal,original,result


def plan_live_aligned_entry(
    payload,
    features,
    account,
    now,
    rules,
    *,
    shared_cfg=None,
    allowed_strategies=None,
):
    """Compute SOL-bot Live-aligned order price & size for P3 Shadow execution."""
    signal, _original = normalize_signal(payload)
    sid = str(signal.get('strategy_id') or '').upper()
    allow = allowed_strategies if allowed_strategies is not None else parse_allowed_strategies(default=('T1', 'T2', 'R2'))
    ok, deny_reason = strategy_entry_allowed(sid, payload.get('regime'), allowed=allow)
    if not ok:
        return None, deny_reason

    context = make_context(
        symbol=signal['symbol'],
        account_id=ACCOUNTS[signal['symbol']],
        strategy_id=sid,
        strategy_version=signal['strategy_version'],
        signal_id=signal['signal_id'],
        decision_time_ms=now,
        features=features,
        inputs={'signal': signal, 'account': account},
        parameter_version='p3-live-aligned-1',
    )
    f_reasons = list(feature_reasons(context, features))
    if f_reasons:
        return None, f_reasons[0]
    if not signal.get('accepted'):
        return None, 'UPSTREAM_SIGNAL_REJECTED'
    if not signal['signal_time'] <= now < signal['expires_at']:
        return None, 'SIGNAL_EXPIRED_OR_FUTURE'

    price, price_err = compute_entry_limit_price(
        sid,
        signal.get('entry_low'),
        signal.get('entry_high'),
        features.get('bid'),
        features.get('ask'),
        rules.tick_size,
    )
    if price is None:
        return None, price_err

    stop_dist = resolve_stop_distance(payload, entry_ref=signal.get('entry_low') or price)
    if stop_dist is None and sid == 'T1':
        stop_dist = Decimal('0.010')

    min_u = D(shared_cfg['min_order_usdt']) if shared_cfg else LIVE_MIN_ORDER_USDT
    max_u = D(shared_cfg['max_order_usdt']) if shared_cfg else LIVE_MAX_ORDER_USDT
    is_custom = is_custom_trading_config({'custom': bool(shared_cfg)}, min_u, max_u)

    free_cash = floor_step(
        (D(account['cash']) - D(account['reserved'])) / (Decimal('1') + DEFAULT_FEE_RATE),
        Decimal('0.01'),
    )
    ref_equity = max(Decimal('2000'), D(account['last_equity']))
    qty, _order_notional, notional, size_err = compute_live_order_size(
        sid,
        price,
        free_cash,
        ref_equity,
        stop_dist,
        fee_rate=DEFAULT_FEE_RATE,
        score=payload.get('score') or '75',
        min_order_usdt=min_u,
        max_order_usdt=max_u,
        is_custom_cfg=is_custom,
        step_size=rules.step_size,
        min_qty=rules.min_qty,
        rules_min_notional=rules.min_notional,
    )
    if qty is None:
        return None, size_err
    return {
        'limit_price': format(price, 'f'),
        'notional': str(floor_step(notional, Decimal('0.01'))),
        'stop_distance': format(stop_dist.normalize(), 'f') if stop_dist is not None else None,
        'quantity': format(qty, 'f'),
    }, None

def main():
    parser=argparse.ArgumentParser()
    for name in ('symbol','market-db','strategy-db','micro-db','audit-db','status-path'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--shadow-db');parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if (args.symbol=='SOLUSDT')!=bool(args.shadow_db):parser.error('only SOL profile owns shadow ledger')
    paths=[Path(v).resolve() for v in (args.market_db,args.strategy_db,args.micro_db,args.audit_db,args.shadow_db) if v]
    for i,p in enumerate(paths):
        for q in paths[:i]:
            if p==q or p.exists() and q.exists() and os.path.samefile(p,q):parser.error('distinct database files required')
    store=StrategyStore(args.strategy_db);scheduler=Scheduler(args.market_db,store,args.symbol)
    micro=MicrostructureStore(args.micro_db,args.symbol,read_only=True);audit=MathAudit(args.audit_db,args.symbol)
    ledger=None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    port=8000 if args.symbol=='SOLUSDT' else 8001
    last_error_key=None
    last_snapshot_ms=0
    flow_smoother=FlowSmoother()
    while True:
        now=time.time_ns()//1000000
        status={'symbol':args.symbol,'updated_at':now,'engine':'P3','live_authorized':False,'status':'unavailable'}
        try:
            features=micro.latest()
            if features is None:raise ValueError('no public depth snapshot')
            features=refresh_features(features,now)
            if args.shadow_db and ledger is None:ledger=P3ShadowExecutor(args.shadow_db,rules=SymbolRules.from_exchange_info(features['rules'],args.symbol),fee_rate='0.00075',minimum_notional='500',maximum_notional='1000')
            shared_cfg=None
            if ledger:
                shared_cfg=load_shared_trading_config(args.market_db,args.shadow_db)
                if shared_cfg:
                    ledger.apply_trading_config(shared_cfg['min_order_usdt'],shared_cfg['max_order_usdt'],shared_cfg['max_slots'])
            evidence=TrendEvidence();market_ready=False
            try:
                with opener.open(f'http://127.0.0.1:{port}/health',timeout=2) as r:health=json.load(r)
                market_ready=health.get('symbol')==args.symbol and health.get('ready') is True
                if market_ready:
                    status['scheduler']=scheduler_status(scheduler.tick(now_ms=now))
                    checkpoint=store.checkpoint(args.symbol)
                    if checkpoint:
                        with scheduler.reader() as db:
                            db.execute('BEGIN');candles=scheduler._inputs(db,checkpoint)
                        smoothed_features=smooth_micro_features(features,flow_smoother)
                        evidence=from_closed_inputs(args.symbol,candles,smoothed_features,checkpoint)
                        if ledger and ledger.snapshot()['positions']:
                            features=dict(features,protection_closed_candles=candles,protection_as_of_ms=checkpoint)
            except Exception as exc:status['candle_error']=str(exc)[:160]
            # Always run existing protection before any new admission, including
            # while candle health or trade-flow warmup is unavailable.
            if ledger:
                ledger.chase_pending(features,now)
                status['execution']=ledger.process_market(features,evidence,now_ms=now)
                account=ledger.snapshot();status['shadow']=account
                status['last_successful_write_at']=now
                if now-last_snapshot_ms>=5000:
                    publish(Path(args.shadow_db),Path(args.shadow_db).with_name('sol-shadow-read.sqlite'))
                    last_snapshot_ms=now
                status['snapshot_at']=last_snapshot_ms
                status['snapshot_id']=str(last_snapshot_ms)
            else:account={'cash':'2000','reserved':'0','last_equity':'2000','peak_equity':'2000','hard_locked':0,'positions':[],'pending_orders':[]}
            if market_ready and features.get('ready'):
                records=store.accepted_after(symbol=args.symbol,now_ms=now,limit=100)
                # One rejected research diagnostic per strategy per five-minute
                # candidate, rather than per depth event. Never execute it.
                for row in store.connection.execute('SELECT payload_json FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 3',(args.symbol,)):
                    value=json.loads(row[0])
                    if not value['accepted']:records.append(value)
                for payload in records:
                    key=payload['signal_id'] if payload['accepted'] else f"diagnostic:{payload['strategy_id']}:{payload['candidate_time']}"
                    if audit.contains(key):continue
                    normalized,original,result=plan_signal(payload,features,account,now,args.market_db)
                    live_order_plan,live_deny=(None,None)
                    if ledger and payload['accepted']:
                        live_order_plan,live_deny=plan_live_aligned_entry(
                            payload,features,account,now,ledger.rules,shared_cfg=shared_cfg,
                        )
                    if ledger and payload['accepted'] and live_order_plan is not None:
                        candidate=dict(payload)
                        if live_order_plan.get('stop_distance'):
                            candidate['stop_distance']=live_order_plan['stop_distance']
                        attach_reference_price(candidate,ledger.rules,features,args.market_db,now)
                        claimed=False
                        try:
                            claimed=acquire_cross_shadow_entry_guard(
                                args.shadow_db,args.market_db,
                                strategy_id=candidate['strategy_id'],
                                signal_id=candidate['signal_id'],
                                signal_time=int(candidate.get('signal_time') or now),
                                now_ms=now,
                                strategy_db=args.strategy_db,
                                regime=candidate.get('regime'),
                            )
                            result['order']=ledger.submit_candidate(
                                candidate,
                                notional=live_order_plan['notional'],
                                limit_price=live_order_plan['limit_price'],
                                now_ms=now,
                                allow_price_below_low=True,
                            )
                            account=ledger.snapshot();status['shadow']=account
                            publish(Path(args.shadow_db),Path(args.shadow_db).with_name('sol-shadow-read.sqlite'))
                            last_snapshot_ms=now
                        except ShadowRejected as exc:
                            if claimed:
                                release_cross_shadow_entry_guard(args.market_db,candidate['signal_id'],own_shadow_db=args.shadow_db)
                            result['order_rejected']={'code':exc.code,'message':str(exc)}
                    elif ledger and payload['accepted'] and live_deny:
                        result['order_rejected']={'code':live_deny,'message':f'blocked by live-aligned policy: {live_deny}'}
                    rej_code=(result.get('order_rejected') or {}).get('code')
                    is_transient_reject=(
                        ledger is not None
                        and payload.get('accepted')
                        and 'order' not in result
                        and rej_code in ('CROSS_SHADOW_SLOTS_FULL','CROSS_STRATEGY_COOLDOWN','CHASE_EXCEEDED','DENY_MAX_SLOTS')
                        and now<int(payload.get('expires_at') or 0)
                    )
                    audit_key=f"attempt:{key}:{now//60000}" if is_transient_reject else key
                    if not audit.contains(audit_key):
                        audit.record(audit_key,now,{'signal':normalized,'p2_original_payload':original,'features':features,'account':account},result)
            status.update(status='ready' if market_ready and features.get('ready') else 'protection_only',micro_ready=features.get('ready'),risk_policy=ledger.risk_policy if ledger else 'LIVE_DENIED')
        except Exception as exc:
            status['error']=f'{type(exc).__name__}: {exc}'[:240]
            status['sqlite_errorcode']=getattr(exc,'sqlite_errorcode',None)
            status['sqlite_errorname']=getattr(exc,'sqlite_errorname',None)
            # Emit a traceback on changes, keeping persistent failures from
            # generating one full traceback per polling second.
            error_key=(type(exc).__name__,str(exc))
            if error_key != last_error_key:
                logging.exception('Shadow runtime failure')
            last_error_key=error_key
        else:
            last_error_key=None
        atomic_json(Path(args.status_path),json_safe(status))
        if args.once:print(json.dumps(json_safe(status)));break
        time.sleep(1)

if __name__=='__main__':main()
