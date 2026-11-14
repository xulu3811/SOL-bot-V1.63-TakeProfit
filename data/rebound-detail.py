import json,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113');sys.path.insert(0,str(p/'deployed/backend/src'))
from solbot.strategy import indicators
zone=timezone(timedelta(hours=8))
def ts(s):return int(datetime.fromisoformat('2026-11-13T'+s+'+08:00').timestamp()*1000)
def dt(t):return datetime.fromtimestamp(t/1000,zone).strftime('%H:%M:%S')
c=sqlite3.connect(p/'market.sqlite');c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute("select * from market_klines where interval='5m' and is_closed=1 order by close_time")]
out=[]
for i,r in enumerate(rows):
 if ts('11:50:00')<=r['close_time']<=ts('15:30:00'):
  m=indicators.series(rows[max(0,i-119):i+1]);out.append({'time':dt(r['close_time']),'close':r['close'],'rsi6':round(m['rsi6'][-1],2),'prior4_rsi_min':round(min(m['rsi6'][-5:-1]),2),'rvol':round(m['rvol20'][-1],2)})
(p/'noon-indicators.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out))
print('milestones')
for price in [79,79.5,80,80.5]:
 r=c.execute("select open_time,close from market_klines where interval='1m' and open_time>=? and cast(close as real)>=? order by open_time limit 1",(ts('11:53:00'),price)).fetchone();print(price,dt(r[0]),r[1])
