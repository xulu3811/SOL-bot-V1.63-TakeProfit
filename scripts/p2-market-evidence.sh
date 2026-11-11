set -eu
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
import json,urllib.request,urllib.error
for port in (8000,8001):
 with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/market/status',timeout=10) as r:
  data=json.load(r);h=data['health'];c=h['collector']
  print(json.dumps({'port':port,'symbol':data['symbol'],'ready':h['ready'],'reasons':h['reasons'],'storage':data['storage'],'errors':c['errors'],'streams':c['last_by_interval'],'backfill_ms':c['last_backfill_duration_ms']}))
PY
systemctl show solbot-market.service solbot-sui-market.service -p MainPID -p MemoryCurrent -p NRestarts -p ActiveEnterTimestamp
free -m
