"""Explicit real-market replay, separate databases; no execution calls."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from solbot.strategy.scheduler import Scheduler, StrategyStore

def replay_one(source: Path, symbol: str, temp: Path, end: int, attempt: int):
    store=StrategyStore(temp/f'{symbol}-{attempt}.sqlite')
    started=time.monotonic()
    try:
        report=Scheduler(source,store,symbol).replay(end-3*86400000+1,end)
        digest=hashlib.sha256()
        for row in store.connection.execute('SELECT payload_json FROM decisions ORDER BY id'):
            digest.update(row[0].encode())
        report['digest']=digest.hexdigest()
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        return report
    finally: store.close()

def main():
    with tempfile.TemporaryDirectory(prefix='p2-replay-') as temporary:
        for symbol,name in [('SOLUSDT','market.sqlite'),('SUIUSDT','sui-market.sqlite')]:
            source=Path('/var/lib/sol-bot/database')/name
            # Replay fixed snapshots, so concurrent official revisions cannot
            # change the input between deterministic runs.
            snapshot=Path(temporary)/f'{symbol}-source.sqlite'
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
                end=dst.execute("SELECT MAX(close_time) FROM market_klines WHERE interval='1m'").fetchone()[0]
            first=replay_one(snapshot,symbol,Path(temporary),end,1)
            second=replay_one(snapshot,symbol,Path(temporary),end,2)
            assert first['processed']>4000
            assert first['digest']==second['digest']
            assert first['counts']==second['counts']
            first['repeat_digest_equal']=True
            first['repeat_seconds']=second['elapsed_seconds']
            print(json.dumps(first,sort_keys=True),flush=True)

if __name__=='__main__':main()
