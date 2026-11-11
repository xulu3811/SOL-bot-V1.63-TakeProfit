"""Production restart/readback and isolated P3 SQLite restore verification."""
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
import json,sqlite3,subprocess,time,sys,hashlib,zlib,shutil
from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.mathematics.contracts import SymbolRules
from solbot.storage.microstructure import MicrostructureStore

base=Path('/var/lib/sol-bot');data=base/'database'
archive=base/'backups'/('p3-acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
archive.mkdir(mode=0o750)
shutil.chown(archive,group='solbot')
def read(name,sql):
    with closing(sqlite3.connect((data/name).as_uri()+'?mode=ro',uri=True)) as db:return db.execute(sql).fetchall()
def backup(source,dest):
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(dest)) as dst:src.backup(dst)
    with closing(sqlite3.connect(dest)) as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
def logical_hash(path):
    digest=hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        for line in db.iterdump():digest.update(line.encode())
    return digest.hexdigest()
def pid(name):return subprocess.check_output(['systemctl','show',name,'-p','MainPID','--value'],text=True).strip()
market_pids={n:pid(n) for n in ('solbot-market','solbot-sui-market','solbot-micro-sol','solbot-micro-sui')}
before={n:read(n,'SELECT signal_id,payload_json FROM decisions ORDER BY id') for n in ('sol-shadow-strategy.sqlite','sui-strategy-strategy.sqlite')}
live=read('sui-live-control.sqlite','SELECT * FROM live_control')
fills=read('sol-shadow.sqlite','SELECT * FROM shadow_fills')
lock=read('sol-shadow.sqlite','SELECT hard_locked FROM shadow_account')
balances=read('sol-shadow.sqlite','SELECT cash,reserved,hard_locked FROM shadow_account')
orders=read('sol-shadow.sqlite','SELECT * FROM shadow_orders')
positions=read('sol-shadow.sqlite','SELECT * FROM shadow_positions')
subprocess.run(['systemctl','restart','sol-shadow.service','sui-strategy.service'],check=True)
for _ in range(20):
    time.sleep(2)
    states={n:json.loads((base/(n+'-status.json')).read_text()) for n in ('sol-shadow','sui-strategy')}
    if all(s.get('engine')=='P3' and s.get('status') in ('ready','protection_only') and time.time_ns()//1000000-s['updated_at']<5000 for s in states.values()):break
else:raise AssertionError(states)
for n,rows in before.items():
    after=read(n,'SELECT signal_id,payload_json FROM decisions ORDER BY id')
    assert set(rows)<=set(after) and len({r[0] for r in after})==len(after)
assert read('sui-live-control.sqlite','SELECT * FROM live_control')==live and live[0][3]=='LIVE_OFF'
assert read('sol-shadow.sqlite','SELECT * FROM shadow_fills')==fills
assert read('sol-shadow.sqlite','SELECT hard_locked FROM shadow_account')==lock
assert read('sol-shadow.sqlite','SELECT cash,reserved,hard_locked FROM shadow_account')==balances
assert read('sol-shadow.sqlite','SELECT * FROM shadow_orders')==orders
assert read('sol-shadow.sqlite','SELECT * FROM shadow_positions')==positions
assert {n:pid(n) for n in market_pids}==market_pids
print('RESTART_PASS',json.dumps({'market_pids_unchanged':market_pids,'workers':states,'live_control_unchanged':True,'fills_unchanged':len(fills)}),flush=True)
for name in ('sol-shadow.sqlite','sol-shadow-strategy.sqlite','sui-strategy-strategy.sqlite','sol-math-audit.sqlite','sui-math-audit.sqlite','sui-live-control.sqlite'):
    target=archive/name;backup(data/name,target);backup(target,archive/('restored-'+name))
    assert logical_hash(target)==logical_hash(archive/('restored-'+name))
    print('BACKUP_RESTORE_PASS',name,target.stat().st_size,hashlib.sha256(target.read_bytes()).hexdigest(),flush=True)
rules=SymbolRules.from_exchange_info(MicrostructureStore(data/'sol-micro.sqlite','SOLUSDT',read_only=True).latest()['rules'],'SOLUSDT')
restored=P3ShadowExecutor(archive/'restored-sol-shadow.sqlite',rules=rules)
assert restored.snapshot()['hard_locked']==lock[0][0]
print('RESTORED_P3_OWNER_PASS',json.dumps(restored.snapshot()),flush=True)
# Explicit synthetic fixtures only in isolated acceptance files, never production.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests/unit'))
from test_p3_apts_shadow import RULES,NOW,features,signal,NORMAL
scenario=archive/'isolated-partial.sqlite';engine=P3ShadowExecutor(scenario,rules=RULES)
engine.submit_candidate(signal(),notional='300',now_ms=NOW)
quote=features(1,bid='99.8',ask='99.9',quantity='10')
engine.process_market(quote,NORMAL,now_ms=NOW+1)
assert engine.snapshot()['fill_count']==1
backup(scenario,archive/'isolated-restored.sqlite')
copy=P3ShadowExecutor(archive/'isolated-restored.sqlite',rules=RULES)
before_copy=copy.snapshot();copy.process_market(quote,NORMAL,now_ms=NOW+1)
assert copy.snapshot()==before_copy
with closing(sqlite3.connect(archive/'isolated-restored.sqlite')) as db:
    db.execute("UPDATE shadow_account SET hard_locked=1,lock_reason='isolated_acceptance_fixture'");db.commit()
copy=P3ShadowExecutor(archive/'isolated-restored.sqlite',rules=RULES)
assert copy.snapshot()['hard_locked']==1
copy.process_market(features(2,bid='99.8',ask='99.9'),NORMAL,now_ms=NOW+2)
assert copy.snapshot()['positions'] and not [o for o in copy.snapshot()['pending_orders'] if o['side']=='BUY']
print('ISOLATED_RESTORE_NO_DUPLICATE_AND_LOCK_PASS',json.dumps({'fixture':'synthetic_not_production','fill_count':copy.snapshot()['fill_count'],'hard_locked':copy.snapshot()['hard_locked']}),flush=True)
for name in ('sol-math-audit.sqlite','sui-math-audit.sqlite'):
    with closing(sqlite3.connect((data/name).as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT signal_id,input_hash,compressed FROM math_records ORDER BY created_at DESC LIMIT 1').fetchone()
        print('MATH_AUDIT',json.dumps({'database':name,'count':db.execute('SELECT count(*) FROM math_records').fetchone()[0],
            'latest':json.loads(zlib.decompress(row[2])) if row else None}),flush=True)
print('ACCEPTANCE_ARCHIVE',str(archive),flush=True)
