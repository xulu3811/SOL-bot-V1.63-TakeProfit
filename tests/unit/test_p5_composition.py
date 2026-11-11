import unittest,tempfile,json,hashlib
from pathlib import Path
from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.p5_bridge import GuardianBridge
from solbot.risk.permission_engine import TradeIntent
from solbot.execution.p5_store import OrderStore
from solbot.execution.p5_authorization import LiveSession,MockAuthorization
from solbot.execution.p5_engine import ExecutionEngine
from solbot.exchange.binance_private import BinancePrivate,MockTransport,Credentials
from solbot.exchange.order_client import OrderClient,EngineClient
from solbot.exchange.account_reader import AccountReader

class ComposedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.now=1800000000000
        self.ledger=AllocationLedger(self.root/'risk.sqlite');self.bridge=GuardianBridge(self.ledger,clock=lambda:self.now)
        BalanceReconciliation(self.ledger).reconcile('initial',{'USDT':{'free':'2000','locked':'0'}},self.now,at=self.now)
        DrawdownEngine(self.ledger).mark('mark',{},self.now,at=self.now)
        self.intent=TradeIntent('trade','sig','SUI-live','SUIUSDT','T1','p2-research-1','1','700','.992',self.now,self.now+60000,self.now,self.now)
        self.gate=LiveSession(environment='mock',clock=lambda:self.now)
        self.gate.request_on(MockAuthorization(),lambda s:{k:True for k in ('account','permissions','orders','balances','protection','guardian','filters')})
    def test_full_signed_adapter_guardian_entry_path(self):
        key='sb5-'+hashlib.sha256(b'SUI-live|trade').hexdigest()[:28]
        transport=MockTransport([(200,{}, {'symbol':'SUIUSDT','side':'BUY','clientOrderId':key,'orderId':10,'status':'NEW','executedQty':'0','cummulativeQuoteQty':'0'})])
        private=BinancePrivate(lambda:Credentials('mock-key','mock-secret'),transport,self.gate,clock=lambda:self.now)
        private.sync_time(self.now,sent_at=self.now,received_at=self.now)
        client=EngineClient(OrderClient(private,self.bridge.verify_order),AccountReader(private),self.bridge.bind_order)
        engine=ExecutionEngine(OrderStore(self.root/'orders.sqlite'),client,self.bridge,self.gate,filters=lambda *a:None,protection_ready=lambda s:True,clock=lambda:self.now)
        self.assertEqual(engine.submit_entry(self.intent)['state'],'NEW')
        self.assertEqual(engine.submit_entry(self.intent)['state'],'NEW');self.assertEqual(len(transport.calls),1)
    def test_guardian_permission_is_bound_expiring_and_single_use(self):
        from dataclasses import asdict
        self.bridge.approve(asdict(self.intent))
        key='sb5-'+hashlib.sha256(b'SUI-live|trade').hexdigest()[:28]
        params={'symbol':'SUIUSDT','side':'BUY','price':'1','quantity':'700','newClientOrderId':key}
        token=self.bridge.bind_order(params)
        self.assertFalse(self.bridge.verify_order(dict(params,quantity='699'),token))
        self.assertTrue(self.bridge.verify_order(params,token));self.assertFalse(self.bridge.verify_order(params,token))
        token=self.bridge.bind_order(params);self.now+=1001;self.assertFalse(self.bridge.verify_order(params,token))
    def test_actual_base_fee_reduces_sellable_inventory(self):
        from dataclasses import asdict
        self.bridge.approve(asdict(self.intent))
        msg={'kind':'FILL','event_id':'fill:1','intent_id':'trade','purpose':'ENTRY','trade':{'qty':'100','price':'1','commission':'.1','commissionAsset':'SUI'}}
        self.bridge.settle(msg);self.bridge.settle(msg)
        self.assertEqual(self.bridge.position('trade')['quantity'],'99.9')
        self.assertEqual(self.ledger.snapshot()['cash'],'1900')
    def test_real_quote_fee_above_estimate_is_booked_then_paused(self):
        from dataclasses import asdict
        self.bridge.approve(asdict(self.intent))
        self.bridge.settle({'kind':'FILL','event_id':'fee','intent_id':'trade','purpose':'ENTRY','trade':{'qty':'100','price':'1','commission':'.2','commissionAsset':'USDT'}})
        s=self.ledger.snapshot();self.assertEqual(s['cash'],'1899.8');self.assertTrue(s['recovery_required'])
    def test_orders_backup_restore(self):
        store=OrderStore(self.root/'orders.sqlite');result=store.backup(self.root/'backup.sqlite');self.assertTrue(Path(result['restore']).exists())
    def test_filter_rejection_tuple_cannot_be_ignored(self):
        engine=ExecutionEngine(OrderStore(self.root/'orders.sqlite'),None,self.bridge,self.gate,filters=lambda *a:('LOT_SIZE',),protection_ready=lambda s:True,clock=lambda:self.now)
        with self.assertRaises(PermissionError): engine.submit_entry(self.intent)
        self.assertEqual(self.ledger.snapshot()['reservations'],[])

if __name__=='__main__':unittest.main()
