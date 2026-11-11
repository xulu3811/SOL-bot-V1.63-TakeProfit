set -eu
systemctl show solbot-market.service -p ActiveState -p MainPID -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p NRestarts
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
import json,sqlite3,urllib.request,urllib.error
try:
 r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/market/status',timeout=10)
 data=json.load(r)
 data['health']['collector'].pop('symbol_info',None)
 print(json.dumps(data,indent=2))
except urllib.error.HTTPError as e: print(e.code,e.read().decode())
PY
