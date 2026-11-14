"""Separate strategy worker consuming local public-market health and closed data."""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
import time
import urllib.error
import urllib.request

from solbot.strategy.scheduler import Scheduler, StrategyStore
from solbot.strategy import VERSION

log=logging.getLogger(__name__)

def atomic_json(path: Path, value: dict) -> None:
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,allow_nan=False),encoding='utf-8')
    temporary.replace(path)

def load_cursor(path: Path, binding: dict, store: StrategyStore) -> int:
    if not path.exists():
        return 0
    state=json.loads(path.read_text())
    if state.get('binding')!=binding:
        raise ValueError('execution cursor source/account differs; explicit recovery is required')
    cursor=state['last_decision_id']
    row=store.connection.execute('SELECT signal_id FROM decisions WHERE id=?',(cursor,)).fetchone()
    if row is None or row[0]!=state.get('signal_id'):
        # Restored/replaced history is reconsumed through the ledger's durable
        # signal idempotency; expiry filters prevent historical order creation.
        return 0
    return cursor

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--symbol',choices=('SOLUSDT','SUIUSDT'),required=True)
    parser.add_argument('--market-db',required=True)
    parser.add_argument('--strategy-db',required=True)
    parser.add_argument('--shadow-db')
    parser.add_argument('--status-path',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if args.shadow_db and args.symbol!='SOLUSDT':
        parser.error('this deployed shadow profile requires SOLUSDT')
    paths=[Path(value).resolve() for value in (args.market_db,args.strategy_db,args.shadow_db) if value]
    import os
    for index,path in enumerate(paths):
        for other in paths[:index]:
            if path==other or (path.exists() and other.exists() and os.path.samefile(path,other)):
                parser.error('market, strategy and shadow database files must be distinct')
    logging.basicConfig(level=logging.INFO)
    store=StrategyStore(args.strategy_db)
    scheduler=Scheduler(args.market_db,store,args.symbol)
    shadow=None
    if args.shadow_db:
        from solbot.execution.shadow import ShadowExecutor
        shadow=ShadowExecutor(args.shadow_db,symbol=args.symbol)
    path=Path(args.status_path)
    cursor_path=path.with_suffix('.cursor.json')
    binding={'strategy_db':str(Path(args.strategy_db).resolve()),'version':VERSION,
             'shadow_db':str(Path(args.shadow_db).resolve()) if args.shadow_db else None,
             'symbol':args.symbol,'account':'SOL-shadow' if shadow else 'decisions_only'}
    cursor=load_cursor(cursor_path,binding,store)
    port=8000 if args.symbol=='SOLUSDT' else 8001
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        now=time.time_ns()//1_000_000
        result={'symbol':args.symbol,'updated_at':now,'status':'unavailable','execution_mode':'shadow' if shadow else 'decisions_only'}
        try:
            with opener.open(f'http://127.0.0.1:{port}/health',timeout=5) as response:
                health=json.load(response)
            if health.get('symbol')!=args.symbol or not health.get('ready'):
                raise ValueError('market not ready for configured symbol')
            result['scheduler']=scheduler.tick(now_ms=now)
            result['status']='ready'
            if shadow is not None:
                quote=health['collector'].get('book_ticker')
                stamp=health['collector'].get('book_ticker_ms',0)
                if quote and quote.get('symbol')==args.symbol and 0<=now-stamp<=10_000:
                    result['fills']=shadow.process_quote(args.symbol,quote['bidPrice'],quote['askPrice'],stamp)
                consumed=[]
                for record in store.accepted_after(after_id=cursor,symbol=args.symbol,now_ms=now,limit=100):
                    try:
                        notional='300'
                        if record.get('strategy_id')=='T2':
                            from solbot.strategy.t2_swing import risk_notional
                            account=shadow.snapshot()
                            sized=risk_notional(account['last_equity'],record.get('stop_distance') or '0',shadow.fee_rate,record.get('score') or 75)
                            if sized is None:
                                raise ValueError('T2_SIZE')
                            notional=format(sized,'f')
                        order=shadow.submit_candidate(record,notional=notional,now_ms=now)
                        consumed.append({'decision_id':record['decision_id'],'result':order})
                    except ValueError as exc:
                        consumed.append({'decision_id':record['decision_id'],'rejected':str(exc)[:200]})
                    cursor=record['decision_id']
                    atomic_json(cursor_path,{'binding':binding,'last_decision_id':cursor,'signal_id':record['signal_id']})
                result['decisions_consumed']=consumed
                result['shadow']=shadow.snapshot()
        except Exception as exc:
            result['error']=f'{type(exc).__name__}: {str(exc)[:200]}'
            log.warning('Worker %s unavailable: %s',args.symbol,result['error'])
        atomic_json(path,result)
        if args.once:
            print(json.dumps(result));break
        time.sleep(5)

if __name__=='__main__':main()
