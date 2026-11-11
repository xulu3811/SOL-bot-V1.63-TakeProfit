"""Automatic ENTRY-fill -> actual account reconciliation -> native protection."""
import unittest
import tempfile
from pathlib import Path
from decimal import Decimal
from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.p5_bridge import GuardianBridge
from solbot.risk.permission_engine import TradeIntent
from solbot.execution.p5_authorization import LiveSession,MockAuthorization
from solbot.execution.p5_engine import ExecutionEngine
from solbot.execution.p5_store import OrderStore
from solbot.execution.p5_system import build_execution_system

INFO={'symbol':'SUIUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['STOP_LOSS_LIMIT'],'filters':[{'filterType':'PRICE_FILTER','tickSize':'.001','minPrice':'.001','maxPrice':'1000'},{'filterType':'LOT_SIZE','stepSize':'.1','minQty':'.1','maxQty':'100000'}]}

class FakeExchange:
    def __init__(self):self.calls=[];self.fail=False;self.reads=0;self.cash='1299.3';self.quantity='700'
    def place(self,**params):
        self.calls.append(params)
        if params['side']=='BUY':
            return {'symbol':'SUIUSDT','side':'BUY','clientOrderId':params['newClientOrderId'],'orderId':1,'status':'FILLED','executedQty':'700','cummulativeQuoteQty':'700','fills':[{'tradeId':1,'qty':'700','price':'1','commission':'.7','commissionAsset':'USDT'}]}
        if self.fail:raise TimeoutError('mock failure')
        return {'symbol':'SUIUSDT','side':'SELL','clientOrderId':params['newClientOrderId'],'orderId':len(self.calls),'status':'NEW','executedQty':'0','cummulativeQuoteQty':'0'}
    def account(self):
        self.reads+=1
        return {'canTrade':True,'permissions':['SPOT'],'balances':[{'asset':'USDT','free':self.cash,'locked':'0'},{'asset':'SUI','free':self.quantity,'locked':'0'}]}
    def open_orders(self,symbol):return []

class AutomaticProtectionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.now=1800000000000
        self.ledger=AllocationLedger(self.root/'ledger.sqlite');self.guardian=GuardianBridge(self.ledger,clock=lambda:self.now)
        BalanceReconciliation(self.ledger).reconcile('initial',{'USDT':{'free':'2000','locked':'0'}},self.now,at=self.now)
        DrawdownEngine(self.ledger).mark('initial-mark',{},self.now,at=self.now)
        self.gate=LiveSession(environment='mock',clock=lambda:self.now)
        self.gate.request_on(MockAuthorization(),lambda s:{k:True for k in ('account','permissions','orders','balances','protection','guardian','filters')})
        self.exchange=FakeExchange()
        self.engine=ExecutionEngine(OrderStore(self.root/'orders.sqlite'),self.exchange,self.guardian,self.gate,filters=lambda *a:None,protection_ready=lambda s:True,clock=lambda:self.now)
        self.system=build_execution_system(self.root/'system',self.engine,self.exchange,self.guardian,self.gate,lambda s:INFO,lambda s:{'bid':'1','at':self.now},clock=lambda:self.now)
        self.intent=TradeIntent('entry','sig','SUI-live','SUIUSDT','T1','p2-research-1','1','700','.992',self.now,self.now+60000,self.now,self.now)
    def test_entry_fill_automatically_installs_native_protection(self):
        result=self.engine.submit_entry(self.intent)
        self.assertEqual(result['state'],'FILLED');self.assertEqual(len(self.exchange.calls),2)
        self.assertEqual(self.exchange.calls[1]['type'],'STOP_LOSS_LIMIT')
        self.assertEqual(self.system.status('entry')['state'],'PROTECTED')
        self.assertEqual(self.system.protection.snapshot()[0]['position_id'],'entry')
    def test_native_failure_keeps_fill_and_pauses_with_durable_alarm(self):
        self.exchange.fail=True
        result=self.engine.submit_entry(self.intent)
        self.assertEqual(result['state'],'FILLED');self.assertEqual(self.gate.state,'PAUSED')
        self.assertEqual(self.system.status('entry')['state'],'PAUSED')
        self.assertEqual(self.system.protection.snapshot()[0]['state'],'UNKNOWN')
        self.assertEqual(Decimal(self.guardian.position('entry')['quantity']),700)
    def test_off_callback_never_reads_private_account(self):
        self.gate.off();self.system.on_position('entry')
        self.assertEqual(self.exchange.reads,0);self.assertFalse(self.exchange.calls)
    def test_repeat_callback_does_not_duplicate_protection(self):
        self.engine.submit_entry(self.intent);self.system.on_position('entry')
        self.assertEqual(len(self.exchange.calls),2)
    def test_stale_market_pauses_without_fake_protection(self):
        self.system.price_provider=lambda s:{'bid':'1','at':self.now-2001}
        self.engine.submit_entry(self.intent)
        self.assertEqual(self.system.status('entry')['state'],'PAUSED');self.assertEqual(len(self.exchange.calls),1)
    def test_failed_balance_reconciliation_does_not_protect_manual_assets(self):
        self.exchange.quantity='600'
        self.engine.submit_entry(self.intent)
        self.assertEqual(self.system.status('entry')['state'],'PAUSED');self.assertEqual(len(self.exchange.calls),1)

if __name__=='__main__':unittest.main()
