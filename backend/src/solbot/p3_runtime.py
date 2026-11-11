"""P2 closed-candle scheduling plus P3 math proposals and protected simulation."""
import argparse,json,time,urllib.request,os
from pathlib import Path
from decimal import Decimal
from solbot.p2_runtime import atomic_json
from solbot.strategy.scheduler import StrategyStore,Scheduler
from solbot.storage.microstructure import MicrostructureStore
from solbot.storage.math_audit import MathAudit,normalize_signal
from solbot.mathematics.contracts import ACCOUNTS,SymbolRules,make_context,feature_reasons
from solbot.mathematics.decimal_utils import D,floor_step,json_safe
from solbot.mathematics.trend_exhaustion import TrendEvidence,from_closed_inputs
from solbot.mathematics.execution_cost import estimate_cost
from solbot.mathematics.expected_return import estimate_return
from solbot.mathematics.fill_probability import estimate_fill
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.position_sizing import size_position
from solbot.execution.p3_shadow import P3ShadowExecutor

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

def plan_signal(payload,features,account,now):
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
    price=D(features['ask'])
    quantity=floor_step(Decimal(500)/price,rules.step_size)
    amount=quantity*price
    cost=estimate_cost(context,entry_price=price,exit_price=price*Decimal('1.015'),quantity=quantity)
    execution=estimate_fill(context,features,limit_price=price,quantity=quantity,wait_ms=min(60000,max(1,signal['expires_at']-now)))
    expected=estimate_return(context,notional=amount,profit_fraction='.015',loss_fraction='.008',cost=cost,execution=execution)
    sizing=size_position(context,features,score=signal['score'],entry_price=price,equity=account['last_equity'],high_watermark=account['peak_equity'],
        cash_balance=account['cash'],reserved_entry_notional=account['reserved'],open_positions=len(account['positions']),
        pending_entries=sum(1 for r in account['pending_orders'] if r['side']=='BUY' and not r.get('position_id')),hard_locked=bool(account['hard_locked']))
    pricing=price_entry(context,features,signal_max_price=signal['entry_high'],notional=amount,
        atr=signal['indicators']['5m']['atr14'],cost=cost,expected=expected)
    result.update(cost=cost.asdict(),execution=execution.asdict(),expected=expected.asdict(),sizing=sizing.asdict(),pricing=pricing.asdict())
    # Unobserved win/cost evidence remains insufficient. No fabricated evidence
    # is injected merely to generate simulated entries.
    if pricing.status!='RESEARCH_CANDIDATE' or sizing.status!='RESEARCH_SIZED':reasons.append('MATH_NOT_APPROVED')
    return signal,original,result

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
    while True:
        now=time.time_ns()//1000000
        status={'symbol':args.symbol,'updated_at':now,'engine':'P3','live_authorized':False,'status':'unavailable'}
        try:
            features=micro.latest()
            if features is None:raise ValueError('no public depth snapshot')
            features=refresh_features(features,now)
            if args.shadow_db and ledger is None:ledger=P3ShadowExecutor(args.shadow_db,rules=SymbolRules.from_exchange_info(features['rules'],args.symbol),fee_rate='0.00075')
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
                        evidence=from_closed_inputs(args.symbol,candles,features,checkpoint)
                        if ledger and ledger.snapshot()['positions']:
                            features=dict(features,protection_closed_candles=candles,protection_as_of_ms=checkpoint)
            except Exception as exc:status['candle_error']=str(exc)[:160]
            # Always run existing protection before any new admission, including
            # while candle health or trade-flow warmup is unavailable.
            if ledger:
                status['execution']=ledger.process_market(features,evidence,now_ms=now)
                account=ledger.snapshot();status['shadow']=account
            else:account={'cash':'2000','reserved':'0','last_equity':'2000','peak_equity':'2000','hard_locked':0,'positions':[],'pending_orders':[]}
            if market_ready and features.get('ready'):
                records=store.accepted_after(symbol=args.symbol,now_ms=now,limit=100)
                # One rejected research diagnostic per strategy per five-minute
                # candidate, rather than per depth event. Never execute it.
                for row in store.connection.execute('SELECT payload_json FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 2',(args.symbol,)):
                    value=json.loads(row[0])
                    if not value['accepted']:records.append(value)
                for payload in records:
                    key=payload['signal_id'] if payload['accepted'] else f"diagnostic:{payload['strategy_id']}:{payload['candidate_time']}"
                    if audit.contains(key):continue
                    normalized,original,result=plan_signal(payload,features,account,now)
                    if ledger and payload['accepted'] and not result['gate_reasons']:
                        pricing,sizing=result['pricing'],result['sizing']
                        candidate=dict(payload);candidate['entry_low']=str(min(D(candidate['entry_low']),D(pricing['limit_price'])))
                        result['order']=ledger.submit_candidate(candidate,notional=sizing['notional'],limit_price=pricing['limit_price'],now_ms=now)
                    audit.record(key,now,{'signal':normalized,'p2_original_payload':original,'features':features,'account':account},result)
            status.update(status='ready' if market_ready and features.get('ready') else 'protection_only',micro_ready=features.get('ready'),risk_policy=ledger.risk_policy if ledger else 'LIVE_DENIED')
        except Exception as exc:status['error']=f'{type(exc).__name__}: {exc}'[:240]
        atomic_json(Path(args.status_path),json_safe(status))
        if args.once:print(json.dumps(json_safe(status)));break
        time.sleep(1)

if __name__=='__main__':main()
