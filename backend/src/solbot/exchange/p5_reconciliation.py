"""Private snapshot handshake. Ownership requires durable order and trade identity."""
import json
import time
import uuid
from solbot.mathematics.decimal_utils import D
from solbot.storage.models import resolve_live_account
from .account_reader import AccountReader
from .commission_service import CommissionService

class PrivateHandshake:
    def __init__(self,reader,store,guardian,*,recover,protection_checker,filters_checker,clock=None,account_id=None,symbols=None):
        self.reader,self.store,self.guardian=reader,store,guardian
        self.recover,self.protection_checker,self.filters_checker=recover,protection_checker,filters_checker
        self.clock=clock or (lambda:time.time_ns()//1000000)
        self.account_id=account_id
        self.symbols=tuple(symbols) if symbols is not None else None
        self.last_evidence=None

    def __call__(self,session):
        account_id=self.account_id or getattr(session,'account_id','SUI-live')
        session.require('PRIVATE_READ',account_id)
        try:
            return self._check(session)
        except BaseException:
            # Temporary read session is shut down on any handshake failure.
            self.reader.client.close()
            raise

    def _check(self,session):
        account_id,primary_symbol,_=resolve_live_account(self.account_id or getattr(session,'account_id','SUI-live'))
        symbols=self.symbols or (primary_symbol,'BNBUSDT')
        self.recover()
        account=self.reader.account(); balances=self.reader.normalize_account(account)
        permissions=self.reader.validate_permissions(self.reader.api_permissions())
        with self.store.tx() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM orders')]
            pending_outbox=db.execute('SELECT 1 FROM outbox WHERE delivered=0').fetchone() is not None
        by_client={r['client_id']:r for r in rows}
        unknown=pending_outbox or any(r['state'] in ('UNKNOWN','SUBMITTING','CREATED','RISK_APPROVED') for r in rows)
        known=[]; manual=[]; commissions={}
        for symbol in symbols:
            for order in self.reader.open_orders(symbol):
                local=by_client.get(order['clientOrderId'])
                if local is None:
                    manual.append({'symbol':symbol,'orderId':order['orderId'],'clientOrderId':order['clientOrderId'],'status':order['status']})
                    continue
                payload=json.loads(local['payload'])
                if payload.get('symbol')!=symbol or str(local['exchange_id'])!=str(order['orderId']): unknown=True
            commissions[symbol]=CommissionService(self.reader.client).rates(symbol)
        for row in rows:
            payload=json.loads(row['payload']); symbol=payload['symbol']
            if row['exchange_id'] is None:
                unknown=True; continue
            remote=self.reader.order(row['client_id'],symbol)
            if remote.get('clientOrderId')!=row['client_id'] or str(remote.get('orderId'))!=str(row['exchange_id']) or remote.get('symbol')!=symbol:
                unknown=True; continue
            if remote.get('side')!=payload.get('side','BUY') or remote.get('status')!=row['state']: unknown=True
            # Explicit pagination prevents a page limit from masquerading as full fill history.
            trades=[]; from_id=0
            for page in range(100):
                batch=self.reader.trades(symbol,order_id=remote['orderId'],from_id=from_id)
                if any(str(t['orderId'])!=str(remote['orderId']) for t in batch): raise ValueError('trade/order identity mismatch')
                trades.extend(batch)
                if len(batch)<1000: break
                next_id=max(int(t['id']) for t in batch)+1
                if from_id is not None and next_id<=from_id: raise ValueError('trade pagination failed to advance')
                from_id=next_id
            else: raise ValueError('bounded trade history incomplete')
            if len({str(t['id']) for t in trades})!=len(trades): raise ValueError('duplicate trade page')
            total=sum((D(t['qty'],nonnegative=True) for t in trades),D('0'))
            if total!=D(remote['executedQty']) or total!=D(row['cumulative']): unknown=True
            with self.store.tx() as db:
                local_rows={r['trade_key']:json.loads(r['payload']) for r in db.execute('SELECT trade_key,payload FROM fills WHERE client_id=?',(row['client_id'],))}
                local_trades=set(local_rows)
            expected={symbol+':'+str(remote['orderId'])+':'+str(t['id']) for t in trades}
            if local_trades!=expected: unknown=True
            for trade in trades:
                local=local_rows.get(symbol+':'+str(remote['orderId'])+':'+str(trade['id']))
                if not local: continue
                if local.get('commissionAsset')!=trade.get('commissionAsset') or any(D(local[k])!=D(trade[k]) for k in ('qty','price','commission')): unknown=True
            known.append({'client_id':row['client_id'],'intent_id':row['intent_id'],'purpose':row['purpose'],'symbol':symbol,'order_id':str(remote['orderId']),'status':remote['status'],'executed_quantity':str(total),'remaining_quantity':str(D(remote['origQty'])-total),'order_type':remote['type']})
        # Concurrent manual activity during a multi-request snapshot invalidates it.
        final=self.reader.account(); final_balances=self.reader.normalize_account(final)
        if final_balances!=balances or final.get('updateTime')!=account.get('updateTime'): unknown=True
        initial=not rows
        enough=not initial or D(balances.get('USDT',{}).get('free','0'))>=D('2000')
        # Preserve manual order evidence; do not silently consume its locked funds.
        unknown=unknown or bool(manual)
        protection=self.protection_checker(known) is True
        filters=self.filters_checker() is True
        observed=self.clock()
        event={'event_id':'p5-reconcile:'+str(uuid.uuid4()),'account_id':account_id,'balances':balances,'observed_at':observed,'unknown_orders':unknown,'manual_orders':manual,'known_orders':known,'commissions':commissions,'protection_verified':protection}
        outcome=self.guardian.reconcile(event)
        reconciled=outcome=='RECONCILED' or (isinstance(outcome,dict) and outcome.get('status')=='RECONCILED')
        evidence={'account':True,'permissions':permissions,'orders':not unknown,'balances':enough and reconciled,'protection':protection,'guardian':reconciled,'filters':filters}
        self.last_evidence={'snapshot':event,'checks':evidence}
        if not all(v is True for v in evidence.values()): self.reader.client.close()
        return evidence
