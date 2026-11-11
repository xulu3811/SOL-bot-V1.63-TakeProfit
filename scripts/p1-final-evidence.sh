set -eu
sudo -u solbot env PYTHONPATH=/opt/sol-bot/backend/src /opt/sol-bot/venv/bin/python - <<'PY'
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
import json,sqlite3,time,urllib.request
from solbot.storage.models import Kline, INTERVAL_MS
path=Path('/var/lib/sol-bot/database/market.sqlite')
with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/market/status',timeout=10) as r: state=json.load(r)
state['health']['collector'].pop('symbol_info',None)
state['sample_utc']=datetime.now(timezone.utc).isoformat()
with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
 db.row_factory=sqlite3.Row
 invalid=0
 for row in db.execute('SELECT * FROM market_klines'):
  data=dict(row);data['is_closed']=bool(data['is_closed'])
  try: Kline(**data).validate()
  except ValueError: invalid+=1
 state['quality']={'invalid_rows':invalid,'open_rows':db.execute('SELECT count(*) FROM market_klines WHERE is_closed<>1').fetchone()[0],
 'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
 'duplicates':db.execute('SELECT count(*) FROM (SELECT 1 FROM market_klines GROUP BY symbol,interval,open_time HAVING count(*)>1)').fetchone()[0]}
 first=db.execute('SELECT payload FROM system_health_snapshots ORDER BY id LIMIT 1').fetchone()
 state['initial_backfill_duration_ms']=json.loads(first[0]).get('last_backfill_duration_ms') if first else None
state['file_bytes']={p.name:p.stat().st_size for p in (path,Path(str(path)+'-wal')) if p.exists()}
print(json.dumps(state,indent=2))
PY
systemctl show solbot-market.service -p ActiveState -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p NRestarts
systemctl is-active ssh chrony sol-bot-backup.timer sol-bot-health.timer solbot-market.service
swapon --show --bytes
sysctl vm.swappiness
free -m
df -h /
ss -lntH '( sport = :8000 )'
