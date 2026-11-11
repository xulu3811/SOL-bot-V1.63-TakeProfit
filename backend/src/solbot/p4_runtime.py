"""Local Guardian watchdog. No socket, credential loader or private API client."""
import argparse
import json
import time
import uuid
from pathlib import Path
from solbot.accounts.allocation_ledger import AllocationLedger, now_ms
from solbot.risk.recovery_guard import RecoveryGuard
from solbot.risk.drawdown_engine import DrawdownEngine

def tick(ledger):
    at=now_ms()
    with ledger.transaction() as db:
        a=ledger.account(db)
        if db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]:
            DrawdownEngine.lock(db,'persistent_hard_lock')
        elif a['live_state'] in ('LIVE_ON','LIVE_PENDING'):
            db.execute("UPDATE account_allocations SET live_state='PAUSED',recovery_required=1")
        for row in db.execute("SELECT intent_id FROM fund_reservations WHERE expires_at<=? AND status IN ('RESERVED','PARTIAL')",(at,)).fetchall():
            db.execute("UPDATE fund_reservations SET status='CANCEL_PENDING' WHERE intent_id=?",(row[0],))
            db.execute("INSERT OR IGNORE INTO pending_actions VALUES(?,'CANCEL_ENTRY',?,'PENDING')",('cancel:'+row[0],row[0]))
    return ledger.snapshot()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--status-path',required=True)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--live-control')
    args=parser.parse_args()
    ledger=AllocationLedger(args.database)
    RecoveryGuard(ledger).restart('boot:'+str(uuid.uuid4()))
    while True:
        status=tick(ledger)
        if args.live_control:
            from contextlib import closing
            import sqlite3
            with closing(sqlite3.connect(Path(args.live_control).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                legacy=dict(db.execute('SELECT * FROM live_control').fetchone())
            if legacy['state']=='HARD_LOCKED' and not status['locked']:
                with ledger.transaction() as db: DrawdownEngine.lock(db,'inherited_live_hard_lock')
                status=ledger.snapshot()
            elif legacy['state']!='LIVE_OFF':
                with ledger.transaction() as db:
                    db.execute("UPDATE account_allocations SET live_state=CASE WHEN live_state='HARD_LOCKED' THEN live_state ELSE 'PAUSED' END,recovery_required=1")
                status=ledger.snapshot()
            status['legacy_live_state']=legacy['state']
        status.update(version='guardian-p4-1',private_calls=0,real_orders=0,at=now_ms(),recovery='P5_PRIVATE_ADAPTER_MISSING')
        path=Path(args.status_path); temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(status,sort_keys=True),encoding='utf-8')
        temporary.replace(path)
        if args.once: break
        time.sleep(5)

if __name__=='__main__': main()

