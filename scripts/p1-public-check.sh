set -eu
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
import asyncio, json, time
import httpx
from websockets.asyncio.client import connect
async def main():
 async with httpx.AsyncClient(base_url='https://data-api.binance.vision', timeout=15, trust_env=False) as client:
  for endpoint,params in [('time',{}),('exchangeInfo',{'symbol':'SOLUSDT'}),('ticker/bookTicker',{'symbol':'SOLUSDT'})]:
   r=await client.get('/api/v3/'+endpoint,params=params)
   print(endpoint,'HTTP',r.status_code)
   r.raise_for_status()
   data=r.json()
   if endpoint=='exchangeInfo':
    s=data['symbols'][0]
    print(json.dumps({k:s[k] for k in ['symbol','status','baseAsset','quoteAsset','isSpotTradingAllowed']}))
    print('rateLimits',json.dumps(data['rateLimits']))
   else: print(json.dumps(data))
 url='wss://data-stream.binance.vision:443/stream?streams='+ '/'.join('solusdt@kline_'+i for i in ['1m','5m','15m','1h','4h'])
 async with connect(url,proxy=None,ping_interval=20,ping_timeout=20,open_timeout=15,max_queue=16) as ws:
  seen=set()
  async with asyncio.timeout(30):
   while len(seen)<5:
    event=json.loads(await ws.recv())['data']
    if event.get('e')=='kline': seen.add(event['k']['i'])
  print('WS native intervals',sorted(seen))
asyncio.run(main())
PY
