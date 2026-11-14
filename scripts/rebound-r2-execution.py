"""Offline P3/APTS replay with explicitly synthetic intraminute depth."""
from pathlib import Path
from decimal import Decimal as D
import sys,sqlite3,json,bisect
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend/src'))
from solbot.execution.r2_shadow import R2ShadowExecutor
from solbot.execution.shadow import ShadowRejected
from solbot.mathematics.contracts import SymbolRules
from solbot.mathematics.decimal_utils import canonical_hash,json_safe
from solbot.mathematics.trend_exhaustion import from_closed_inputs
p=root/'data/rebound-20261113';output=p/'r2-execution-100.sqlite'
if output.exists():raise RuntimeError('Execution output already exists')
class OfflineExecutor(R2ShadowExecutor):
 def _connect(self):
  db=super()._connect();db.execute('PRAGMA synchronous=OFF');return db
rules=SymbolRules('SOLUSDT',D('.01'),D('.001'),D('.01'),D('1000000'),D('.001'),D('1000000'),D('5'))
ledger=OfflineExecutor(output,rules=rules,fee_rate='0.00075')
c=sqlite3.connect(p/'market.sqlite');c.row_factory=sqlite3.Row
frames={tf:[dict(r) for r in c.execute('select * from market_klines where interval=? and is_closed=1 order by close_time',(tf,))] for tf in ['1m','5m','15m','1h','4h']}
times={tf:[r['close_time'] for r in rows] for tf,rows in frames.items()}
signals={r['signal_time']:r for r in json.loads((p/'r2-v2-validation.json').read_text())['signals']}
results=[];active=False;counter=0
for bar in frames['1m']:
 t=bar['close_time']
 if active:
  prior=bar['open_time']-1
  inputs={}
  for tf,rows in frames.items():
   i=bisect.bisect_right(times[tf],prior);inputs[tf]=rows[max(0,i-120):i]
  last=inputs['1m'][-1];ratio=D(last['taker_buy_quote_volume'])/D(last['quote_volume']) if D(last['quote_volume']) else D('.5')
  ledger.invalidate_pending(last['close'],prior)
  for offset,key in [(1,'open'),(15000,'low'),(30000,'high'),(59999,'close')]:
   stamp=bar['open_time']+offset;bid=D(bar[key]);ask=bid+D('.01');qty=max(D('.001'),D(last['volume'])/4)
   counter+=1
   features={'symbol':'SOLUSDT','ready':True,'synced':True,'exchange_time_ms':stamp,'received_time_ms':stamp,'bid':str(bid),'ask':str(ask),'bids':[[str(bid),str(qty)]],'asks':[[str(ask),str(qty)]],'update_id':counter,'buy_ratio':str(ratio),'input_hash':canonical_hash([stamp,key,str(bid)])}
   evidence=from_closed_inputs('SOLUSDT',inputs,features,prior)
   ledger.process_market(features,evidence,now_ms=stamp)
  account=ledger.snapshot();active=bool(account['positions'] or account['pending_orders'])
 if t in signals:
  signal=signals[t]
  try:
   order=ledger.submit_intent(signal,t);results.append({'time':t,'price':signal['entry_low'],'order_id':order['order_id']});active=True
  except ShadowRejected as exc:results.append({'time':t,'price':signal['entry_low'],'rejected':exc.code})
account=ledger.snapshot()
report={'assumptions':{'depth':'synthetic, previous minute volume/4 per quote; 10% participation','path':'open-low-high-close at 1ms,15s,30s,59.999s; intrabar path is unobserved','spread_usdt':'.01','fee_each_side':'.00075','slippage_each_side':'.0005','signal_source':'r2-rebound-2; no future candles','engine':'production P3 paper fills and APTS with prior closed candles','risk_budget':'.002','minimum_notional':str(ledger.minimum_notional),'qualification':'development sample; not out-of-sample; exchange filters modeled'},'admissions':results,'account':account,'positions':ledger.positions(),'orders':ledger.orders()}
(p/'r2-execution-100.json').write_text(json.dumps(json_safe(report),indent=2))
print(json.dumps(json_safe({'admissions':results,'equity':account['last_equity'],'fills':account['fill_count'],'positions':ledger.positions()}),indent=2))

