"""Authorized local-host acceptance: consistent backup, restart, hashes and status."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import time
from solbot.accounts.allocation_ledger import AllocationLedger

release=Path('/opt/sol-bot/p4').resolve()
ledger=AllocationLedger('/var/lib/sol-bot/database/sui-guardian-p4.sqlite')
def live():
    with closing(sqlite3.connect('file:/var/lib/sol-bot/database/sui-live-control.sqlite?mode=ro',uri=True)) as db:
        return db.execute('SELECT * FROM live_control').fetchall()
def services():
    return subprocess.check_output(['systemctl','show','solbot-guardian','sol-shadow','sui-strategy','solbot-market','solbot-sui-market','solbot-micro-sol','solbot-micro-sui','-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts','-p','MemoryCurrent'],text=True)
before=ledger.snapshot(); legacy=live(); service_before=services()
backup=Path('/var/lib/sol-bot/backups')/('p4-acceptance-'+str(time.time_ns()))
backup.mkdir(mode=0o750)
__import__('shutil').chown(backup,group='solbot')
restore=ledger.backup_restore(backup/'guardian.sqlite')
subprocess.run(['systemctl','restart','solbot-guardian'],check=True)
time.sleep(7)
after=ledger.snapshot(); legacy_after=live()
economic=('allocated','cash','high','equity','units','nav_high','locked','positions','reservations','pending_actions')
assert all(before[k]==after[k] for k in economic)
assert legacy==legacy_after and legacy_after[0][3]=='LIVE_OFF'
status=json.loads(Path('/var/lib/sol-bot/guardian-status.json').read_text())
assert status['live_state']=='LIVE_OFF' and status['recovery_required']==1
assert status['private_calls']==status['real_orders']==0 and not status['live_ready']
assert subprocess.check_output(['systemctl','is-active','solbot-guardian'],text=True).strip()=='active'
manifest=json.loads((release/'data/p4-source-manifest.json').read_text())
differences=[name for name,value in manifest.items() if hashlib.sha256((release/name).read_bytes()).hexdigest()!=value]
assert not differences
result={'release':str(release),'backup':restore,'before':before,'after':after,'legacy_before':legacy,'legacy_after':legacy_after,'status':status,'services_before':service_before,'services_after':services(),'manifest_files':len(manifest),'manifest_differences':differences,'economic_restart_equal':True,'test_evidence_count':len(json.loads((release/'data/p4-test-evidence.json').read_text()))}
(release/'data/p4-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,sort_keys=True))
