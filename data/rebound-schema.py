import sqlite3,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113')
for name in ['market','sol-shadow-strategy','sol-shadow','sol-math-audit']:
 c=sqlite3.connect(p/(name+'.sqlite'));c.row_factory=sqlite3.Row
 print(name)
 for row in c.execute("select name,sql from sqlite_master where type='table'"):
  print(row['name'],row['sql'][:500])
 if name=='market':print([dict(r) for r in c.execute('select interval,count(*) n,min(close_time) first,max(close_time) last from market_klines group by interval')])
 if name=='sol-shadow-strategy':
  print(dict(c.execute('select * from decisions order by id desc limit 1').fetchone()))
