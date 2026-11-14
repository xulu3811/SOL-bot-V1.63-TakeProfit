import sqlite3,json,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('data/rebound-20261113');c=sqlite3.connect(p/'market.sqlite');c.row_factory=sqlite3.Row
out={}
for tf,step in [('1m',60000),('5m',300000),('15m',900000),('1h',3600000),('4h',14400000)]:
 rows=list(c.execute('select * from market_klines where interval=? and is_closed=1 order by open_time',(tf,)))
 out[tf]={'bars':len(rows),'gaps':sum(b['open_time']-a['open_time']!=step for a,b in zip(rows,rows[1:])),'bad_close_time':sum(r['close_time']!=r['open_time']+step-1 for r in rows),'invalid_ohlc':sum(float(r['low'])>min(float(r['open']),float(r['close'])) or float(r['high'])<max(float(r['open']),float(r['close'])) for r in rows)}
files=['strategy/__init__.py','strategy/t1_pullback.py','strategy/r1_reversal.py','strategy/t2_swing.py','strategy/market_regime.py','strategy/scheduler.py']
out['source_hashes']={f:hashlib.sha256((p/'deployed/backend/src/solbot'/f).read_bytes()).hexdigest() for f in files}
s=sqlite3.connect(p/'sol-shadow-strategy.sqlite');zone=timezone(timedelta(hours=8))
for sid in ['T1','R1','T2']:
 t=s.execute('select min(signal_time) from decisions where strategy_id=?',(sid,)).fetchone()[0];out[sid+'_first_record']=datetime.fromtimestamp(t/1000,zone).isoformat()
(p/'integrity.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
