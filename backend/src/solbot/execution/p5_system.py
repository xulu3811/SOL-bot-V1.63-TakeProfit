"""Post-settlement orchestration: reconcile real inventory, then protect owned fills.

No client or credentials are constructed here. Production authorization is the
same engine gate; a persisted display state never enables private reads.
"""
from contextlib import contextmanager,closing
from pathlib import Path
import json
import sqlite3
import uuid
from solbot.accounts.allocation_ledger import now_ms
from solbot.mathematics.decimal_utils import D,floor_step,ceil_step
from solbot.mathematics.protective_stop import stop_limit_price
from solbot.exchange.account_reader import AccountReader
from .p5_protection import ProtectionManager,ACTIVE,TERMINAL
from .p5_bnb import BNBExecution

class ExecutionSystem:
    def __init__(self,path,engine,protection,reader,guardian,gate,exchange_info_provider,price_provider,*,clock=now_ms):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.engine,self.protection,self.reader,self.guardian,self.gate=engine,protection,reader,guardian,gate
        self.exchange_info_provider,self.price_provider,self.clock=exchange_info_provider,price_provider,clock
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS position_protection_status(position_id TEXT PRIMARY KEY,state TEXT NOT NULL,payload TEXT NOT NULL,updated_at INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS position_protection_audit(event_id TEXT PRIMARY KEY,position_id TEXT NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL)')
        engine.on_position=self.on_position

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None,timeout=10)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def _record(self,position_id,state,**details):
        value={'position_id':position_id,'state':state,**details};body=json.dumps(value,sort_keys=True)
        with self.tx() as db:
            db.execute('INSERT INTO position_protection_status VALUES(?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at',(position_id,state,body,self.clock()))
            db.execute('INSERT INTO position_protection_audit VALUES(?,?,?,?)',(str(uuid.uuid4()),position_id,body,self.clock()))
        return value

    def status(self,position_id):
        with self.tx() as db:
            row=db.execute('SELECT payload FROM position_protection_status WHERE position_id=?',(position_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def on_position(self,position_id):
        """Engine invokes only after its ENTRY fill outbox is durably settled."""
        try:
            self.gate.require('PRIVATE_READ',account_id='SUI-live',symbol='SUIUSDT')
            with self.engine.store.tx() as db:
                orders=[dict(r) for r in db.execute('SELECT * FROM orders')]
                outbox_unknown=db.execute('SELECT 1 FROM outbox WHERE delivered=0').fetchone() is not None
            unknown=outbox_unknown or any(r['state'] in ('UNKNOWN','SUBMITTING','CREATED','RISK_APPROVED','CANCEL_PENDING') for r in orders)
            by_id={r['client_id']:r for r in orders}
            manual=[]
            known=[]
            for symbol in ('SUIUSDT','BNBUSDT'):
                for order in self.reader.open_orders(symbol):
                    local=by_id.get(order.get('clientOrderId'))
                    if local is None:manual.append(order.get('orderId'));unknown=True
                    elif str(local['exchange_id'])!=str(order.get('orderId')):unknown=True
                    else: known.append({'client_id':local['client_id'],'purpose':local['purpose'],'symbol':symbol,'status':order['status'],'remaining_quantity':str(D(order.get('origQty',json.loads(local['payload'])['quantity']))-D(order.get('executedQty','0')))})
            account=self.reader.account()
            balances=AccountReader.normalize_account(account)
            outcome=self.guardian.reconcile({'event_id':'protect-reconcile:'+str(uuid.uuid4()),'account_id':'SUI-live','balances':balances,'observed_at':self.clock(),'unknown_orders':unknown,'manual_orders':manual,'known_orders':known})
            if outcome!='RECONCILED' and not (isinstance(outcome,dict) and outcome.get('status')=='RECONCILED'):
                raise ValueError('ACCOUNT_RECONCILIATION_REQUIRED')
            position=self.guardian.position(position_id)
            if position.get('intent_id',position_id)!=position_id or position.get('asset')!='SUI':raise ValueError('POSITION_IDENTITY_MISMATCH')
            quantity=D(position['quantity'],nonnegative=True)
            if quantity==0:return self._record(position_id,'CLOSED',quantity='0')
            rows=[r for r in self.protection.snapshot() if r['position_id']==position_id]
            if any(r['state'] not in ACTIVE|TERMINAL for r in rows):raise ValueError('EXISTING_PROTECTION_UNKNOWN')
            covered=sum((D(r['quantity'])-D(r['executed']) for r in rows if r['state'] in ACTIVE),D('0'))
            if covered>quantity:raise ValueError('PROTECTION_EXCEEDS_OWNED_POSITION')
            if covered==quantity:return self._record(position_id,'PROTECTED',quantity=str(quantity),covered=str(covered))
            info=self.exchange_info_provider('SUIUSDT')
            filters={f['filterType']:f for f in info['filters']}
            step=D(filters['LOT_SIZE']['stepSize'],positive=True);tick=D(filters['PRICE_FILTER']['tickSize'],positive=True)
            remainder=quantity-covered;tradable=floor_step(remainder,step);dust=remainder-tradable
            if tradable==0:raise ValueError('PROTECTION_QUANTITY_BELOW_LOT_STEP')
            protection=position['protection'];protection=json.loads(protection) if isinstance(protection,str) else protection
            stop=ceil_step(protection['stop_price'],tick)
            market=self.price_provider('SUIUSDT')
            if not isinstance(market,dict) or type(market.get('at')) is not int or not 0<=self.clock()-market['at']<=2000:
                raise ValueError('PROTECTION_MARKET_STALE')
            if D(market['bid'],positive=True)<=stop:raise ValueError('INITIAL_STOP_ALREADY_BREACHED')
            # Each partial-fill tranche uses cumulative owned size in its ID.
            # This allows equal-sized consecutive fills without reusing an old order.
            result=self.protection.install(position_id,'SUIUSDT',str(tradable),position.get('robot_owned_quantity',str(quantity)),str(stop),str(stop_limit_price(stop,tick)),info,generation=str(quantity))
            if result['state'] not in ACTIVE:raise ValueError('NATIVE_PROTECTION_NOT_ACKNOWLEDGED')
            if dust:
                self.gate.pause()
                return self._record(position_id,'PARTIAL_PROTECTION',quantity=str(quantity),covered=str(covered+tradable),dust=str(dust),client_id=result['client_id'],alert='UNPROTECTED_DUST')
            return self._record(position_id,'PROTECTED',quantity=str(quantity),covered=str(covered+tradable),client_id=result['client_id'])
        except Exception as exc:
            self.gate.pause()
            # Do not copy arbitrary API exception text (which may contain secrets).
            return self._record(position_id,'PAUSED',alert=type(exc).__name__,protection_confirmed=False)

def build_execution_system(path,engine,reader,guardian,gate,exchange_info_provider,price_provider,*,clock=now_ms):
    root=Path(path);root.mkdir(parents=True,exist_ok=True)
    protection=ProtectionManager(root/'native-protection.sqlite',engine,gate)
    system=ExecutionSystem(root/'protection-system.sqlite',engine,protection,reader,guardian,gate,exchange_info_provider,price_provider,clock=clock)
    system.bnb=BNBExecution(root/'bnb-execution.sqlite',engine,gate,guardian.bnb_candidate)
    return system
