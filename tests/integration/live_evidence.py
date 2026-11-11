"""Explicit, bounded public-data acceptance checks. Never used by offline discovery."""
import asyncio
from contextlib import closing
from decimal import Decimal
import json
import sqlite3
import time

import httpx
from websockets.asyncio.client import connect

DB = '/var/lib/sol-bot/database/market.sqlite'

async def main():
    async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
        for path in ['/health','/api/v1/market/status','/api/v1/market/klines?limit=2','/api/v1/market/latest','/api/v1/market/gaps', '/api/v1/market/klines?limit=1001']:
            response = await http.get('http://127.0.0.1:8000'+path)
            print(json.dumps({'endpoint':path,'http_status':response.status_code}))
            assert response.status_code == (422 if '1001' in path else 200)
        # One clean connection is deliberately closed after a closed 1m event.
        url = 'wss://data-stream.binance.vision:443/stream?streams='+'/'.join('solusdt@kline_'+i for i in ['1m','5m','15m','1h','4h'])
        seen=set()
        async with connect(url, proxy=None, max_queue=16, open_timeout=15) as ws:
            async with asyncio.timeout(85):
                while True:
                    payload=json.loads(await ws.recv())['data']
                    if payload.get('e') != 'kline': continue
                    candle=payload['k'];seen.add(candle['i'])
                    if candle['i']=='1m' and candle['x'] and len(seen)==5: break
        await asyncio.sleep(3)
        response=await http.get('https://data-api.binance.vision/api/v3/klines',params={'symbol':'SOLUSDT','interval':'1m','startTime':candle['t'],'limit':1})
        response.raise_for_status()
        rest=response.json()[0]
        for ws_key,index in [('o',1),('h',2),('l',3),('c',4),('v',5),('q',7),('V',9),('Q',10)]:
            assert Decimal(candle[ws_key])==Decimal(rest[index]), ws_key
        assert candle['n']==rest[8]
        with closing(sqlite3.connect('file:'+DB+'?mode=ro',uri=True)) as db:
            stored=db.execute('SELECT close,volume,trade_count FROM market_klines WHERE interval=? AND open_time=?',('1m',candle['t'])).fetchone()
            assert stored and Decimal(stored[0])==Decimal(rest[4]) and Decimal(stored[1])==Decimal(rest[5]) and stored[2]==rest[8]
            print(json.dumps({'integrity':db.execute('PRAGMA integrity_check').fetchone()[0], 'journal_mode':db.execute('PRAGMA journal_mode').fetchone()[0], 'duplicates':db.execute('SELECT count(*) FROM (SELECT 1 FROM market_klines GROUP BY symbol,interval,open_time HAVING count(*)>1)').fetchone()[0]}))
        print(json.dumps({'five_native_streams':sorted(seen),'closed_1m_rest_ws_db_equal':True,'closed_open_ms':candle['t'],'test_connection_closed':True}))

if __name__=='__main__': asyncio.run(main())
