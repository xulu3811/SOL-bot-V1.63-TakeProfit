"""Run on the authorized host after validation. Stop only strategy workers."""
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
import json,sqlite3,subprocess,shutil,time
from solbot.storage.microstructure import MicrostructureStore
from solbot.mathematics.contracts import SymbolRules
from solbot.execution.p3_shadow import P3ShadowExecutor

BASE=Path('/var/lib/sol-bot/database');source=BASE/'sol-shadow.sqlite'
archive=Path('/var/lib/sol-bot/backups')/('p3-migration-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
archive.mkdir(mode=0o750)
shutil.chown(archive,group='solbot')
def backup(path,target):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:src.backup(dst)
    with closing(sqlite3.connect(target)) as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
def content(path):
    with closing(sqlite3.connect(path)) as db:
        db.row_factory=sqlite3.Row
        return {t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in ('shadow_account','shadow_orders','shadow_positions')}
before=content(source)
print('BEFORE',json.dumps(before),flush=True)
with closing(sqlite3.connect((BASE/'sui-live-control.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
    live_before=db.execute('SELECT * FROM live_control').fetchall();assert live_before[0][3]=='LIVE_OFF'
backup(source,archive/'online.sqlite')
backup(archive/'online.sqlite',archive/'restore-check.sqlite')
assert content(archive/'online.sqlite')==content(archive/'restore-check.sqlite')
print('ONLINE_BACKUP_RESTORE_OK',str(archive),flush=True)
subprocess.run(['systemctl','stop','sol-shadow.service','sui-strategy.service'],check=True)
backup(source,archive/'final.sqlite');before=content(source)
for name in ('sol-shadow','sui-strategy'):shutil.copy2('/etc/systemd/system/'+name+'.service',archive/(name+'.service'))
try:
    backup(archive/'final.sqlite',archive/'final-restore-check.sqlite')
    assert content(archive/'final-restore-check.sqlite')==before
    features=MicrostructureStore(BASE/'sol-micro.sqlite','SOLUSDT',read_only=True).latest()
    assert features and features['synced']
    rules=SymbolRules.from_exchange_info(features['rules'],'SOLUSDT')
    ledger=P3ShadowExecutor(source,rules=rules)
    after=content(source)
    for table,rows in before.items():
        assert len(rows)==len(after[table])
        for old,new in zip(rows,after[table]):
            assert all(new[k]==v for k,v in old.items()),(table,old,new)
    # Compile-time configured services, no dynamic destinations.
    for name in ('sol-shadow','sui-strategy'):
        unit=Path('/opt/sol-bot/p3/deploy')/(name+'-p3.service')
        subprocess.run(['systemd-analyze','verify',str(unit)],check=True)
        shutil.copy2(unit,'/etc/systemd/system/'+name+'.service')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','start','sol-shadow.service','sui-strategy.service'],check=True)
    for _ in range(20):
        time.sleep(2)
        states=[json.loads((Path('/var/lib/sol-bot')/(n+'-status.json')).read_text()) for n in ('sol-shadow','sui-strategy')]
        if all(s.get('engine')=='P3' and s.get('status') in ('ready','protection_only') and time.time_ns()//1000000-s['updated_at']<10000 for s in states):break
    else:raise RuntimeError('P3 worker readiness verification failed')
    with closing(sqlite3.connect((BASE/'sui-live-control.sqlite').as_uri()+'?mode=ro',uri=True)) as db:assert db.execute('SELECT * FROM live_control').fetchall()==live_before
    print('MIGRATION_PASS',json.dumps({'archive':str(archive),'preserved_tables':{k:len(v) for k,v in before.items()},'cash':ledger.snapshot()['cash'],'risk_policy':ledger.risk_policy,'workers':states}),flush=True)
except BaseException:
    subprocess.run(['systemctl','stop','sol-shadow.service','sui-strategy.service'],check=True)
    with closing(sqlite3.connect(archive/'final.sqlite')) as src,closing(sqlite3.connect(source)) as dst:src.backup(dst)
    assert content(source)==before
    for name in ('sol-shadow','sui-strategy'):shutil.copy2(archive/(name+'.service'),'/etc/systemd/system/'+name+'.service')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('MIGRATION_FAILED_PREIMAGE_RESTORED_WORKERS_STOPPED',flush=True)
    raise
