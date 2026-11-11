set -eu
/opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
import json, sqlite3, subprocess, time, urllib.request, urllib.error
from pathlib import Path
path=Path('/var/lib/sol-bot/database/market.sqlite')
with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
 before=set(db.execute('SELECT interval,open_time FROM market_klines'))
start=time.monotonic()
subprocess.run(['systemctl','restart','solbot-market.service'],check=True)
for attempt in range(60):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as r:
   state=json.load(r)
   if state['ready']: break
 except (urllib.error.URLError,TimeoutError): pass
 time.sleep(2)
else: raise SystemExit('Service failed to regain readiness')
with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
 after=set(db.execute('SELECT interval,open_time FROM market_klines'))
 duplicates=db.execute('SELECT count(*) FROM (SELECT 1 FROM market_klines GROUP BY symbol,interval,open_time HAVING count(*)>1)').fetchone()[0]
assert before.issubset(after) and duplicates==0
print(json.dumps({'restart_retained_keys':len(before),'after_keys':len(after),'lost_keys':len(before-after),'duplicates':duplicates,'ready_after_seconds':round(time.monotonic()-start,2)}))
PY
systemctl start sol-bot-backup.service
systemctl start sol-bot-health.service
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
import json,sqlite3,tempfile
files=sorted(Path('/var/lib/sol-bot/backups').glob('market.sqlite.*.bak'))
assert files
latest=files[-1]
with tempfile.TemporaryDirectory(prefix='solbot-restore-') as temp:
 with closing(sqlite3.connect(latest.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(Path(temp)/'restored.sqlite')) as dst:
  src.backup(dst)
  integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]
  count=dst.execute('SELECT count(*) FROM market_klines').fetchone()[0]
assert integrity=='ok' and count>55000
print(json.dumps({'inherited_backup_file':latest.name,'mode':oct(latest.stat().st_mode & 0o777),'bytes':latest.stat().st_size,'restored_rows':count,'integrity':integrity}))
PY
systemctl is-active ssh chrony sol-bot-backup.timer sol-bot-health.timer solbot-market.service
systemctl show sol-bot-backup.service sol-bot-health.service -p Result -p ExecMainStatus
ss -lntH '( sport = :8000 )' | awk '{print $4}'
free -m
df -h /
