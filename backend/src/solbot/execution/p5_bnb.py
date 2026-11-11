"""Actual fee-asset accounting and guarded BNB refill coordinator."""
from contextlib import closing,contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3
from solbot.mathematics.decimal_utils import D,ceil_step

def account_fill(balances,*,side,base,quote,quantity,quote_quantity,commission,commission_asset):
    """Pure double-entry asset deltas; caller commits deltas with unique trade ID."""
    if side not in ('BUY','SELL') or base==quote: raise ValueError('invalid fill identity')
    result={k:D(v,nonnegative=True) for k,v in balances.items()}
    qty,cost,fee=D(quantity,positive=True),D(quote_quantity,positive=True),D(commission,nonnegative=True)
    if fee and not commission_asset: raise ValueError('commission asset required')
    delta={base:qty if side=='BUY' else -qty,quote:-cost if side=='BUY' else cost}
    if fee: delta[commission_asset]=delta.get(commission_asset,D('0'))-fee
    for asset,change in delta.items():
        result[asset]=result.get(asset,D('0'))+change
        if result[asset]<0: raise ValueError('fill exceeds attributed robot asset balance')
    return {k:str(v) for k,v in result.items()}

def commission_value(commission,asset,prices):
    fee=D(commission,nonnegative=True)
    if not fee: return D('0')
    if asset=='USDT': return fee
    if asset not in prices: raise ValueError('actual fee asset valuation unavailable')
    return fee*D(prices[asset],positive=True)

class BNBExecution:
    def __init__(self,path,executor,gate,guardian):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.executor,self.gate,self.guardian=executor,gate,guardian
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bnb_execution(intent_id TEXT PRIMARY KEY,client_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,state TEXT NOT NULL,executed TEXT NOT NULL,exchange_id TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS bnb_trade_events(trade_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS bnb_robot_balances(asset TEXT PRIMARY KEY,quantity TEXT NOT NULL)')

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None,timeout=10)) as db:
            db.row_factory=sqlite3.Row; db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try: yield db;db.commit()
            except BaseException: db.rollback();raise

    def refill(self,intent_id,*,robot_bnb,free_bnb,price,free_usdt,robot_cash,step,min_notional,hard_locked=False):
        self.gate.require('TRADE',account_id='SUI-live',symbol='BNBUSDT')
        owned,available,px=D(robot_bnb,nonnegative=True),D(free_bnb,nonnegative=True),D(price,positive=True)
        if hard_locked or min(owned,available)*px>=3: raise ValueError('refill not authorized or not needed')
        qty=ceil_step(max(D('10'),D(min_notional,positive=True))/px,D(step,positive=True)); amount=qty*px
        if owned*px+amount>20 or amount>D('10')+px*D(step): raise ValueError('BNB 10/20 limit')
        if amount*D('1.001')>min(D(free_usdt,nonnegative=True),D(robot_cash,nonnegative=True)): raise ValueError('insufficient allocated/free cash')
        client='p5b-'+hashlib.sha256(intent_id.encode()).hexdigest()[:28]
        params={'symbol':'BNBUSDT','side':'BUY','type':'LIMIT','timeInForce':'GTC','quantity':str(qty),'price':str(px),'newClientOrderId':client}
        body=json.dumps(params,sort_keys=True)
        with self.tx() as db:
            previous=db.execute('SELECT * FROM bnb_execution WHERE intent_id=?',(intent_id,)).fetchone()
            if previous:
                if previous['payload']!=body: raise ValueError('refill intent conflict')
                return dict(previous)
            if db.execute("SELECT 1 FROM bnb_execution WHERE state NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED')").fetchone(): raise ValueError('pending or unknown BNB order')
            if db.execute("SELECT count(*) FROM bnb_execution WHERE state='REJECTED'").fetchone()[0]>=3: raise ValueError('bounded refill failure limit')
            approval=self.guardian(params)
            if not isinstance(approval,dict) or approval.get('approved') is not True or approval.get('intent_hash')!=hashlib.sha256(body.encode()).hexdigest(): raise ValueError('Guardian binding required')
            db.execute("INSERT INTO bnb_execution VALUES(?,?,?,'SUBMITTING','0',NULL)",(intent_id,client,body))
        try:
            result=self.executor.submit(params,purpose='BNB')
            self.order_event(result)
        except Exception:
            with self.tx() as db: db.execute("UPDATE bnb_execution SET state='UNKNOWN' WHERE intent_id=? AND state='SUBMITTING'",(intent_id,))
            raise
        return self.snapshot(intent_id)

    def snapshot(self,intent_id):
        with self.tx() as db:
            row=db.execute('SELECT * FROM bnb_execution WHERE intent_id=?',(intent_id,)).fetchone()
            if not row: raise ValueError('unknown refill')
            return dict(row)

    def order_event(self,event):
        with self.tx() as db:
            row=db.execute('SELECT * FROM bnb_execution WHERE client_id=?',(event.get('clientOrderId'),)).fetchone()
            if not row or event.get('symbol')!='BNBUSDT' or event.get('side','BUY')!='BUY': raise ValueError('refill event identity')
            quantity=D(event.get('executedQty','0'),nonnegative=True);status=event.get('status')
            if status not in ('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','UNKNOWN'): raise ValueError('refill status')
            if quantity>D(json.loads(row['payload'])['quantity']): raise ValueError('excess BNB execution')
            order_id=event.get('orderId')
            if status in ('NEW','PARTIALLY_FILLED','FILLED') and order_id is None: raise ValueError('exchange order ID required')
            if row['exchange_id'] is not None and order_id is not None and str(order_id)!=row['exchange_id']: raise ValueError('exchange order identity changed')
            if status=='FILLED' and quantity!=D(json.loads(row['payload'])['quantity']): raise ValueError('filled quantity mismatch')
            if quantity<D(row['executed']): return
            terminal=('FILLED','CANCELED','EXPIRED','REJECTED')
            if row['state'] in terminal and status not in terminal:return
            db.execute('UPDATE bnb_execution SET state=?,executed=?,exchange_id=? WHERE intent_id=?',(status,str(quantity),str(order_id) if order_id is not None else row['exchange_id'],row['intent_id']))

    def record_trade(self,trade_id,event,initial_balances=None):
        """Trusted reconciler must supply exchange trade identity, not local ACK guesses."""
        body=json.dumps(event,sort_keys=True)
        with self.tx() as db:
            old=db.execute('SELECT payload FROM bnb_trade_events WHERE trade_id=?',(trade_id,)).fetchone()
            if old:
                if old[0]!=body: raise ValueError('trade ID conflict')
                return dict(db.execute('SELECT asset,quantity FROM bnb_robot_balances'))
            known=db.execute('SELECT payload FROM bnb_execution WHERE client_id=?',(event['clientOrderId'],)).fetchone()
            if not known: raise ValueError('trade without owned refill order')
            order=json.loads(known[0])
            recorded=sum((D(json.loads(r[0])['qty']) for r in db.execute('SELECT payload FROM bnb_trade_events') if json.loads(r[0])['clientOrderId']==event['clientOrderId']),D('0'))
            qty,quote=D(event['qty'],positive=True),D(event['quoteQty'],positive=True)
            if recorded+qty>D(order['quantity']) or quote>qty*D(order['price']): raise ValueError('fill violates BNB order quantity or limit')
            balances=dict(db.execute('SELECT asset,quantity FROM bnb_robot_balances'))
            if not balances:
                if initial_balances is None: raise ValueError('reconciled opening balances required')
                balances=initial_balances
            result=account_fill(balances,side='BUY',base='BNB',quote='USDT',quantity=event['qty'],quote_quantity=event['quoteQty'],commission=event['commission'],commission_asset=event['commissionAsset'])
            db.execute('INSERT INTO bnb_trade_events VALUES(?,?)',(trade_id,body))
            for asset,qty in result.items(): db.execute('INSERT INTO bnb_robot_balances VALUES(?,?) ON CONFLICT(asset) DO UPDATE SET quantity=excluded.quantity',(asset,qty))
            return result
