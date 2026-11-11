"""Durable submit-before-I/O, no blind retry, exchange-confirmed state only."""
import hashlib
import json
from dataclasses import asdict
from solbot.accounts.allocation_ledger import encoded, digest, now_ms
from solbot.mathematics.decimal_utils import D
from .p5_store import OrderStore

TERMINAL={'FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH','REJECTED'}
EXCHANGE_STATES=TERMINAL|{'NEW','PARTIALLY_FILLED','PENDING_NEW','PENDING_CANCEL'}

class ExecutionEngine:
    def __init__(self,store,client,guardian,gate,*,filters,protection_ready,clock=now_ms,on_position=None):
        self.store,self.client,self.guardian,self.gate=store,client,guardian,gate
        self.filters,self.protection_ready,self.clock=filters,protection_ready,clock
        self.on_position=on_position
    def _validate_filters(self,symbol,params):
        result=self.filters(symbol,params)
        if isinstance(result,(tuple,list)):
            if result: raise PermissionError('exchange filters rejected: '+','.join(result))
        elif result is not None and result is not True:
            raise PermissionError('exchange filter validation failed')
    def submit_entry(self,intent):
        self.gate.require('TRADE',intent.account_id,intent.symbol)
        if not self.protection_ready(intent.symbol): raise PermissionError('native protection unavailable')
        self._validate_filters(intent.symbol,{'symbol':intent.symbol,'side':'BUY','type':'LIMIT','price':intent.price,'quantity':intent.quantity,'timeInForce':'GTC'})
        key='sb5-'+hashlib.sha256((intent.account_id+'|'+intent.intent_id).encode()).hexdigest()[:28]
        old=self.store.get(key)
        if old:
            if old['fingerprint']!=digest(intent): raise ValueError('intent reuse')
            return old # Query/recover explicitly. Never automatically resubmit.
        with self.store.tx() as db:
            if db.execute("SELECT 1 FROM orders WHERE state IN ('UNKNOWN','SUBMITTING','CANCEL_PENDING')").fetchone(): raise PermissionError('unknown order blocks entries')
            db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(key,intent.intent_id,digest(intent),encoded(intent),'ENTRY','CREATED',None,'0','0',0,None,self.clock()))
        decision=self.guardian.approve(asdict(intent))
        if decision['decision']!='ALLOW':
            with self.store.tx() as db: db.execute("UPDATE orders SET state='REJECTED',terminal='REJECTED' WHERE client_id=?",(key,))
            return self.store.get(key)
        with self.store.tx() as db: db.execute("UPDATE orders SET state='RISK_APPROVED' WHERE client_id=?",(key,))
        self.gate.require('TRADE',intent.account_id,intent.symbol)
        if self.clock()>=intent.expires_at or self.guardian.recheck(asdict(intent))['decision']!='ALLOW':
            self.store.unknown(key,'approval expired or risk changed before send'); self.gate.pause(); return self.store.get(key)
        params={'symbol':intent.symbol,'side':'BUY','type':'LIMIT','quantity':intent.quantity,'price':intent.price,'timeInForce':'GTC','newClientOrderId':key}
        with self.store.tx() as db: db.execute("UPDATE orders SET state='SUBMITTING' WHERE client_id=?",(key,))
        try:
            reply=self.client.place(**params)
            self.observe(reply)
        except Exception:
            self.store.unknown(key,'submission result unknown'); self.gate.pause()
            self.guardian.unknown(intent.intent_id)
        return self.store.get(key)

    def observe(self,event):
        """REST orders and unwrapped executionReport share monotone cumulative state."""
        client_id=event.get('clientOrderId',event.get('c'))
        row=self.store.get(client_id)
        if not row: raise ValueError('unrecognized order; manual assets not adopted')
        payload=json.loads(row['payload']); symbol=event.get('symbol',event.get('s'))
        if symbol!=payload['symbol']: raise ValueError('event symbol mismatch')
        if event.get('side',event.get('S',payload.get('side','BUY')))!=payload.get('side','BUY'): raise ValueError('event side mismatch')
        state=event.get('status',event.get('X'))
        if state not in EXCHANGE_STATES: raise ValueError('unknown exchange status')
        exchange_id=str(event.get('orderId',event.get('i')))
        if exchange_id=='None' or row['exchange_id'] not in (None,exchange_id): raise ValueError('exchange identity mismatch')
        cumulative=D(event.get('executedQty',event.get('z','0')),nonnegative=True)
        quote=D(event.get('cummulativeQuoteQty',event.get('Z','0')),nonnegative=True)
        if cumulative>D(payload['quantity']): raise ValueError('overfilled order')
        event_time=int(event.get('updateTime',event.get('T',event.get('E',self.clock()))))
        trades=[]
        if event.get('x')=='TRADE' and D(event.get('l','0'))>0:
            trades=[{'id':event['t'],'qty':event['l'],'price':event['L'],'commission':event['n'],'commissionAsset':event['N']}]
        elif 'fills' in event:
            trades=[dict(t,id=t.get('tradeId',t.get('id'))) for t in event['fills']]
        with self.store.tx() as db:
            current=dict(db.execute('SELECT * FROM orders WHERE client_id=?',(client_id,)).fetchone())
            for trade in trades:
                if trade['id'] is None: continue # query myTrades before settlement
                trade_key=symbol+':'+exchange_id+':'+str(trade['id'])
                message={'kind':'FILL','intent_id':row['intent_id'],'client_id':client_id,'purpose':row['purpose'],'symbol':symbol,'side':payload.get('side','BUY'),'position_id':payload.get('position_id'),'trade':trade,'event_id':'fill:'+trade_key}
                old=db.execute('SELECT payload FROM fills WHERE trade_key=?',(trade_key,)).fetchone()
                # Canonical fields avoid REST/WS additional metadata differences.
                normalized={k:str(trade[k]) for k in ('id','qty','price','commission','commissionAsset')}
                message['trade']=normalized
                if old and old[0]!=encoded(normalized): raise ValueError('trade ID payload conflict')
                if not old:
                    db.execute('INSERT INTO fills VALUES(?,?,?)',(trade_key,client_id,encoded(normalized)))
                    db.execute('INSERT INTO outbox VALUES(?,?,0)',(message['event_id'],encoded(message)))
            newer=cumulative>=D(current['cumulative']) and event_time>=current['last_time']
            if newer:
                next_state=state
                if current['terminal'] and state not in TERMINAL: next_state=current['terminal']
                if current['terminal']=='FILLED': next_state='FILLED'
                if state=='FILLED' and cumulative!=D(payload['quantity']): raise ValueError('inconsistent FILLED')
                db.execute('UPDATE orders SET state=?,exchange_id=?,cumulative=?,quote=?,last_time=?,terminal=? WHERE client_id=?',(next_state,exchange_id,str(cumulative),str(quote),event_time,next_state if next_state in TERMINAL else current['terminal'],client_id))
            db.execute('INSERT OR IGNORE INTO order_events VALUES(?,?,?)',(digest(event),encoded(event),self.clock()))
        self.drain()
        self._terminal_settlement(client_id)
        if trades and row['purpose']=='ENTRY':
            if self.on_position is None: self.gate.pause()
            else:
                try: self.on_position(row['intent_id'])
                except Exception: self.gate.pause(); raise

    def drain(self):
        with self.store.tx() as db: pending=[dict(r) for r in db.execute('SELECT * FROM outbox WHERE delivered=0 ORDER BY rowid')]
        for row in pending:
            self.guardian.settle(json.loads(row['payload']))
            with self.store.tx() as db: db.execute('UPDATE outbox SET delivered=1 WHERE event_id=?',(row['event_id'],))

    def _terminal_settlement(self,client_id):
        row=self.store.get(client_id)
        if row['state'] not in TERMINAL: return
        with self.store.tx() as db:
            total=sum((D(json.loads(r[0])['qty']) for r in db.execute('SELECT payload FROM fills WHERE client_id=?',(client_id,))),D('0'))
            if total!=D(row['cumulative']): return # fees/trades missing: keep reserve
            message={'kind':'TERMINAL','intent_id':row['intent_id'],'event_id':'terminal:'+client_id,'executed_quantity':str(total),'state':row['state'],'purpose':row['purpose']}
            db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,0)',(message['event_id'],encoded(message)))
        self.drain()

    def cancel(self,symbol,clientOrderId):
        self.gate.require('CANCEL',symbol=symbol)
        row=self.store.get(clientOrderId)
        if not row or json.loads(row['payload'])['symbol']!=symbol: raise ValueError('unknown robot order')
        if row['state'] in TERMINAL: return row
        with self.store.tx() as db: db.execute("UPDATE orders SET state='CANCEL_PENDING' WHERE client_id=?",(clientOrderId,))
        try:
            reply=self.client.cancel(symbol=symbol,origClientOrderId=clientOrderId); self.observe(reply); return reply
        except Exception:
            self.store.unknown(clientOrderId,'cancel not confirmed'); self.gate.pause(); raise

    def recover(self):
        self.gate.require('PRIVATE_READ')
        self.gate.pause()
        with self.store.tx() as db: rows=[dict(r) for r in db.execute('SELECT * FROM orders')]
        for row in rows:
            symbol=json.loads(row['payload'])['symbol']
            try:
                reply=self.client.query(symbol=symbol,origClientOrderId=row['client_id'])
                trades=self.client.trades(symbol=symbol,orderId=reply['orderId'])
                reply=dict(reply,fills=[dict(t,tradeId=t['id']) for t in trades]); self.observe(reply)
            except Exception: self.store.unknown(row['client_id'],'recovery unresolved')
        self.drain()
        return [self.store.get(r['client_id']) for r in rows]

    def handle_hard_lock(self):
        self.gate.hard_lock()
        with self.store.tx() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM orders WHERE purpose IN ('ENTRY','BNB') AND terminal IS NULL")]
        for row in rows:
            self.cancel(json.loads(row['payload'])['symbol'],row['client_id'])
        return [self.store.get(r['client_id']) for r in rows]

    def submit(self,params,*,purpose,position_id=None):
        if purpose not in ('PROTECT','EXIT','BNB'): raise ValueError('unsupported auxiliary intent')
        params=dict(params); symbol=params['symbol']
        self.gate.require('TRADE' if purpose=='BNB' else 'PROTECT',symbol=symbol)
        self._validate_filters(symbol,params)
        key=params.get('newClientOrderId') or 'sb5a-'+digest(params)[:26]
        params['newClientOrderId']=key
        fingerprint=digest({'params':params,'purpose':purpose,'position_id':position_id})
        old=self.store.get(key)
        if old:
            if old['fingerprint']!=fingerprint: raise ValueError('auxiliary intent reused')
            if old['state']=='UNKNOWN': raise PermissionError('auxiliary order unknown')
            return {'clientOrderId':key,'symbol':symbol,'status':old['state'],'orderId':old['exchange_id'],'executedQty':old['cumulative']}
        approved=self.guardian.approve_aux(params,purpose,position_id)
        if approved.get('decision')!='ALLOW': raise PermissionError('Guardian auxiliary risk denied')
        payload=dict(params,position_id=position_id)
        with self.store.tx() as db:
            db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(key,key,fingerprint,encoded(payload),purpose,'SUBMITTING',None,'0','0',0,None,self.clock()))
        try:
            self.gate.require('TRADE' if purpose=='BNB' else 'PROTECT',symbol=symbol)
            reply=self.client.place(**params); self.observe(reply); return reply
        except Exception:
            self.store.unknown(key,'auxiliary submission unknown'); self.gate.pause(); raise
