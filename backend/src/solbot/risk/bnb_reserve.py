"""3/10/20 USDT refill candidates, zero order transport and bounded retries."""
from solbot.accounts.allocation_ledger import D, encoded, now_ms
from solbot.mathematics.decimal_utils import ceil_step

class BNBReserve:
    def __init__(self,ledger): self.ledger=ledger

    def candidate(self,intent_id,*,price,step,min_notional,observed_at,permission=False,at=None):
        at=now_ms() if at is None else at
        price,step,minimum=D(price,positive=True),D(step,positive=True),D(min_notional,positive=True)
        with self.ledger.transaction() as db:
            a=self.ledger.account(db)
            existing=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(intent_id,)).fetchone()
            params={'price':price,'step':step,'min_notional':minimum,'observed_at':observed_at,'permission':permission}
            if existing:
                if existing['payload']!=encoded(params): raise ValueError('refill intent conflict')
                return {'decision':'DENY','code':'DENY_DUPLICATE_REFILL','live_authorized':False}
            code='REFILL_CANDIDATE'
            balance=db.execute("SELECT * FROM exchange_balances WHERE asset='BNB'").fetchone()
            free=D(balance['free']) if balance else D('0')
            owned=self.ledger.owned(db,'BNB')
            value=min(owned,free)*price
            qty=ceil_step(max(D('10'),minimum)/price,step); notional=qty*price
            reserve=notional*D('1.001')
            committed=sum((D(r[0])+D(r[1]) for r in db.execute('SELECT remaining,fees FROM fund_reservations')),D('0'))
            usdt=db.execute("SELECT free FROM exchange_balances WHERE asset='USDT'").fetchone()
            if db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]: code='DENY_HARD_LOCKED'
            elif a['live_state']!='LIVE_ON': code='DENY_LIVE_STATE'
            elif permission is not True: code='DENY_TRADING_PERMISSION'
            elif not 0<=at-observed_at<=5000 or not 0<=at-a['reconciled_at']<=5000 or not 0<=at-a['equity_at']<=5000 or a['recovery_required']: code='PAUSE_RECONCILIATION'
            elif value>=3: code='DENY_REFILL_NOT_NEEDED'
            elif owned*price+notional>20 or notional>D('10')+price*step: code='DENY_REFILL_CAP_OR_MINIMUM'
            elif db.execute("SELECT 1 FROM bnb_refill_intents WHERE status IN ('CANDIDATE','UNKNOWN')").fetchone(): code='DENY_REFILL_PENDING'
            elif db.execute('SELECT COALESCE(SUM(attempts),0) FROM bnb_refill_intents').fetchone()[0]>=3: code='DENY_RETRY_LIMIT'
            elif not usdt or reserve+committed>min(D(usdt[0]),D(a['cash'])): code='DENY_CASH'
            if code=='REFILL_CANDIDATE':
                db.execute("INSERT INTO bnb_refill_intents VALUES(?,?,'CANDIDATE',?,0)",(intent_id,encoded(params),str(reserve)))
            result={'decision':'ALLOW' if code=='REFILL_CANDIDATE' else ('PAUSE' if code.startswith('PAUSE') else 'DENY'),'code':code,'quantity':str(qty),'notional':str(notional),'live_authorized':False}
            self.ledger.event(db,'bnb:'+intent_id,'BNB_CANDIDATE',{'input':params,'output':result},at)
            return result

    def failed(self,event_id,intent_id,*,definitely_not_submitted=False,at=None):
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'BNB_FAILURE',{'intent_id':intent_id,'definitely_not_submitted':definitely_not_submitted},now_ms() if at is None else at): return
            row=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(intent_id,)).fetchone()
            if not row: raise ValueError('unknown refill')
            if row['status']!='CANDIDATE': raise ValueError('terminal or unknown refill requires explicit reconciliation')
            attempts=row['attempts']+1
            status='FAILED' if definitely_not_submitted else 'UNKNOWN'
            db.execute('UPDATE bnb_refill_intents SET status=?,attempts=?,reserve=? WHERE intent_id=?',(status,attempts,'0' if definitely_not_submitted else row['reserve'],intent_id))
            db.execute('UPDATE account_allocations SET recovery_required=1,reconciled_at=0')

    @staticmethod
    def fee_rate(*,discount_confirmed=False):
        # P4 never assumes an account-level discount without a P5 adapter.
        return D('.001')

