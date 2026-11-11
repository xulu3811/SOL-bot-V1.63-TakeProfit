"""Bounded local-only burst/steady load and SQLite restore test.

Run explicitly: PYTHONPATH=backend/src python tests/integration/resource_probe.py.
No network traffic. All databases are under an auto-cleaned temporary directory.
"""
import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import resource
import sqlite3
import tempfile
import time

from solbot.storage.database import Database
from solbot.storage.models import Kline, INTERVAL_MS

async def main():
    with tempfile.TemporaryDirectory(prefix='solbot-p1-load-') as temp:
        path = Path(temp) / 'synthetic.sqlite'
        db = Database(path)
        await db.start()
        now = int(time.time() * 1000)
        batches = []
        for interval, step in INTERVAL_MS.items():
            end = now // step * step - step
            rows = []
            for index in range(1000):
                start = end - index * step
                rows.append(Kline.from_rest([start,'100','102','98','101','10',start+step-1,'1000',10,'5','500','0'], interval, now))
            batches.append(rows)
        cpu_start = time.process_time()
        start = time.monotonic()
        latency = []
        for _ in range(20):
            for rows in batches:
                before = time.perf_counter()
                await db.write_klines(rows)
                latency.append((time.perf_counter()-before)*1000)
        burst = time.monotonic()-start
        # A bounded steady phase includes duplicate delivery and readonly queries.
        for _ in range(300):
            await db.write_klines(batches[0][-10:])
            db.latest('1m')
            await asyncio.sleep(0.1)
        backup = Path(temp) / 'restore.sqlite'
        await db.backup(backup)
        with closing(sqlite3.connect(backup)) as conn:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            count = conn.execute('SELECT count(*) FROM market_klines').fetchone()[0]
        assert integrity == 'ok' and count == 5000
        elapsed = time.monotonic()-start
        result = {'burst_attempted_rows':100000,'unique_rows':count,'burst_seconds':round(burst,3),
                  'elapsed_seconds':round(elapsed,3),'cpu_seconds':round(time.process_time()-cpu_start,3),
                  'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'batch_latency_p95_ms':round(sorted(latency)[int(len(latency)*.95)],3),
                  'database_bytes':path.stat().st_size,'wal_bytes':Path(str(path)+'-wal').stat().st_size,
                  'restore_integrity':integrity}
        await db.close()
        reopened = Database(path)
        await reopened.start()
        assert len(reopened.klines('1m',1000)) == 1000
        await reopened.close()
        result['reopen_preserved_rows'] = True
        print(json.dumps(result,indent=2))

if __name__ == '__main__':
    asyncio.run(main())
