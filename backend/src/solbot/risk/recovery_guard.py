"""Recovery records intent; P4 cannot validate real exchange safety."""
import os
from solbot.accounts.allocation_ledger import now_ms

class RecoveryGuard:
    def __init__(self,ledger): self.ledger=ledger

    def restart(self,event_id,*,at=None):
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'RESTART',{},now_ms() if at is None else at): return
            db.execute("UPDATE account_allocations SET live_state=CASE WHEN live_state IN ('LIVE_ON','LIVE_PENDING') THEN 'PAUSED' ELSE live_state END,reconciled_at=0,equity_at=0,recovery_required=1")

    def request_on(self,event_id,*,actor,at=None):
        if not actor: raise ValueError('authenticated actor required')
        with self.ledger.transaction() as db:
            self.ledger.event(db,event_id,'LIVE_ON_REQUEST',{'actor':actor},now_ms() if at is None else at)
            locked=db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]
            db.execute("UPDATE account_allocations SET authorization_intent=1,live_state=CASE WHEN live_state='HARD_LOCKED' THEN live_state ELSE 'LIVE_OFF' END")
            return 'DENY_HARD_LOCKED' if locked else 'DENY_NOT_READY'

    def off(self,event_id,*,actor,at=None):
        with self.ledger.transaction() as db:
            self.ledger.event(db,event_id,'LIVE_OFF',{'actor':actor},now_ms() if at is None else at)
            db.execute("UPDATE account_allocations SET authorization_intent=0,live_state=CASE WHEN live_state='HARD_LOCKED' THEN live_state ELSE 'LIVE_OFF' END")

    def administrator_unlock(self,event_id,*,reason,user_authorization,at=None):
        """Only local root with explicit authorization reference. Never auto-called."""
        if not hasattr(os,'geteuid') or os.geteuid()!=0: raise PermissionError('OS root required')
        if not reason or not user_authorization: raise ValueError('explicit user authorization reference and reason required')
        at=now_ms() if at is None else at
        with self.ledger.transaction() as db:
            a=self.ledger.account(db); h=dict(db.execute('SELECT * FROM risk_high_watermarks').fetchone())
            if not 0<=at-a['equity_at']<=5000 or not 0<=at-a['reconciled_at']<=5000 or a['recovery_required']: raise ValueError('fresh verified risk state required')
            if not self.ledger.event(db,event_id,'ADMIN_UNLOCK',{'uid':os.geteuid(),'reason':reason,'user_authorization':user_authorization,'risk':h},at): return
            db.execute("UPDATE risk_hard_locks SET locked=0,reason='' ")
            db.execute("UPDATE account_allocations SET live_state='PAUSED',recovery_required=1")
            # Preserve historical high watermarks. Loss still >=5% relocks at next mark.

