"""Replay deployed strategy on copied closed candles, with isolated counterfactuals."""
import sys,json,sqlite3,bisect,time,csv
from pathlib import Path
from datetime import datetime,timezone,timedelta
from collections import Counter,defaultdict
p=Path(__file__).resolve().parent/'rebound-20261113'
sys.path.insert(0,str(p/'deployed/backend/src'))
from solbot.strategy import evaluate
from solbot.storage.models import INTERVAL_MS
zone=timezone(timedelta(hours=8))
start=int(datetime(2026,11,6,tzinfo=zone).timestamp()*1000)
today=int(datetime(2026,11,13,tzinfo=zone).timestamp()*1000)
m=sqlite3.connect(p/'market.sqlite');m.row_factory=sqlite3.Row
frames={tf:[dict(r) for r in m.execute('select * from market_klines where interval=? and is_closed=1 order by close_time',(tf,))] for tf in INTERVAL_MS}
closes={tf:[r['close_time'] for r in rr] for tf,rr in frames.items()}
source=sqlite3.connect(p/'sol-shadow-strategy.sqlite')
observed={(r['strategy_id'],r['signal_time']):r for (payload,) in source.execute('select payload_json from decisions where signal_time>=?',(today,)) for r in [json.loads(payload)]}
macro={'REGIME_NOT_UPTREND','HIGHER_TIMEFRAME_WEAK_TREND','R1_REQUIRES_RANGE','MACRO_DOWNTREND_GUARD','FOUR_HOUR_DOWNTREND'}
counts=defaultdict(Counter);reasons=defaultdict(Counter);examples={};csvrows=[];mismatch=Counter();n=0
for bar in frames['1m']:
 t=bar['close_time']
 if t<start:continue
 inputs={tf:rr[max(0,bisect.bisect_right(closes[tf],t)-120):bisect.bisect_right(closes[tf],t)] for tf,rr in frames.items()}
 rr=evaluate('SOLUSDT',inputs,t)
 period='today' if t>=today else 'prior7days'
 for r in rr:
  sid=r['strategy_id'];key=period+'/'+sid;c=counts[key];c['evaluations']+=1;c['accepted']+=r['accepted'];c['max_score']=max(c['max_score'],r['score']);reasons[key].update(r['reasons'])
  rs=set(r['reasons']);c['lower_threshold_50']+=not(rs-{'SCORE_BELOW_75'}) and r['score']>=50
  c['remove_macro_and_score_only']+=not(rs-macro-{'SCORE_BELOW_75'})
  c['remove_macro_only']+=not(rs-macro)
  if not(rs-macro-{'SCORE_BELOW_75'}):examples.setdefault(key,[]).append({'time':datetime.fromtimestamp(t/1000,zone).isoformat(),'price':bar['close'],'score':r['score'],'reasons':r['reasons']})
  old=observed.get((sid,t))
  if old:
   if old['accepted']!=r['accepted']:mismatch['accepted']+=1
   if any(abs(old['score_factors'][f]-r['score_factors'][f])>0.001 for f in ['trend','momentum','participation','structure']):mismatch['non_execution_factors']+=1
   if set(old['reasons'])!=rs:mismatch['reasons']+=1
  if t>=today:
   csvrows.append({'time':datetime.fromtimestamp(t/1000,zone).isoformat(),'strategy':sid,'price':bar['close'],'score':r['score'],'accepted':r['accepted'],'regime':r['regime'],'reasons':'|'.join(r['reasons']),**r['score_factors']})
 n+=1
 if n%1000==0:print('evaluated',n,flush=True)
result={'period_start':datetime.fromtimestamp(start/1000,zone).isoformat(),'period_end':datetime.fromtimestamp(closes['1m'][-1]/1000,zone).isoformat(),'counts':dict(counts),'reasons':dict(reasons),'counterfactual_examples':examples,'today_comparison_mismatches':mismatch,'microstructure':'OHLCV only, execution component 7.5/15; live spread and imbalance not reconstructed'}
(p/'replay.json').write_text(json.dumps(result,indent=2),encoding='utf8')
with (p/'replay-today.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(csvrows[0]));w.writeheader();w.writerows(csvrows)
print(json.dumps({'counts':dict(counts),'mismatches':mismatch,'examples':examples},indent=2))
