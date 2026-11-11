"""Trusted fill/cancel evidence ingress; transactionally preserves cash and ownership."""
import json
from solbot.mathematics.decimal_utils import D
from solbot.accounts.allocation_ledger import now_ms

class ExposureManager:
    def __init__(self,ledger): self.ledger=ledger

    def fill(self,event_id,intent_id,quantity,price,fee,*,at=None):
        at=now_ms() if at is None else at
        qty,px,fee=D(quantity,positive=True),D(price,positive=True),D(fee,nonnegative=True)
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'FILL',{'intent_id':intent_id,'quantity':qty,'price':px,'fee':fee},at): return
            r=db.execute('SELECT * FROM fund_reservations WHERE intent_id=?',(intent_id,)).fetchone()
            if not r or r['status'] not in ('RESERVED','PARTIAL','CANCEL_PENDING','UNKNOWN'): raise ValueError('fill without outstanding reservation')
            request=json.loads(r['payload']); limit=D(request['price'])
            if px>limit or qty+D(r['filled'])>D(r['quantity']): raise ValueError('fill violates intent')
            if fee>qty*limit*D('.001'): raise ValueError('fee exceeds reserved fee; reconciliation required')
            remaining=max(D('0'),D(r['remaining'])-qty*limit)
            remaining_fee=remaining*D('.001')
            remaining_risk=D(r['risk'])*(remaining/D(r['remaining'])) if D(r['remaining']) else D('0')
            allocated_risk=D(r['risk'])-remaining_risk
            a=self.ledger.account(db)
            cash=D(a['cash'])-qty*px-fee
            if cash<0: raise ValueError('negative cash')
            db.execute('UPDATE account_allocations SET cash=?,equity_at=0,reconciled_at=0,recovery_required=1',(str(cash),))
            db.execute('UPDATE fund_reservations SET remaining=?,fees=?,risk=?,filled=?,cost=?,status=? WHERE intent_id=?',(str(remaining),str(remaining_fee),str(remaining_risk),str(D(r['filled'])+qty),str(D(r['cost'])+qty*px),'FILLED' if remaining==0 else (r['status'] if r['status'] in ('CANCEL_PENDING','UNKNOWN') else 'PARTIAL'),intent_id))
            pos=db.execute('SELECT * FROM robot_positions WHERE intent_id=?',(intent_id,)).fetchone()
            oldqty=D(pos['quantity']) if pos else D('0'); oldcost=D(pos['cost']) if pos else D('0'); oldrisk=D(pos['risk']) if pos else D('0')
            protection=pos['protection'] if pos else json.dumps({'stop_price':request['stop_price'],'native_status':'UNVERIFIED','responsibility':'KEEP_PROTECTION'})
            db.execute('INSERT INTO robot_positions VALUES(?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET quantity=excluded.quantity,cost=excluded.cost,risk=excluded.risk',(intent_id,'SUI',str(oldqty+qty),str(oldcost+qty*px),str(oldrisk+allocated_risk),protection))
            self.ledger.set_owned(db,'SUI','ROBOT_OWNED',self.ledger.owned(db,'SUI')+qty)
            db.execute("INSERT OR IGNORE INTO pending_actions VALUES(?,'VERIFY_PROTECTION',?,'PENDING')",('protection:'+intent_id,intent_id))

    def cancel_request(self,event_id,intent_id,*,at=None):
        at=now_ms() if at is None else at
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'CANCEL_REQUEST',{'intent_id':intent_id},at): return
            if not db.execute('SELECT 1 FROM fund_reservations WHERE intent_id=?',(intent_id,)).fetchone(): raise ValueError('unknown intent')
            db.execute("UPDATE fund_reservations SET status='CANCEL_PENDING' WHERE intent_id=? AND remaining!='0'",(intent_id,))
            db.execute("INSERT OR IGNORE INTO pending_actions VALUES(?,'CANCEL_ENTRY',?,'PENDING')",('cancel:'+intent_id,intent_id))

    def cancel_ack(self,event_id,intent_id,*,executed_quantity,at=None):
        at=now_ms() if at is None else at
        executed=D(executed_quantity,nonnegative=True)
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'CANCEL_ACK',{'intent_id':intent_id,'executed_quantity':executed},at): return
            r=db.execute('SELECT * FROM fund_reservations WHERE intent_id=?',(intent_id,)).fetchone()
            if not r or executed!=D(r['filled']): raise ValueError('cancel ACK requires all fills reconciled')
            db.execute("UPDATE fund_reservations SET remaining='0',fees='0',risk='0',status='CANCELLED' WHERE intent_id=?",(intent_id,))
            db.execute("UPDATE pending_actions SET status='ACKNOWLEDGED' WHERE action_id=?",('cancel:'+intent_id,))

    def unknown(self,event_id,intent_id,*,at=None):
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'UNKNOWN_ORDER',{'intent_id':intent_id},now_ms() if at is None else at): return
            db.execute("UPDATE fund_reservations SET status='UNKNOWN' WHERE intent_id=?",(intent_id,))
            db.execute('UPDATE account_allocations SET reconciled_at=0,recovery_required=1')

