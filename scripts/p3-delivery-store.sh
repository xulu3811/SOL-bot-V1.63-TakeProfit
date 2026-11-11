set -eu
install -d -o root -g solbot -m750 /opt/sol-bot/deliveries
install -o root -g solbot -m640 /tmp/solbot-p1-package.tar.gz /opt/sol-bot/deliveries/p3-final-delivery-20261111.tar.gz
sha256sum /opt/sol-bot/deliveries/p3-final-delivery-20261111.tar.gz
readlink -f /opt/sol-bot/p3
systemctl show sol-shadow.service sui-strategy.service -p Id -p ActiveState -p MainPID -p NRestarts
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
from contextlib import closing
import sqlite3,json,time
base=Path('/var/lib/sol-bot')
for name in ('sol-shadow','sui-strategy'):
 s=json.loads((base/(name+'-status.json')).read_text());assert s['engine']=='P3' and time.time_ns()//1000000-s['updated_at']<5000
 print('FINAL_WORKER',name,s['status'],s.get('micro_ready'),s['live_authorized'])
with closing(sqlite3.connect((base/'database/sui-live-control.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
 print('LIVE',db.execute('SELECT state,revision FROM live_control').fetchone());assert db.execute('SELECT state FROM live_control').fetchone()[0]=='LIVE_OFF'
with closing(sqlite3.connect((base/'database/sol-shadow.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
 print('ACCOUNT',db.execute('SELECT cash,reserved,hard_locked FROM shadow_account').fetchone())
 print('ORDERS_FILLS',db.execute('SELECT count(*) FROM shadow_orders').fetchone()[0],db.execute('SELECT count(*) FROM shadow_fills').fetchone()[0])
PY
