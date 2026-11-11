set -eu
date -u +%FT%TZ
readlink -f /opt/sol-bot/p2
systemctl show solbot-market.service solbot-sui-market.service sol-shadow.service sui-strategy.service -p Id -p ActiveState -p MainPID -p NRestarts -p MemoryCurrent -p ActiveEnterTimestamp
systemctl is-active sol-bot-backup.timer sol-bot-health.timer
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
import hashlib,json,sqlite3,time
base=Path('/var/lib/sol-bot')
for name in ('sol-shadow','sui-strategy'):
 p=base/(name+'-status.json')
 status=json.loads(p.read_text())
 assert status['status']=='ready',status
 assert 0<=time.time_ns()//1000000-status['updated_at']<30000
 print('WORKER',json.dumps(status,sort_keys=True))
 p=base/'database'/(name+'-strategy.sqlite')
 with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row
  print('STRATEGY',name,json.dumps({'checkpoint':[dict(r) for r in db.execute('SELECT * FROM checkpoints')],
   'counts':[dict(r) for r in db.execute('SELECT symbol,strategy_id,accepted,count(*) AS count FROM decisions GROUP BY symbol,strategy_id,accepted')],
   'latest':json.loads(db.execute('SELECT payload_json FROM decisions ORDER BY id DESC LIMIT 1').fetchone()[0]),
   'duplicates':db.execute('SELECT count(*) FROM (SELECT symbol,candidate_time FROM decisions WHERE accepted=1 GROUP BY symbol,candidate_time HAVING count(*)>1)').fetchone()[0]}))
for name in ('sol-shadow.sqlite','sui-live-control.sqlite'):
 p=base/'database'/name
 with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row
  table='shadow_account' if name.startswith('sol-') else 'live_control'
  row=dict(db.execute('SELECT * FROM '+table).fetchone())
  if table=='live_control':assert row['state']=='LIVE_OFF'
  print('ACCOUNT',name,json.dumps(row))
  if table=='shadow_account':
   print('SHADOW_ORDERS',json.dumps([dict(r) for r in db.execute('SELECT side,status,count(*) AS count FROM shadow_orders GROUP BY side,status')]))
# Preserve the exact immutable source snapshots used by the ongoing replay.
archive=base/'replays'/'p2-20261111'
archive.mkdir(parents=True,exist_ok=True)
for directory in Path('/tmp').glob('p2-replay-*'):
 if not directory.is_dir():continue
 for name in ('SOLUSDT-source.sqlite','SUIUSDT-source.sqlite'):
  source=directory/name
  if not source.exists():continue
  dest=archive/name
  if not dest.exists():
   with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(dest)) as out:
    src.backup(out)
  print('REPLAY_ARCHIVE',str(dest),hashlib.sha256(dest.read_bytes()).hexdigest())
PY
ps -eo pid,comm,pcpu,pmem,etime,args --sort=-pcpu | head -n 10
free -m
df -h /
