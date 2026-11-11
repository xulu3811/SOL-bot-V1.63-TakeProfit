"""Add P4 sidecar after consistent backup/restore; P3 files retain their schemas."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.risk.drawdown_engine import DrawdownEngine

base=Path('/var/lib/sol-bot/database')
backup=Path(sys.argv[1]); backup.mkdir(parents=True,exist_ok=False)
results=[]
for name in ('sui-live-control.sqlite','sol-shadow.sqlite'):
    source=base/name
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(backup/name)) as dst: src.backup(dst)
    with closing(sqlite3.connect(backup/name)) as src, closing(sqlite3.connect(backup/(name+'.restored'))) as dst:
        src.backup(dst)
        hashes=[hashlib.sha256('\n'.join(c.iterdump()).encode()).hexdigest() for c in (src,dst)]
        assert hashes[0]==hashes[1] and dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        results.append({'database':name,'logical_sha256':hashes[0],'restore':'ok'})
with closing(sqlite3.connect((base/'sui-live-control.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
    legacy=dict(zip([x[0] for x in db.execute('SELECT * FROM live_control').description],db.execute('SELECT * FROM live_control').fetchone()))
    assert legacy['account_id']=='SUI-live' and legacy['symbol']=='SUIUSDT'
    assert legacy['state'] in ('LIVE_OFF','HARD_LOCKED'), 'unexpected live state; inspect before migration'
path=base/'sui-guardian-p4.sqlite'
if path.exists(): raise RuntimeError('existing P4 database requires separate controlled upgrade')
ledger=AllocationLedger(path)
with ledger.transaction() as db:
    ledger.event(db,'p3-import','P3_CONTROL_IMPORT',legacy,__import__('time').time_ns()//1000000)
    if legacy['state']=='HARD_LOCKED': DrawdownEngine.lock(db,'inherited_p3_live_hard_lock')
    # 2000 is a plan, not a verified exchange balance. Recovery stays required.
results.append(ledger.backup_restore(backup/'p4-initial.sqlite'))
for kind in ('LIVE','SHADOW_P3'):
    from solbot.execution._storage import verify_existing_store
    try: verify_existing_store(path,kind,'SUI-live','SUIUSDT')
    except ValueError: results.append({'old_writer':kind,'result':'DENIED'})
    else: raise AssertionError('old schema writer accepted P4')
print(json.dumps({'backups':results,'legacy':legacy,'guardian':ledger.snapshot()},sort_keys=True))

