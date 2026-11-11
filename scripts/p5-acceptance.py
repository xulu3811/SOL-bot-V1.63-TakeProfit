from pathlib import Path
from contextlib import closing
import subprocess,json,sqlite3,time,hashlib,pwd
from solbot.execution.p5_store import OrderStore
from solbot.accounts.allocation_ledger import AllocationLedger
release=Path('/opt/sol-bot/p5').resolve()
ledger=AllocationLedger('/var/lib/sol-bot/database/sui-guardian-p4.sqlite')
before=ledger.snapshot()
names=['solbot-execution-p5','solbot-guardian-ipc','solbot-guardian','sol-shadow','sui-strategy','solbot-market','solbot-sui-market','solbot-micro-sol','solbot-micro-sui']
def service_state():return subprocess.check_output(['systemctl','show',*names,'-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts','-p','MemoryCurrent','-p','CPUUsageNSec'],text=True)
first=service_state();start=time.time_ns()
order=OrderStore('/var/lib/sol-bot-execution/orders.sqlite')
backup=Path('/var/lib/sol-bot/backups')/('p5-acceptance-'+str(start));backup.mkdir(mode=0o750)
__import__('shutil').chown(backup,group='solbot')
restored=order.backup(backup/'orders.sqlite')
wal=Path('/var/lib/sol-bot-execution/orders.sqlite-wal');wal_before=wal.stat().st_size if wal.exists() else 0
ipc_code="from solbot.risk.p5_ipc import GuardianIPC; import pwd; print(GuardianIPC('/run/solbot-guardian-p5/guardian.sock',expected_uid=pwd.getpwnam('solbot').pw_uid).health())"
def ipc(user):return subprocess.run(['sudo','-u',user,'env','PYTHONPATH='+str(release/'backend/src'),'/opt/sol-bot/venv/bin/python','-c',ipc_code],text=True,capture_output=True)
allowed=ipc('solbot-exec');denied=ipc('solbot')
assert allowed.returncode==0 and denied.returncode!=0
subprocess.run(['systemctl','restart','solbot-execution-p5'],check=True)
time.sleep(10)
after=ledger.snapshot();assert before==after
status=json.loads(Path('/var/lib/sol-bot-execution/status.json').read_text())
assert status['live_state']=='LIVE_OFF' and status['private_calls']==status['real_orders']==0
manifest=json.loads((release/'data/p5-source-manifest.json').read_text())
diff=[name for name,sha in manifest.items() if hashlib.sha256((release/name).read_bytes()).hexdigest()!=sha];assert not diff
second=service_state()
assert subprocess.check_output(['systemctl','is-active','solbot-execution-p5','solbot-guardian-ipc'],text=True).split()==['active','active']
result={'release':str(release),'guardian_unchanged':True,'guardian':after,'status':status,'order_restore':restored,'ipc_correct_uid':allowed.stdout.strip(),'ipc_wrong_uid_denied':denied.returncode!=0,'services_before':first,'services_after':second,'elapsed_seconds':(time.time_ns()-start)/1e9,'wal_before_bytes':wal_before,'wal_after_bytes':wal.stat().st_size if wal.exists() else 0,'source_differences':diff,'source_files':len(manifest)}
(release/'data/p5-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
