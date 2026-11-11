set -eu
date -u +%FT%TZ
systemctl show solbot-market.service -p MainPID -p ActiveEnterTimestamp -p ActiveState -p MemoryCurrent
systemctl is-active sol-bot-backup.timer sol-bot-health.timer ssh
readlink -f /opt/sol-bot/backend
free -m
df -h /
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
import json,urllib.request,httpx,asyncio
with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as response:
 data=json.load(response); print('SOL_READY',data['ready'])
async def main():
 async with httpx.AsyncClient(base_url='https://data-api.binance.vision',trust_env=False,timeout=15) as http:
  response=await http.get('/api/v3/exchangeInfo',params={'symbol':'SUIUSDT','showPermissionSets':'false'})
  print('SUI_EXCHANGE_INFO_HTTP',response.status_code);response.raise_for_status()
  symbol=response.json()['symbols'][0]
  print(json.dumps({k:symbol[k] for k in ['symbol','status','baseAsset','quoteAsset','isSpotTradingAllowed']}))
  if symbol['symbol']!='SUIUSDT' or symbol['status']!='TRADING' or not symbol['isSpotTradingAllowed']: raise SystemExit('SUI blocked')
asyncio.run(main())
PY
