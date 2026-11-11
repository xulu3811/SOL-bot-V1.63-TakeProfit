set -eu
date -u +%FT%TZ
systemctl show solbot-market.service solbot-sui-market.service sol-shadow.service sui-strategy.service -p Id -p MainPID -p ActiveState -p NRestarts
systemctl is-active sol-bot-backup.timer sol-bot-health.timer
readlink -f /opt/sol-bot/p2
/opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
import json,sqlite3
base=Path('/var/lib/sol-bot/database')
for name,table in [('sol-shadow.sqlite','shadow_account'),('sui-live-control.sqlite','live_control')]:
 with closing(sqlite3.connect((base/name).as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row
  print(name,json.dumps(dict(db.execute('SELECT * FROM '+table).fetchone())))
  if table=='shadow_account':
   print('open_positions',db.execute("SELECT count(*) FROM shadow_positions WHERE status='OPEN'").fetchone()[0])
   print('pending_orders',db.execute("SELECT count(*) FROM shadow_orders WHERE status='PENDING'").fetchone()[0])
  else:assert db.execute('SELECT state FROM live_control').fetchone()[0]=='LIVE_OFF'
PY
free -m
df -h /
