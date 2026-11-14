"""Guardian-owned execution evidence settlement; no exchange networking."""
import json
import secrets,hashlib,hmac
from dataclasses import asdict
from solbot.accounts.allocation_ledger import D, now_ms
from solbot.accounts.custody_permissions import CustodyPermissions
from solbot.risk.guardian import Guardian
from solbot.risk.permission_engine import TradeIntent
from solbot.risk.exposure_manager import ExposureManager
from solbot.risk.bnb_reserve import BNBReserve

class GuardianBridge:
    def __init__(self,ledger,clock=now_ms):
        self.ledger,self.clock=ledger,clock
        self.symbol=getattr(ledger,'symbol','SUIUSDT')
        self.base_asset=getattr(ledger,'base_asset','SUI')
        self._signing_key=secrets.token_bytes(32)
    def health(self):
        with self.ledger.transaction() as db:
            return {'schema':4,'policy':'guardian-p4-1','live_state':self.ledger.account(db)['live_state']}
    def reconcile(self,payload):
        from solbot.accounts.balance_reconciliation import BalanceReconciliation
        if payload['account_id']!=self.ledger.account_id: raise ValueError('wrong account')
        result=BalanceReconciliation(self.ledger).reconcile(payload['event_id'],payload['balances'],payload['observed_at'],account_id=payload['account_id'],unknown_orders=payload['unknown_orders'],at=self.clock())
        with self.ledger.transaction() as db: self.ledger.event(db,'snapshot:'+payload['event_id'],'P5_ACCOUNT_SNAPSHOT',dict(payload,result=result),self.clock())
        return result
    def position(self,intent_id):
        with self.ledger.transaction() as db:
            row=db.execute('SELECT * FROM robot_positions WHERE intent_id=?',(intent_id,)).fetchone()
            if not row: raise ValueError('position not owned')
            return dict(row,robot_owned_quantity=str(self.ledger.owned(db,self.base_asset)))
    def bind_order(self,params,protective=False):
        from solbot.accounts.allocation_ledger import encoded,digest
        key=params.get('newClientOrderId',params.get('origClientOrderId'))
        auxiliary=None
        with self.ledger.transaction() as db:
            if params.get('origClientOrderId'):
                own=db.execute('SELECT 1 FROM exit_reservations WHERE intent_id=?',(key,)).fetchone() or db.execute('SELECT 1 FROM bnb_refill_intents WHERE intent_id=?',(key,)).fetchone()
                if not own:
                    own=any('sb5-'+hashlib.sha256((self.ledger.account_id+'|'+r[0]).encode()).hexdigest()[:28]==key for r in db.execute('SELECT intent_id FROM fund_reservations'))
                if not own: raise PermissionError('cancel not robot owned')
                intent=None
            elif params.get('side')=='BUY' and params.get('symbol')==self.symbol:
                match=None
                for row in db.execute('SELECT * FROM fund_reservations'):
                    if 'sb5-'+hashlib.sha256((self.ledger.account_id+'|'+row['intent_id']).encode()).hexdigest()[:28]==key: match=row
                if not match: raise PermissionError('missing durable reservation')
                intent=json.loads(match['payload'])
                if any(D(params[k])!=D(intent[k]) for k in ('price','quantity')): raise PermissionError('order amount mismatch')
            elif params.get('side')=='SELL' and protective:
                row=db.execute('SELECT * FROM exit_reservations WHERE intent_id=?',(key,)).fetchone()
                if not row or row['status']!='RESERVED' or D(row['quantity'])!=D(params['quantity']): raise PermissionError('unreserved protective sell')
                approved=db.execute('SELECT payload FROM risk_events WHERE event_id=?',('aux:'+key,)).fetchone()
                if not approved or json.loads(approved[0])['params']!=params: raise PermissionError('protective parameters not bound')
                auxiliary=json.loads(approved[0])
                intent=None
            elif params.get('symbol')=='BNBUSDT':
                row=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(key,)).fetchone()
                if not row or row['status']!='CANDIDATE' or D(params['quantity'])*D(params['price'])*D('1.001')>D(row['reserve']): raise PermissionError('unreserved BNB')
                auxiliary={'purpose':'BNB','position_id':None}
                intent=None
            else: raise PermissionError('unsupported order')
        if intent and self.recheck(intent)['decision']!='ALLOW': raise PermissionError('latest Guardian checks denied')
        if auxiliary and self.approve_aux(params,auxiliary['purpose'],auxiliary['position_id'])['decision']!='ALLOW': raise PermissionError('latest auxiliary risk denied')
        token={'id':secrets.token_hex(16),'params':digest(params),'protective':protective,'expires_at':self.clock()+1000}
        token['mac']=hmac.new(self._signing_key,encoded(token).encode(),hashlib.sha256).hexdigest()
        return token
    def verify_order(self,params,token,protective=False):
        from solbot.accounts.allocation_ledger import encoded,digest
        body={k:v for k,v in token.items() if k!='mac'}
        if not hmac.compare_digest(token.get('mac',''),hmac.new(self._signing_key,encoded(body).encode(),hashlib.sha256).hexdigest()): return False
        if body['params']!=digest(params) or body['protective']!=protective or self.clock()>=body['expires_at']: return False
        self.bind_order(params,protective) # recheck current reservation/risk at consumption
        with self.ledger.transaction() as db:
            if db.execute('SELECT 1 FROM risk_events WHERE event_id=?',('permit:'+body['id'],)).fetchone(): return False
            self.ledger.event(db,'permit:'+body['id'],'P5_PERMISSION_CONSUMED',body,self.clock())
        return True
    def approve(self,intent): return asdict(Guardian(self.ledger).evaluate(TradeIntent(**intent),at=self.clock(),reserve=True))
    def recheck(self,intent): return asdict(Guardian(self.ledger).evaluate(TradeIntent(**intent),at=self.clock()))
    def unknown(self,intent_id):
        ExposureManager(self.ledger).unknown('p5-unknown:'+intent_id,intent_id,at=self.clock())
    def bnb_candidate(self,params):
        from solbot.accounts.allocation_ledger import encoded
        qty,price=D(params['quantity'],positive=True),D(params['price'],positive=True)
        key=params['newClientOrderId']; cost=qty*price
        with self.ledger.transaction() as db:
            a=self.ledger.account(db)
            free=db.execute("SELECT free FROM exchange_balances WHERE asset='BNB'").fetchone()
            usdt=db.execute("SELECT free FROM exchange_balances WHERE asset='USDT'").fetchone()
            owned=self.ledger.owned(db,'BNB')
            committed=sum((D(r[0])+D(r[1]) for r in db.execute('SELECT remaining,fees FROM fund_reservations')),D('0'))
            if params['symbol']!='BNBUSDT' or params['side']!='BUY' or not D('10')<=cost<=D('10.1'): raise PermissionError('invalid BNB intent')
            if a['live_state']!='LIVE_ON' or a['recovery_required'] or not 0<=self.clock()-a['reconciled_at']<=5000: raise PermissionError('BNB account not ready')
            if db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0] or min(owned,D(free[0]) if free else D('0'))*price>=3 or owned*price+cost>20: raise PermissionError('BNB risk limits')
            if not usdt or committed+cost*D('1.001')>min(D(a['cash']),D(usdt[0])): raise PermissionError('BNB funds')
            old=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(key,)).fetchone()
            if old:
                if old['payload']!=encoded({'price':price,'params':params}) or old['status']!='CANDIDATE': raise ValueError('BNB reused')
            else:
                if db.execute("SELECT 1 FROM bnb_refill_intents WHERE status IN ('CANDIDATE','UNKNOWN')").fetchone(): raise PermissionError('BNB pending')
                db.execute("INSERT INTO bnb_refill_intents VALUES(?,?,'CANDIDATE',?,0)",(key,encoded({'price':price,'params':params}),str(cost*D('1.001'))))
        return {'approved':True,'intent_hash':hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()}
    def approve_aux(self,params,purpose,position_id):
        key=params['newClientOrderId']
        if purpose=='BNB':
            with self.ledger.transaction() as db:
                r=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(key,)).fetchone()
                if not r or r['status']!='CANDIDATE': return {'decision':'DENY'}
                request=json.loads(r['payload'])
                if D(params['price'])!=D(request['price']) or D(params['price'])*D(params['quantity'])*D('1.001')!=D(r['reserve']): return {'decision':'DENY'}
                if db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]: return {'decision':'DENY'}
                a=self.ledger.account(db)
                cash=db.execute("SELECT free FROM exchange_balances WHERE asset='USDT'").fetchone()
                if a['live_state']!='LIVE_ON' or a['recovery_required'] or not 0<=self.clock()-a['reconciled_at']<=5000 or not cash or D(r['reserve'])>min(D(cash[0]),D(a['cash'])): return {'decision':'DENY'}
                return {'decision':'ALLOW'}
        if params.get('side')!='SELL' or params.get('symbol')!=self.symbol: return {'decision':'DENY'}
        with self.ledger.transaction() as db:
            pos=db.execute('SELECT * FROM robot_positions WHERE intent_id=?',(position_id,)).fetchone()
            if not pos or D(params['quantity'])>D(pos['quantity']): return {'decision':'DENY'}
            if purpose=='PROTECT' and D(params.get('stopPrice','0'))<D(json.loads(pos['protection'])['stop_price']): return {'decision':'DENY'}
        with self.ledger.transaction() as db:
            a=self.ledger.account(db)
            if a['recovery_required'] or not 0<=self.clock()-a['reconciled_at']<=5000: raise PermissionError('fresh account required')
            snapshot=db.execute("SELECT payload FROM risk_events WHERE kind='P5_ACCOUNT_SNAPSHOT' ORDER BY rowid DESC LIMIT 1").fetchone()
            known={}
            if snapshot:
                evidence=json.loads(snapshot[0])
                if evidence['result']=='RECONCILED' and evidence['observed_at']==a['reconciled_at']:
                    known={r['client_id']:D(r['remaining_quantity']) for r in evidence.get('known_orders',[]) if r['symbol']==self.symbol and r['purpose'] in ('PROTECT','EXIT') and r['status'] in ('NEW','PARTIALLY_FILLED')}
            rows=[dict(r) for r in db.execute("SELECT * FROM exit_reservations WHERE asset=? AND status='RESERVED' AND intent_id!=?",(self.base_asset,key))]
            total=sum((D(r['quantity']) for r in rows),D('0'))
            exchange_locked=sum((min(D(r['quantity']),known.get(r['intent_id'],D('0'))) for r in rows),D('0'))
            bal=db.execute("SELECT free,locked FROM exchange_balances WHERE asset=?",(self.base_asset,)).fetchone()
            qty=D(params['quantity'])
            if not bal or exchange_locked>D(bal['locked']) or qty+total-exchange_locked>D(bal['free']) or qty+total>self.ledger.owned(db,self.base_asset): raise PermissionError('free/owned sell limit')
            from solbot.accounts.allocation_ledger import encoded
            payload={'asset':self.base_asset,'quantity':qty,'category':'ROBOT_OWNED','authorization_id':None}
            old=db.execute('SELECT * FROM exit_reservations WHERE intent_id=?',(key,)).fetchone()
            if old and (old['payload']!=encoded(payload) or old['status']!='RESERVED'): raise ValueError('exit conflict')
            if not old: db.execute("INSERT INTO exit_reservations VALUES(?,?,'ROBOT_OWNED',?,?,'RESERVED')",(key,self.base_asset,str(qty),encoded(payload)))
            self.ledger.event(db,'aux:'+key,'P5_AUX_APPROVAL',{'params':params,'purpose':purpose,'position_id':position_id},self.clock())
        result={'decision':'ALLOW','live_authorized':False}
        return result
    def settle(self,message):
        if message['kind']=='TERMINAL':
            if message['purpose']=='ENTRY':
                ExposureManager(self.ledger).cancel_ack(message['event_id'],message['intent_id'],executed_quantity=message['executed_quantity'],at=self.clock())
            elif message['purpose'] in ('PROTECT','EXIT'):
                with self.ledger.transaction() as db:
                    if not self.ledger.event(db,message['event_id'],'P5_TERMINAL',message,self.clock()): return
                    db.execute("UPDATE exit_reservations SET quantity='0',status='TERMINAL' WHERE intent_id=?",(message['intent_id'],))
            elif message['purpose']=='BNB':
                with self.ledger.transaction() as db:
                    if self.ledger.event(db,message['event_id'],'P5_BNB_TERMINAL',message,self.clock()):
                        db.execute("UPDATE bnb_refill_intents SET reserve='0',status=? WHERE intent_id=?",(message['state'],message['intent_id']))
            return
        t=message['trade']; qty,price,fee=D(t['qty'],positive=True),D(t['price'],positive=True),D(t['commission'],nonnegative=True)
        asset=t['commissionAsset']; ident=message['intent_id']
        if message['purpose'] in ('PROTECT','EXIT'):
            CustodyPermissions(self.ledger).sell_fill(message['event_id'],ident,str(qty),str(price),str(fee if asset=='USDT' else D('0')),position_id=message['position_id'],at=self.clock())
            if asset!='USDT' and fee:
                with self.ledger.transaction() as db:
                    if self.ledger.event(db,message['event_id']+':commission','P5_COMMISSION',message,self.clock()):
                        self.ledger.set_owned(db,asset,'ROBOT_OWNED',self.ledger.owned(db,asset)-fee)
                        if asset==self.base_asset:
                            pos=db.execute('SELECT quantity FROM robot_positions WHERE intent_id=?',(message['position_id'],)).fetchone()
                            if D(pos[0])<fee: raise ValueError('base commission position deficit')
                            db.execute('UPDATE robot_positions SET quantity=? WHERE intent_id=?',(str(D(pos[0])-fee),message['position_id']))
            return
        if message['purpose']=='BNB':
            with self.ledger.transaction() as db:
                if not self.ledger.event(db,message['event_id'],'P5_BNB_FILL',message,self.clock()): return
                r=db.execute('SELECT * FROM bnb_refill_intents WHERE intent_id=?',(ident,)).fetchone()
                if not r: raise ValueError('unapproved BNB fill')
                a=self.ledger.account(db)
                if asset not in ('BNB','USDT'): raise ValueError('unknown BNB commission asset')
                self.ledger.set_owned(db,'BNB','ROBOT_OWNED',self.ledger.owned(db,'BNB')+qty-(fee if asset=='BNB' else D('0')))
                cash=D(a['cash'])-qty*price-(fee if asset=='USDT' else D('0'))
                reserve=max(D('0'),D(r['reserve'])-qty*price*D('1.001'))
                db.execute('UPDATE bnb_refill_intents SET reserve=? WHERE intent_id=?',(str(reserve),ident))
                db.execute('UPDATE account_allocations SET cash=?,equity_at=0,reconciled_at=0,recovery_required=1',(str(cash),))
            return
        with self.ledger.transaction() as db:
            if not self.ledger.event(db,message['event_id'],'P5_FILL',message,self.clock()): return
            r=db.execute('SELECT * FROM fund_reservations WHERE intent_id=?',(ident,)).fetchone()
            if not r: raise ValueError('unreserved exchange fill')
            request=json.loads(r['payload']); limit=D(request['price'])
            if qty+D(r['filled'])>D(r['quantity']) or price>limit: raise ValueError('exchange fill conflict')
            if qty*limit>D(r['remaining']): raise ValueError('late fill conflicts with released reservation; reconcile')
            if asset not in (self.base_asset,'USDT','BNB') and fee: raise ValueError('unvalued commission asset')
            acquired=qty-(fee if asset==self.base_asset else D('0'))
            if acquired<0: raise ValueError('base commission exceeds quantity')
            if asset=='BNB' and fee>self.ledger.owned(db,'BNB'): raise ValueError('BNB fee ownership unresolved')
            remaining=D(r['remaining'])-qty*limit
            remaining_risk=D(r['risk'])*remaining/D(r['remaining']) if D(r['remaining']) else D('0')
            a=self.ledger.account(db); cash=D(a['cash'])-qty*price-(fee if asset=='USDT' else D('0'))
            if cash<0: raise ValueError('fill cash deficit; reconcile')
            pos=db.execute('SELECT * FROM robot_positions WHERE intent_id=?',(ident,)).fetchone()
            pq=D(pos['quantity']) if pos else D('0'); pc=D(pos['cost']) if pos else D('0'); pr=D(pos['risk']) if pos else D('0')
            protection=pos['protection'] if pos else json.dumps({'stop_price':request['stop_price'],'native_status':'UNVERIFIED','responsibility':'KEEP_PROTECTION'})
            db.execute('INSERT INTO robot_positions VALUES(?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET quantity=excluded.quantity,cost=excluded.cost,risk=excluded.risk',(ident,self.base_asset,str(pq+acquired),str(pc+qty*price),str(pr+D(r['risk'])-remaining_risk),protection))
            self.ledger.set_owned(db,self.base_asset,'ROBOT_OWNED',self.ledger.owned(db,self.base_asset)+acquired)
            if asset=='BNB': self.ledger.set_owned(db,'BNB','ROBOT_OWNED',self.ledger.owned(db,'BNB')-fee)
            db.execute('UPDATE fund_reservations SET remaining=?,fees=?,risk=?,filled=?,cost=?,status=? WHERE intent_id=?',(str(remaining),str(remaining*D('.001')),str(remaining_risk),str(D(r['filled'])+qty),str(D(r['cost'])+qty*price),'FILLED' if remaining==0 else (r['status'] if r['status'] in ('UNKNOWN','CANCEL_PENDING') else 'PARTIAL'),ident))
            db.execute('UPDATE account_allocations SET cash=?,equity_at=0,reconciled_at=0,recovery_required=1',(str(cash),))
            db.execute("INSERT OR IGNORE INTO pending_actions VALUES(?,'VERIFY_PROTECTION',?,'PENDING')",('protection:'+ident,ident))
