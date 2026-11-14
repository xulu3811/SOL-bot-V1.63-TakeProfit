import json,sqlite3,sys,bisect
from pathlib import Path
from collections import Counter
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend/src'))
from solbot.strategy import indicators,market_regime,prepare
from solbot.storage.r2_store import R2Store
source=root/'data/rebound-20261113/market.sqlite'
out=root/'data/rebound-20261113/r2-v2-validation.sqlite'
if out.exists():raise RuntimeError('Choose a fresh output for a repeat run')
c=sqlite3.connect(source);c.row_factory=sqlite3.Row
frames={tf:[dict(r) for r in c.execute('select * from market_klines where interval=? and is_closed=1 order by close_time',(tf,))] for tf in ['1m','5m','15m','1h','4h']}
times={tf:[r['close_time'] for r in rows] for tf,rows in frames.items()}
store=R2Store(out,mode='replay');counts=Counter();signals=[]
# Rebuildable offline results; production stores retain durable commits.
store.connection.execute('PRAGMA synchronous=OFF')
start=1793894400000
for n,bar in enumerate(r for r in frames['1m'] if r['close_time']>=start):
 t=bar['close_time'];candles={}
 for tf,rows in frames.items():
  i=bisect.bisect_right(times[tf],t);candles[tf]=rows[max(0,i-120):i]
 candles,errors=prepare('SOLUSDT',candles,t)
 if errors:raise RuntimeError(errors)
 metrics={tf:indicators.series(rows) for tf,rows in candles.items()}
 regime,_=market_regime.classify(candles,metrics)
 state,intent=store.step(candles,metrics,regime,t);counts[state['state']]+=1
 if intent:
  signals.append(intent);store.acknowledge(intent['signal_id'],{'mode':'signal_replay','execution':'not_simulated'})
 if n%2000==0:print(n,flush=True)
store.close()
result={'states':dict(counts),'signals':signals,'note':'signal replay only; no execution or profit inference'}
(root/'data/rebound-20261113/r2-v2-validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
