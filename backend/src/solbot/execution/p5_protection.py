"""Durable native protection coordinator; all I/O goes through the guarded engine."""
from contextlib import closing, contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3
from solbot.mathematics.decimal_utils import D, ceil_step, floor_step

TERMINAL = {'FILLED','CANCELED','EXPIRED','REJECTED','EXPIRED_IN_MATCH'}
ACTIVE = {'NEW','PARTIALLY_FILLED'}

class ProtectionError(ValueError): pass

class ProtectionManager:
    def __init__(self,path,executor,gate):
        self.path=Path(path); self.executor=executor; self.gate=gate
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS native_protection(client_id TEXT PRIMARY KEY,position_id TEXT NOT NULL,symbol TEXT NOT NULL,quantity TEXT NOT NULL,stop TEXT NOT NULL,executed TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,exchange_id TEXT,previous_id TEXT,alert TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS protection_events(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None,timeout=10)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            try: yield db; db.commit()
            except BaseException: db.rollback(); raise

    @staticmethod
    def plan(symbol,quantity,stop_price,limit_price,exchange_info,*,trailing_delta=None):
        if symbol!='SUIUSDT' or exchange_info.get('symbol')!=symbol or exchange_info.get('status')!='TRADING' or exchange_info.get('isSpotTradingAllowed') is not True:
            raise ProtectionError('unsupported or inactive protection market')
        types=exchange_info.get('orderTypes',[])
        kind='STOP_LOSS' if 'STOP_LOSS' in types else 'STOP_LOSS_LIMIT' if 'STOP_LOSS_LIMIT' in types else None
        if kind is None: raise ProtectionError('native stop capability missing')
        filters={f['filterType']:f for f in exchange_info.get('filters',[])}
        if 'PRICE_FILTER' not in filters or 'LOT_SIZE' not in filters: raise ProtectionError('missing exchange precision rules')
        pf,lot=filters['PRICE_FILTER'],filters['LOT_SIZE']
        tick,step=D(pf['tickSize'],positive=True),D(lot['stepSize'],positive=True)
        qty=floor_step(quantity,step); stop=ceil_step(stop_price,tick)
        if qty!=D(quantity) or qty<D(lot['minQty']) or qty>D(lot['maxQty']): raise ProtectionError('quantity filter')
        if not D(pf['minPrice'])<=stop<=D(pf['maxPrice']): raise ProtectionError('stop filter')
        result={'symbol':symbol,'side':'SELL','type':kind,'quantity':str(qty),'stopPrice':str(stop)}
        if kind=='STOP_LOSS_LIMIT':
            limit=floor_step(limit_price,tick)
            if not D(pf['minPrice'])<=limit<=stop or limit>D(pf['maxPrice']): raise ProtectionError('limit filter')
            result.update(price=str(limit),timeInForce='GTC')
        if trailing_delta is not None:
            td=filters.get('TRAILING_DELTA',{})
            if exchange_info.get('allowTrailingStop') is not True or type(trailing_delta) is not int or not td.get('minTrailingBelowDelta',1)<=trailing_delta<=td.get('maxTrailingBelowDelta',0):
                raise ProtectionError('native trailing parameter unsupported')
            result['trailingDelta']=trailing_delta
        # Remaining contextual filters are rechecked by the engine immediately before send.
        return result

    def install(self,position_id,symbol,quantity,owned_quantity,stop_price,limit_price,exchange_info,*,trailing_delta=None,previous_id=None,generation=None):
        self.gate.require('PROTECT',account_id='SUI-live',symbol=symbol)
        params=self.plan(symbol,quantity,stop_price,limit_price,exchange_info,trailing_delta=trailing_delta)
        identity=json.dumps([position_id,params,generation],sort_keys=True,separators=(',',':'))
        client_id='p5p-'+hashlib.sha256(identity.encode()).hexdigest()[:28]
        params['newClientOrderId']=client_id
        with self.tx() as db:
            old=db.execute('SELECT * FROM native_protection WHERE client_id=?',(client_id,)).fetchone()
            if old: return dict(old)
            committed=sum((D(r['quantity'])-D(r['executed']) for r in db.execute('SELECT * FROM native_protection WHERE symbol=?',(symbol,)) if r['state'] not in TERMINAL),D('0'))
            if committed+D(quantity)>D(owned_quantity,nonnegative=True): raise ProtectionError('aggregate protective sell quantity exceeds robot inventory')
            db.execute('INSERT INTO native_protection VALUES(?,?,?,?,?,?,?,?,?,?,?)',(client_id,position_id,symbol,str(D(quantity)),params['stopPrice'],'0','SUBMITTING',json.dumps(params,sort_keys=True),None,previous_id,'PROTECTION_NOT_CONFIRMED'))
        try:
            reply=self.executor.submit(params,purpose='PROTECT',position_id=position_id)
            self.observe('ack:'+client_id,reply)
        except Exception:
            with self.tx() as db:
                db.execute("UPDATE native_protection SET state='UNKNOWN',alert='PROTECTION_SUBMISSION_UNKNOWN' WHERE client_id=? AND state='SUBMITTING'",(client_id,))
            raise
        return self.snapshot(client_id)

    def snapshot(self,client_id=None):
        with self.tx() as db:
            if client_id:
                row=db.execute('SELECT * FROM native_protection WHERE client_id=?',(client_id,)).fetchone()
                if not row: raise ProtectionError('unknown protection')
                return dict(row)
            return [dict(r) for r in db.execute('SELECT * FROM native_protection')]

    def initial(self,position_id,quantity,owned_quantity,entry_price,exchange_info):
        from solbot.mathematics.protective_stop import initial_stop,stop_limit_price
        filters={f['filterType']:f for f in exchange_info['filters']}
        tick=filters['PRICE_FILTER']['tickSize']
        stop=initial_stop(entry_price,tick)
        return self.install(position_id,'SUIUSDT',quantity,owned_quantity,stop,stop_limit_price(stop,tick),exchange_info)

    def observe(self,event_id,event):
        """Called only with authenticated exchange/order-query evidence by the engine."""
        payload=json.dumps(event,sort_keys=True,separators=(',',':'))
        client_id=event.get('clientOrderId',event.get('c'))
        status=event.get('status',event.get('X'))
        cumulative=D(event.get('executedQty',event.get('z','0')),nonnegative=True)
        with self.tx() as db:
            previous=db.execute('SELECT payload FROM protection_events WHERE event_id=?',(event_id,)).fetchone()
            if previous:
                if previous[0]!=payload: raise ProtectionError('event identity conflict')
                return
            row=db.execute('SELECT * FROM native_protection WHERE client_id=?',(client_id,)).fetchone()
            if not row or event.get('symbol',event.get('s'))!=row['symbol'] or event.get('side',event.get('S','SELL'))!='SELL': raise ProtectionError('protection identity mismatch')
            exchange_id=event.get('orderId',event.get('i'))
            if row['exchange_id'] is not None and exchange_id is not None and str(exchange_id)!=row['exchange_id']: raise ProtectionError('exchange order identity changed')
            if status not in ACTIVE|TERMINAL|{'UNKNOWN'} or cumulative>D(row['quantity']): raise ProtectionError('invalid protection order evidence')
            db.execute('INSERT INTO protection_events VALUES(?,?)',(event_id,payload))
            if cumulative<D(row['executed']): return
            if row['state'] in TERMINAL and status not in TERMINAL: return
            if status in ACTIVE and event.get('orderId',event.get('i')) is None: raise ProtectionError('exchange order identity required')
            if status=='FILLED' and cumulative!=D(row['quantity']): raise ProtectionError('filled quantity mismatch')
            alert='' if status in ACTIVE|{'FILLED'} else 'PROTECTION_NOT_CONFIRMED'
            db.execute('UPDATE native_protection SET state=?,executed=?,exchange_id=?,alert=? WHERE client_id=?',(status,str(cumulative),str(exchange_id) if exchange_id is not None else row['exchange_id'],alert,client_id))

    def update(self,client_id,new_stop,limit_price,owned_quantity,exchange_info):
        row=self.snapshot(client_id)
        if D(new_stop)<D(row['stop']): raise ProtectionError('protective stop cannot decrease')
        if D(new_stop)==D(row['stop']): return row
        self.gate.require('PROTECT',account_id='SUI-live',symbol=row['symbol'])
        if row['state'] not in ACTIVE: raise ProtectionError('old protection state not confirmed')
        # Never cancel first. A separate independently owned free tranche must
        # cover the new order until the old cancellation is positively confirmed.
        qty=D(row['quantity'])-D(row['executed'])
        new=self.install(row['position_id'],row['symbol'],qty,owned_quantity,new_stop,limit_price,exchange_info,previous_id=client_id)
        if new['state'] not in ACTIVE: raise ProtectionError('replacement not acknowledged; old protection retained')
        try:
            response=self.executor.cancel(row['symbol'],client_id)
            self.observe('replace-cancel:'+client_id+':'+new['client_id'],response)
        except Exception:
            with self.tx() as db: db.execute("UPDATE native_protection SET state='UNKNOWN',alert='OLD_PROTECTION_CANCEL_UNKNOWN' WHERE client_id=?",(client_id,))
            raise
        return new

    def apply_apts(self,client_id,decision,owned_quantity,exchange_info):
        row=self.snapshot(client_id)
        if decision.context.symbol!=row['symbol'] or decision.context.account_id!='SUI-live': raise ProtectionError('APTS ownership mismatch')
        if decision.status in ('DATA_UNAVAILABLE','DUPLICATE_OR_OLD_EVENT'): return row
        if decision.exit_required:
            if row['alert'].startswith('APTS_EXIT_'): raise ProtectionError('prior APTS exit requires order reconciliation')
            self.gate.require('PROTECT',account_id='SUI-live',symbol=row['symbol'])
            qty=D(row['quantity'])-D(row['executed'])
            intent='p5x-'+hashlib.sha256((client_id+str(decision.context.decision_time_ms)).encode()).hexdigest()[:28]
            params={'symbol':row['symbol'],'side':'SELL','type':'LIMIT','timeInForce':'GTC','quantity':str(qty),'price':str(decision.limit_price),'newClientOrderId':intent}
            # Guarded engine checks current free inventory and aggregate exits.
            # If native protection locks the inventory, keep it and report PAUSE.
            with self.tx() as db: db.execute("UPDATE native_protection SET alert='APTS_EXIT_SUBMITTING' WHERE client_id=?",(client_id,))
            try: reply=self.executor.submit(params,purpose='EXIT',position_id=row['position_id'])
            except Exception:
                with self.tx() as db: db.execute("UPDATE native_protection SET alert='APTS_EXIT_PAUSED_KEEP_NATIVE_PROTECTION' WHERE client_id=?",(client_id,))
                raise
            with self.tx() as db: db.execute("UPDATE native_protection SET alert='APTS_EXIT_PENDING_RECONCILIATION' WHERE client_id=?",(client_id,))
            return {'protection':self.snapshot(client_id),'exit':reply}
        return self.update(client_id,decision.stop_price,decision.limit_price,owned_quantity,exchange_info)

    def disconnect(self):
        with self.tx() as db: db.execute("UPDATE native_protection SET state='UNKNOWN',alert='CONNECTION_LOST_KEEP_NATIVE_ORDER' WHERE state IN ('NEW','PARTIALLY_FILLED','SUBMITTING')")

    def entry_safe(self):
        rows=self.snapshot()
        for row in rows:
            if row['state'] in ACTIVE|{'FILLED'} and not row['alert']: continue
            if row['state']=='CANCELED' and any(next_row['previous_id']==row['client_id'] and next_row['state'] in ACTIVE|{'FILLED'} and not next_row['alert'] for next_row in rows): continue
            return False
        return True
