"""No exchange transport: snapshots must originate from the future trusted adapter."""
from .allocation_ledger import D, encoded, now_ms

class BalanceReconciliation:
    def __init__(self,ledger): self.ledger=ledger

    def reconcile(self,event_id,balances,observed_at,*,account_id='SUI-live',unknown_orders=False,at=None):
        at=now_ms() if at is None else at
        if account_id!=self.ledger.account_id: raise ValueError('wrong account snapshot')
        with self.ledger.transaction() as db:
            payload={'balances':balances,'observed_at':observed_at,'unknown_orders':unknown_orders,'account_id':account_id}
            if not self.ledger.event(db,event_id,'RECONCILE',payload,at):
                return db.execute('SELECT status FROM balance_reconciliation_events WHERE event_id=?',(event_id,)).fetchone()[0]
            a=self.ledger.account(db)
            valid=type(observed_at) is int and 0<=at-observed_at<=5000 and unknown_orders is False
            if db.execute("SELECT 1 FROM fund_reservations WHERE status IN ('UNKNOWN','CANCEL_PENDING')").fetchone(): valid=False
            if db.execute("SELECT 1 FROM bnb_refill_intents WHERE status='UNKNOWN'").fetchone(): valid=False
            expected={'USDT':D(a['cash'])}
            for asset,category,quantity in db.execute('SELECT * FROM asset_ownership'):
                expected[asset]=expected.get(asset,D('0'))+D(quantity)
                if category=='UNRECONCILED' and D(quantity)>0: valid=False
            for asset in set(expected)|set(balances):
                row=balances.get(asset,{'free':'0','locked':'0'})
                free,locked=D(row['free'],nonnegative=True),D(row['locked'],nonnegative=True)
                total=free+locked
                db.execute('INSERT INTO exchange_balances VALUES(?,?,?,?) ON CONFLICT(asset) DO UPDATE SET free=excluded.free,locked=excluded.locked,observed_at=excluded.observed_at',(asset,str(free),str(locked),observed_at))
                old=expected.get(asset,D('0'))
                if total<old: valid=False
                if total>old:
                    # Unknown excess is manual, never an automatic robot allocation.
                    manual=self.ledger.owned(db,asset,'MANUAL_UNMANAGED')
                    self.ledger.set_owned(db,asset,'MANUAL_UNMANAGED',manual+total-old)
            status='RECONCILED' if valid else 'UNRECONCILED'
            db.execute('INSERT INTO balance_reconciliation_events VALUES(?,?,?,?)',(event_id,encoded(payload),status,at))
            db.execute('UPDATE account_allocations SET reconciled_at=?,recovery_required=?',(observed_at if valid else 0,0 if valid else 1))
            if not valid:
                db.execute("UPDATE account_allocations SET live_state=CASE WHEN live_state IN ('LIVE_OFF','HARD_LOCKED') THEN live_state ELSE 'PAUSED' END")
            return status

