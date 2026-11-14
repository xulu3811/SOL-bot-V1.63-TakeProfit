import sqlite3,json,sys,collections
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113');tz=timezone(timedelta(hours=8))
def dt(t):return datetime.fromtimestamp(t/1000,tz).strftime('%m-%d %H:%M:%S')
start=int(datetime(2026,11,13,tzinfo=tz).timestamp()*1000)
c=sqlite3.connect(p/'sol-shadow-strategy.sqlite')
rows=[json.loads(r[0]) for r in c.execute('select payload_json from decisions where signal_time>=? order by signal_time',(start,))]
out={}
for period,hour in [('day',0),('rebound',12)]:
 out[period]={}
 for sid in ['T1','R1','T2']:
  rr=[r for r in rows if r['strategy_id']==sid and r['signal_time']>=start+hour*3600000]
  out[period][sid]={'n':len(rr),'accepted':sum(r['accepted'] for r in rr),'score_max':max((r['score'] for r in rr),default=0),'above50':sum(r['score']>=50 for r in rr),'above75':sum(r['score']>=75 for r in rr),'reasons':dict(collections.Counter(x for r in rr for x in r['reasons'])),'regimes':dict(collections.Counter(r['regime'] for r in rr))}
  if rr:
   best=max(rr,key=lambda r:r['score']);out[period][sid]['best']={k:best[k] for k in ['score','score_factors','reasons','entry_low']};out[period][sid]['best']['time']=dt(best['signal_time'])
m=sqlite3.connect(p/'market.sqlite');m.row_factory=sqlite3.Row
bars=[dict(r) for r in m.execute("select * from market_klines where interval='1m' and is_closed=1 and close_time>=? order by close_time",(start,))]
low=min(bars,key=lambda r:float(r['low']));later=[r for r in bars if r['open_time']>=low['open_time']];high=max(later,key=lambda r:float(r['high']))
out['price']={'low':low['low'],'low_time':dt(low['open_time']),'high_after':high['high'],'high_time':dt(high['open_time']),'last':bars[-1]['close'],'last_time':dt(bars[-1]['close_time'])}
out['checkpoints']=[]
for h in [11,12,13,14,15,16,17,18,19]:
 rr=[r for r in rows if r['strategy_id']=='T1' and r['signal_time']<start+h*3600000]
 if rr:
  r=rr[-1];out['checkpoints'].append({'time':dt(r['signal_time']),'price':r['entry_low'],'score':r['score'],'factors':r['score_factors'],'regime':r['regime'],'indicators':r['indicators']})
s=sqlite3.connect(p/'sol-shadow.sqlite');s.row_factory=sqlite3.Row
out['shadow']={t:s.execute('select count(*) from '+t).fetchone()[0] for t in ['shadow_orders','shadow_fills','shadow_positions']};out['shadow']['account']=dict(s.execute('select * from shadow_account').fetchone())
out['decision_range']=[dt(min(r['signal_time'] for r in rows)),dt(max(r['signal_time'] for r in rows))]
(p/'observed.json').write_text(json.dumps(out,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k!='checkpoints'},indent=2))
