"""Diagnostic counterfactual, fixed exits, no production changes."""
import json,sqlite3,bisect
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113');r=json.loads((p/'replay.json').read_text());z=timezone(timedelta(hours=8))
c=sqlite3.connect(p/'market.sqlite');c.row_factory=sqlite3.Row
bars=[dict(x) for x in c.execute("select * from market_klines where interval='1m' and is_closed=1 order by open_time")];times=[x['open_time'] for x in bars]
def iso(t):return datetime.fromtimestamp(t/1000,z).isoformat()
fee=.00075;slip=.0005
results={}
for period in ['prior7days','today']:
 trades=[];last_signal=0;busy_until=0
 for signal in r['counterfactual_examples'].get(period+'/T1',[]):
  t=int(datetime.fromisoformat(signal['time']).timestamp()*1000)
  if t<busy_until or t-last_signal<3600000:continue
  i=bisect.bisect_right(times,t)
  if i>=len(bars):continue
  entry=float(bars[i]['open'])*(1+slip);stop=entry*.99;target=entry*1.015
  end=times[i]+4*3600000
  exit_price=None;reason='END_OF_SAMPLE';exit_t=0
  for b in bars[i:]:
   if b['open_time']>=end:
    exit_price=float(b['open'])*(1-slip);reason='4H_TIMEOUT';exit_t=b['open_time'];break
   if float(b['low'])<=stop:
    exit_price=min(stop,float(b['open']))*(1-slip);reason='STOP';exit_t=b['close_time'];break
   if float(b['high'])>=target:
    exit_price=target*(1-slip);reason='TARGET';exit_t=b['close_time'];break
  if exit_price is None:exit_price=float(bars[-1]['close'])*(1-slip);exit_t=bars[-1]['close_time']
  profit=300*(exit_price/entry*(1-fee)-(1+fee))
  trades.append({'signal_time':iso(t),'entry_time':iso(times[i]),'entry':entry,'exit_time':iso(exit_t),'exit':exit_price,'exit_reason':reason,'pnl_usdt':profit,'net_trade_pct':profit/300*100})
  busy_until=exit_t;last_signal=t
 results[period]={'trades':trades,'count':len(trades),'wins':sum(t['pnl_usdt']>0 for t in trades),'pnl_usdt':sum(t['pnl_usdt'] for t in trades),'account_return_pct':sum(t['pnl_usdt'] for t in trades)/2000*100}
results['assumptions']={'entry':'next minute open + 5bp slippage','exit':'1% stop / 1.5% target / 4h timeout, stop first if both touched, 5bp exit slippage','fee_each_side':fee,'notional':300,'initial_capital':2000,'concurrency':1,'signal_cooldown_minutes':60,'variant':'Remove T1 macro gates and score threshold only; all other conditions required','caveat':'diagnostic fixed-exit simulation, not production APTS/execution/risk replay; selected after seeing event; not out-of-sample'}
(p/'counterfactual.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
