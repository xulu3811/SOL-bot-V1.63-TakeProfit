"""OS-authenticated caller interface, never trusts an Android boolean as identity."""
from .allocation_ledger import D, encoded, now_ms

class CustodyPermissions:
    def __init__(self,ledger): self.ledger=ledger

    def grant(self,authorization_id,asset,quantity,*,actor,reason,expires_at,at=None):
        at=now_ms() if at is None else at
        qty=D(quantity,positive=True)
        if asset not in ('SUI','SOL') or not actor or not reason or type(expires_at) is not int or expires_at<=at: raise ValueError('invalid custody authorization')
        with self.ledger.transaction() as db:
            payload={'asset':asset,'quantity':qty,'actor':actor,'reason':reason,'expires_at':expires_at}
            if not self.ledger.event(db,authorization_id,'CUSTODY_GRANT',payload,at): return
            manual=self.ledger.owned(db,asset,'MANUAL_UNMANAGED')
            if qty>manual: raise ValueError('custody exceeds manual inventory')
            self.ledger.set_owned(db,asset,'MANUAL_UNMANAGED',manual-qty)
            self.ledger.set_owned(db,asset,'MANUAL_CUSTODIED',self.ledger.owned(db,asset,'MANUAL_CUSTODIED')+qty)
            db.execute('INSERT INTO custody_authorizations VALUES(?,?,?,?,?,?,0)',(authorization_id,asset,str(qty),actor,reason,expires_at))

    def revoke(self,event_id,authorization_id,*,actor,reason,at=None):
        at=now_ms() if at is None else at
        if not actor or not reason: raise ValueError('audit identity required')
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,event_id,'CUSTODY_REVOKE',{'authorization_id':authorization_id,'actor':actor,'reason':reason},at): return
            row=db.execute('SELECT * FROM custody_authorizations WHERE authorization_id=?',(authorization_id,)).fetchone()
            if not row: raise ValueError('unknown authorization')
            db.execute('UPDATE custody_authorizations SET revoked=1 WHERE authorization_id=?',(authorization_id,))
            db.execute('UPDATE account_allocations SET recovery_required=1,reconciled_at=0')
            # Do not reassign assets while an exit may still be in flight.

    def reserve_sell(self,intent_id,asset,quantity,*,category='ROBOT_OWNED',authorization_id=None,at=None):
        at=now_ms() if at is None else at
        qty=D(quantity,positive=True)
        payload={'asset':asset,'quantity':qty,'category':category,'authorization_id':authorization_id}
        with self.ledger.transaction() as db:
            old=db.execute('SELECT * FROM exit_reservations WHERE intent_id=?',(intent_id,)).fetchone()
            if old:
                if old['payload']!=encoded(payload): raise ValueError('exit intent ID conflict')
                if old['status']!='RESERVED' or D(old['quantity'])!=qty: raise ValueError('exit intent already consumed')
            a=self.ledger.account(db)
            if not 0<=at-a['reconciled_at']<=5000 or a['recovery_required']: raise ValueError('unreconciled account')
            if asset not in ('SUI','SOL') or category not in ('ROBOT_OWNED','MANUAL_CUSTODIED'): raise ValueError('unauthorized ownership')
            rows=db.execute("SELECT * FROM exit_reservations WHERE asset=? AND status='RESERVED' AND intent_id!=?",(asset,intent_id)).fetchall()
            committed=sum((D(r['quantity']) for r in rows),D('0'))
            own_committed=sum((D(r['quantity']) for r in rows if r['category']==category),D('0'))
            if category=='MANUAL_CUSTODIED':
                auth=db.execute('SELECT * FROM custody_authorizations WHERE authorization_id=?',(authorization_id,)).fetchone()
                if not auth or auth['asset']!=asset or auth['revoked'] or auth['expires_at']<=at: raise ValueError('custody permission invalid')
                auth_reserved=sum((D(r['quantity']) for r in rows if __import__('json').loads(r['payload']).get('authorization_id')==authorization_id),D('0'))
                if qty+auth_reserved>D(auth['quantity']): raise ValueError('custody quantity exceeded')
            balance=db.execute('SELECT * FROM exchange_balances WHERE asset=?',(asset,)).fetchone()
            if not balance or qty+committed>D(balance['free']) or qty+own_committed>self.ledger.owned(db,asset,category): raise ValueError('sell exceeds free authorized assets')
            if not old: db.execute("INSERT INTO exit_reservations VALUES(?,?,?,?,?,'RESERVED')",(intent_id,asset,category,str(qty),encoded(payload)))
            return {'decision':'ALLOW','live_authorized':False,'protection_responsibility':True}

    def sell_fill(self,event_id,intent_id,quantity,price,fee,*,position_id=None,at=None):
        """Book an observed fill against its prior sell reservation, never submit."""
        import json
        qty,px,cost=D(quantity,positive=True),D(price,positive=True),D(fee,nonnegative=True)
        at=now_ms() if at is None else at
        with self.ledger.transaction() as db:
            payload={'intent_id':intent_id,'quantity':qty,'price':px,'fee':cost,'position_id':position_id}
            if not self.ledger.event(db,event_id,'SELL_FILL',payload,at): return
            row=db.execute('SELECT * FROM exit_reservations WHERE intent_id=?',(intent_id,)).fetchone()
            if not row or row['status']!='RESERVED' or qty>D(row['quantity']) or cost>qty*px: raise ValueError('unmatched sell fill')
            owned=self.ledger.owned(db,row['asset'],row['category'])
            if qty>owned: raise ValueError('sell violates ownership')
            remaining=D(row['quantity'])-qty
            db.execute('UPDATE exit_reservations SET quantity=?,status=? WHERE intent_id=?',(str(remaining),'FILLED' if remaining==0 else 'RESERVED',intent_id))
            self.ledger.set_owned(db,row['asset'],row['category'],owned-qty)
            if row['category']=='ROBOT_OWNED':
                pos=db.execute('SELECT * FROM robot_positions WHERE intent_id=?',(position_id,)).fetchone()
                if not pos or pos['asset']!=row['asset'] or qty>D(pos['quantity']): raise ValueError('robot position mismatch')
                fraction=(D(pos['quantity'])-qty)/D(pos['quantity'])
                db.execute('UPDATE robot_positions SET quantity=?,cost=?,risk=? WHERE intent_id=?',(str(D(pos['quantity'])-qty),str(D(pos['cost'])*fraction),str(D(pos['risk'])*fraction),position_id))
                a=self.ledger.account(db)
                db.execute('UPDATE account_allocations SET cash=?',(str(D(a['cash'])+qty*px-cost),))
            else:
                authorization=json.loads(row['payload'])['authorization_id']
                auth=db.execute('SELECT quantity FROM custody_authorizations WHERE authorization_id=?',(authorization,)).fetchone()
                db.execute('UPDATE custody_authorizations SET quantity=? WHERE authorization_id=?',(str(D(auth[0])-qty),authorization))
                self.ledger.set_owned(db,'USDT','MANUAL_UNMANAGED',self.ledger.owned(db,'USDT','MANUAL_UNMANAGED')+qty*px-cost)
            db.execute('UPDATE account_allocations SET reconciled_at=0,equity_at=0,recovery_required=1')

