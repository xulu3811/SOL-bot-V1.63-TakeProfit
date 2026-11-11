set -eu
cd /opt/sol-bot/p3
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
from contextlib import closing
import subprocess,json,sqlite3,time,hashlib,urllib.request
names=('solbot-market','solbot-sui-market','solbot-micro-sol','solbot-micro-sui','sol-shadow','sui-strategy')
def sample():
 out={'at_ms':time.time_ns()//1000000,'services':{},'workers':{},'db':{}}
 for name in names:
  lines=subprocess.check_output(['systemctl','show',name,'-p','MainPID','-p','ActiveState','-p','NRestarts','-p','CPUUsageNSec','-p','MemoryCurrent'],text=True).splitlines()
  out['services'][name]=dict(r.split('=',1) for r in lines)
 for name in ('sol-shadow','sui-strategy'):
  state=json.loads((Path('/var/lib/sol-bot')/(name+'-status.json')).read_text())
  assert state['engine']=='P3' and time.time_ns()//1000000-state['updated_at']<5000,state
  assert not state['live_authorized']
  out['workers'][name]=state
 base=Path('/var/lib/sol-bot/database')
 for file,table in [('sol-math-audit.sqlite','math_records'),('sui-math-audit.sqlite','math_records'),('sol-shadow.sqlite','shadow_math_audit')]:
  with closing(sqlite3.connect((base/file).as_uri()+'?mode=ro',uri=True)) as db:
   out['db'][file]={'rows':db.execute('SELECT count(*) FROM '+table).fetchone()[0],'bytes':(base/file).stat().st_size}
 with closing(sqlite3.connect((base/'sui-live-control.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
  out['live_state']=db.execute('SELECT state,revision FROM live_control').fetchone();assert out['live_state'][0]=='LIVE_OFF'
 for port in (8000,8001):
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as r:out[str(port)]=json.load(r)
  except Exception as exc:out[str(port)]={'unready':str(exc)}
 return out
a=sample();print('OBSERVATION_START',json.dumps(a),flush=True)
time.sleep(35)
b=sample()
for name in names:
 assert a['services'][name]['MainPID']==b['services'][name]['MainPID']
 assert a['services'][name]['NRestarts']==b['services'][name]['NRestarts']=='0'
 duration=(b['at_ms']-a['at_ms'])/1000
 b['services'][name]['cpu_percent_one_core']=round((int(b['services'][name]['CPUUsageNSec'])-int(a['services'][name]['CPUUsageNSec']))/1e9/duration*100,3)
assert b['db']['sol-shadow.sqlite']['rows']==a['db']['sol-shadow.sqlite']['rows']==0
print('OBSERVATION_END',json.dumps(b),flush=True)
root=Path.cwd().resolve()
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('backend','tests','deploy','scripts') for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
print('SOURCE_MANIFEST',json.dumps({'release':str(root),'files':manifest}),flush=True)
PY
systemctl is-active sol-bot-backup.timer sol-bot-health.timer
free -m
df -h /
