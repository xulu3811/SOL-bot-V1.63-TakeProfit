set -eu
date -u +%FT%TZ
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
from contextlib import closing
import hashlib,importlib.metadata,json,sqlite3,sys,urllib.request
root=Path('/opt/sol-bot/p2').resolve()
items={}
for folder in ('backend','tests','deploy','android'):
 for p in sorted((root/folder).rglob('*')):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.sqlite','.db'):
   items[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
digest=hashlib.sha256(json.dumps(items,sort_keys=True,separators=(',',':')).encode()).hexdigest()
print('RELEASE',json.dumps({'path':str(root),'source_manifest_sha256':digest,'files':len(items),'python':sys.version,
 'dependencies':{k:importlib.metadata.version(k) for k in ('fastapi','httpx','websockets','uvicorn')}}))
for port in (8000,8001):
 with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/market/status',timeout=5) as response:
  data=json.load(response)
  assert data['health']['ready']
  print('FINAL_MARKET',json.dumps({'symbol':data['symbol'],'ready':True,'intervals':data['storage']['intervals']}))
archive=Path('/var/lib/sol-bot/replays/p2-20261111')
archive.chmod(0o750)
for symbol in ('SOLUSDT','SUIUSDT'):
 p=archive/(symbol+'-source.sqlite')
 assert p.is_file()
 p.chmod(0o640)
 with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
  end=db.execute("SELECT MAX(close_time) FROM market_klines WHERE interval='1m'").fetchone()[0]
  integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
  assert integrity=='ok'
 print('ARCHIVE',json.dumps({'symbol':symbol,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'last_1m_close':end,'integrity':integrity}))
PY
systemctl show solbot-market.service solbot-sui-market.service sol-shadow.service sui-strategy.service -p Id -p MainPID -p NRestarts -p MemoryCurrent -p ActiveState
systemctl is-active sol-bot-backup.timer sol-bot-health.timer
free -m
df -h /
