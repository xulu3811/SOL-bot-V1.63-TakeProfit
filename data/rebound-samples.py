import sqlite3,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113');c=sqlite3.connect(p/'sol-shadow-strategy.sqlite');z=timezone(timedelta(hours=8))
for h in ['12:04:59','13:16:59','13:17:59','14:24:59','15:09:59','19:43:59']:
 t=int(datetime.fromisoformat('2026-11-13T'+h+'+08:00').timestamp()*1000)+999
 for (s,) in c.execute("select payload_json from decisions where signal_time=? and strategy_id='T1'",(t,)):
  r=json.loads(s);print(h,{k:r[k] for k in ['score','score_factors','reasons']})
