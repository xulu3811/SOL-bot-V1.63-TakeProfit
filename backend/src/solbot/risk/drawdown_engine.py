"""Unitized NAV preserves drawdown across explicitly recorded external flows."""
from solbot.mathematics.decimal_utils import D
from . import risk_policy as policy
from solbot.accounts.allocation_ledger import now_ms

class DrawdownEngine:
    def __init__(self,ledger): self.ledger=ledger

    @staticmethod
    def lock(db,reason):
        db.execute('UPDATE risk_hard_locks SET locked=1,reason=?',(reason,))
        db.execute("UPDATE account_allocations SET live_state='HARD_LOCKED'")
        for r in db.execute("SELECT intent_id FROM fund_reservations WHERE status IN ('RESERVED','PARTIAL','UNKNOWN','CANCEL_PENDING')").fetchall():
            db.execute("UPDATE fund_reservations SET status='CANCEL_PENDING' WHERE intent_id=?",(r[0],))
            db.execute("INSERT OR IGNORE INTO pending_actions VALUES(?,'CANCEL_ENTRY',?,'PENDING')",('cancel:'+r[0],r[0]))
        # Neither remaining reservations nor protection are deleted before ACK.

    def mark(self,event_id,prices,observed_at,*,at=None):
        at=now_ms() if at is None else at
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'MARK',{'prices':prices,'observed_at':observed_at},at): return
            account=self.ledger.account(db)
            rows=db.execute("SELECT asset,quantity FROM asset_ownership WHERE category='ROBOT_OWNED'").fetchall()
            valid=type(observed_at) is int and 0<=at-observed_at<=5000
            equity=D(account['cash'])
            for asset,quantity in rows:
                if D(quantity)>0:
                    if asset not in prices: valid=False
                    else: equity+=D(quantity)*D(prices[asset],positive=True)
            if not valid:
                db.execute('UPDATE account_allocations SET equity_at=0,recovery_required=1')
                if not db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]:
                    db.execute("UPDATE account_allocations SET live_state=CASE WHEN live_state='LIVE_OFF' THEN live_state ELSE 'PAUSED' END")
                return
            row=db.execute('SELECT * FROM risk_high_watermarks').fetchone()
            units=D(row['units'],positive=True)
            nav=equity/units
            nav_high=max(D(row['nav_high']),nav)
            high=nav_high*units
            db.execute('UPDATE risk_high_watermarks SET high=?,equity=?,nav_high=?',(str(high),str(equity),str(nav_high)))
            db.execute('UPDATE account_allocations SET equity_at=?',(observed_at,))
            if equity<=high*(1-policy.MAX_DRAWDOWN): self.lock(db,'maximum_drawdown_5_percent')

    def external_flow(self,event_id,amount,*,actor,reason,at=None):
        """Trusted administrator boundary; funded adjustment, not account discovery."""
        at=now_ms() if at is None else at
        flow=D(amount)
        if not actor or not reason or flow==0: raise ValueError('documented nonzero flow required')
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'FLOW',{'amount':flow,'actor':actor,'reason':reason},at): return
            a=self.ledger.account(db); h=db.execute('SELECT * FROM risk_high_watermarks').fetchone()
            if not 0<=at-a['equity_at']<=5000 or D(h['equity'])<=0: raise ValueError('fresh pre-flow equity required')
            reserved=sum((D(r[0])+D(r[1]) for r in db.execute('SELECT remaining,fees FROM fund_reservations')),D('0'))
            reserved+=sum((D(r[0]) for r in db.execute("SELECT reserve FROM bnb_refill_intents WHERE status IN ('CANDIDATE','UNKNOWN')")),D('0'))
            cash=D(a['cash'])+flow
            if cash<reserved or D(h['equity'])+flow<=0 or D(a['allocated'])+flow<=0: raise ValueError('withdrawal violates committed capital')
            units=D(h['units'])*(D(h['equity'])+flow)/D(h['equity'])
            db.execute('UPDATE risk_high_watermarks SET units=?,high=?,equity=?',(str(units),str(units*D(h['nav_high'])),str(D(h['equity'])+flow)))
            db.execute('UPDATE account_allocations SET cash=?,allocated=?,reconciled_at=0,recovery_required=1',(str(cash),str(D(a['allocated'])+flow)))

