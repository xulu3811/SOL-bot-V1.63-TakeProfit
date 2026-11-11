set -eu
/opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
import json,sqlite3,subprocess,time
base=Path('/var/lib/sol-bot')
def records():
 result={}
 for name in ('sol-shadow','sui-strategy'):
  path=base/'database'/(name+'-strategy.sqlite')
  with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
   result[name]={'ids':list(db.execute('SELECT signal_id,payload_json FROM decisions ORDER BY id')),
                 'checkpoint':db.execute('SELECT last_close_time FROM checkpoints').fetchone()[0]}
 return result
before=records()
solpid=subprocess.check_output(['systemctl','show','solbot-market.service','-p','MainPID','--value'],text=True).strip()
subprocess.run(['systemctl','restart','sol-shadow.service','sui-strategy.service'],check=True)
time.sleep(8)
after=records()
for name in before:
 assert set(before[name]['ids']).issubset(set(after[name]['ids']))
 assert len({r[0] for r in after[name]['ids']})==len(after[name]['ids'])
 status=json.loads((base/(name+'-status.json')).read_text())
 assert status['status']=='ready',status
 assert time.time_ns()//1000000-status['updated_at']<10000
 print('RESTART',json.dumps({'worker':name,'before_rows':len(before[name]['ids']),'after_rows':len(after[name]['ids']),
 'before_checkpoint':before[name]['checkpoint'],'after_checkpoint':after[name]['checkpoint'],'retained':True,'duplicates':0,'status':status['status']}))
assert subprocess.check_output(['systemctl','show','solbot-market.service','-p','MainPID','--value'],text=True).strip()==solpid=='261317'
print('P1_UNCHANGED_PID',solpid)
PY
systemctl start sol-bot-backup.service
systemctl start sol-bot-health.service
/opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
import json,sqlite3,tempfile
base=Path('/var/lib/sol-bot/backups')
for name in ('market.sqlite','sui-market.sqlite','sol-shadow-strategy.sqlite','sui-strategy-strategy.sqlite','sol-shadow.sqlite','sui-live-control.sqlite'):
 files=sorted(base.glob(name+'.*.bak'))
 assert files,name
 latest=files[-1]
 with tempfile.TemporaryDirectory(prefix='p2-restore-') as tmp:
  with closing(sqlite3.connect(latest.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(Path(tmp)/'restored.sqlite')) as dst:
   src.backup(dst)
   integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]
   assert integrity=='ok'
   tables=[r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
   if name=='sui-live-control.sqlite':assert dst.execute('SELECT state FROM live_control').fetchone()[0]=='LIVE_OFF'
   print('BACKUP_RESTORE',json.dumps({'file':latest.name,'bytes':latest.stat().st_size,'integrity':integrity,'tables':tables}))
PY
systemctl show sol-bot-backup.service sol-bot-health.service -p Id -p Result -p ExecMainStatus
systemctl is-active solbot-market.service solbot-sui-market.service sol-shadow.service sui-strategy.service sol-bot-backup.timer sol-bot-health.timer
ss -lntH '( sport = :8000 or sport = :8001 )' | awk '{print $4}'
