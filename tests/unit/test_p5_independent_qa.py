"""Independent P5 QA uses only synthetic data and socket-free mock transport."""
from pathlib import Path
import tempfile
import unittest
from solbot.exchange.binance_private import (BinancePrivate, MockTransport, HTTPTransport,
    Credentials, AuthorizationDenied, ExchangeError)
from solbot.execution.p5_protection import ProtectionManager, ProtectionError

class AllowMockGate:
    def __init__(self): self.enabled=True
    def require(self,*args,**kwargs):
        if not self.enabled: raise AuthorizationDenied('OFF')

class IndependentAdapterQA(unittest.TestCase):
    def client(self,responses):
        self.loads=[]; self.gate=AllowMockGate(); self.transport=MockTransport(responses)
        def loader(): self.loads.append(1); return Credentials('synthetic-key','synthetic-secret')
        c=BinancePrivate(loader,self.transport,self.gate,clock=lambda:10000)
        c.sync_time(10000,sent_at=10000,received_at=10000)
        return c
    def test_closed_default_gate_never_loads_credentials(self):
        calls=[]; c=BinancePrivate(lambda:calls.append('loaded'))
        with self.assertRaises(AuthorizationDenied): c.request('GET','/api/v3/account')
        self.assertEqual(calls,[])
    def test_forged_allow_gate_cannot_open_production_transport(self):
        calls=[]; c=BinancePrivate(lambda:calls.append('loaded'),HTTPTransport(),AllowMockGate())
        with self.assertRaises(AuthorizationDenied): c.request('GET','/api/v3/account')
        self.assertEqual(calls,[])
    def test_revocation_during_loading_prevents_transport(self):
        c=self.client([(200,{}, {'ok':True})])
        def loader(): self.gate.enabled=False; return Credentials('synthetic-key','synthetic-secret')
        c.loader=loader
        with self.assertRaises(AuthorizationDenied): c.request('GET','/api/v3/account')
        self.assertEqual(self.transport.calls,[])
    def test_timeout_has_no_blind_resubmit(self):
        c=self.client([TimeoutError('synthetic-secret'),(200,{}, {'status':'NEW'})])
        with self.assertRaises(ExchangeError) as ctx: c.request('POST','/api/v3/order',{'symbol':'SUIUSDT'},scope='TRADE')
        self.assertTrue(ctx.exception.unknown); self.assertNotIn('synthetic-secret',str(ctx.exception))
        self.assertEqual(len(self.transport.calls),1)
    def test_error_text_is_not_exposed(self):
        c=self.client([(400,{}, {'code':-2010,'msg':'synthetic-secret'})])
        with self.assertRaises(ExchangeError) as ctx: c.request('POST','/api/v3/order',{'symbol':'SUIUSDT'},scope='TRADE')
        self.assertNotIn('synthetic-secret',str(ctx.exception))
    def test_malformed_error_code_cannot_inject_secret_into_logs(self):
        c=self.client([(400,{}, {'code':'synthetic-secret','msg':'other'})])
        with self.assertRaises(ExchangeError) as ctx: c.request('GET','/api/v3/account')
        self.assertNotIn('synthetic-secret',str(ctx.exception))
    def test_rate_limit_blocks_next_request_before_loading(self):
        c=self.client([(429,{'Retry-After':'15'}, {'code':-1003})])
        with self.assertRaises(ExchangeError): c.request('GET','/api/v3/account')
        with self.assertRaises(ExchangeError): c.request('GET','/api/v3/account')
        self.assertEqual(len(self.loads),1); self.assertEqual(len(self.transport.calls),1)
    def test_wrong_symbol_and_transfer_endpoint_denied(self):
        c=self.client([])
        with self.assertRaises(AuthorizationDenied): c.request('GET','/api/v3/order',{'symbol':'SOLUSDT'})
        with self.assertRaises(AuthorizationDenied): c.request('POST','/sapi/v1/asset/transfer',{},scope='TRADE')
        self.assertEqual(self.loads,[])

INFO={'symbol':'SUIUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['STOP_LOSS_LIMIT'],
      'filters':[{'filterType':'PRICE_FILTER','tickSize':'.001','minPrice':'.001','maxPrice':'10000'},
                 {'filterType':'LOT_SIZE','stepSize':'1','minQty':'1','maxQty':'100000'}]}

class MockProtectionExecutor:
    def __init__(self): self.submits=[]; self.cancels=[]; self.fail=False
    def submit(self,params,purpose,**kwargs):
        self.submits.append(dict(params))
        if self.fail: raise TimeoutError('unknown result')
        return {'clientOrderId':params['newClientOrderId'],'symbol':params['symbol'],'side':'SELL','status':'NEW','executedQty':'0','orderId':1}
    def cancel(self,symbol,client_id):
        self.cancels.append(client_id)
        return {'clientOrderId':client_id,'symbol':symbol,'side':'SELL','status':'CANCELED','executedQty':'0','orderId':1}

class IndependentProtectionQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.gate=AllowMockGate(); self.executor=MockProtectionExecutor()
        self.m=ProtectionManager(Path(self.tmp.name)/'protect.sqlite',self.executor,self.gate)
    def install(self): return self.m.install('position','SUIUSDT','100','100','.992','.99',INFO)
    def test_off_does_not_cancel_confirmed_native_order(self):
        row=self.install(); self.gate.enabled=False
        with self.assertRaises(AuthorizationDenied): self.m.update(row['client_id'],'1.01','1','100',INFO)
        self.assertEqual(self.executor.cancels,[])
        self.assertEqual(self.m.snapshot(row['client_id'])['state'],'NEW')
    def test_timeout_persists_unknown_and_does_not_resubmit(self):
        self.executor.fail=True
        with self.assertRaises(TimeoutError): self.install()
        row=self.install()
        self.assertEqual(row['state'],'UNKNOWN'); self.assertEqual(len(self.executor.submits),1)
    def test_raising_stop_does_not_cancel_before_replacement_funded(self):
        row=self.install()
        with self.assertRaises(ProtectionError): self.m.update(row['client_id'],'1.01','1','100',INFO)
        self.assertEqual(self.executor.cancels,[]); self.assertEqual(len(self.executor.submits),1)
    def test_old_new_event_cannot_reopen_filled_protection(self):
        row=self.install(); cid=row['client_id']
        event={'clientOrderId':cid,'symbol':'SUIUSDT','side':'SELL','status':'FILLED','executedQty':'100','orderId':1}
        self.m.observe('fill',event); self.m.observe('fill',event)
        self.m.observe('old',dict(event,status='NEW',executedQty='0'))
        final=self.m.snapshot(cid)
        self.assertEqual(final['state'],'FILLED'); self.assertEqual(final['executed'],'100')
    def test_exchange_identity_mismatch_is_rejected(self):
        row=self.install()
        with self.assertRaises(ProtectionError): self.m.observe('bad',{'clientOrderId':row['client_id'],'symbol':'SOLUSDT','status':'NEW','orderId':1})
    def test_protection_exchange_order_id_cannot_change(self):
        row=self.install()
        with self.assertRaises(ProtectionError): self.m.observe('changed-id',{'clientOrderId':row['client_id'],'symbol':'SUIUSDT','status':'NEW','orderId':99})
    def test_stop_cannot_be_lowered(self):
        row=self.install()
        with self.assertRaises(ProtectionError): self.m.update(row['client_id'],'.98','.97','100',INFO)
        self.assertEqual(self.executor.cancels,[])

from solbot.execution.p5_engine import ExecutionEngine
from solbot.execution.p5_store import OrderStore
from solbot.risk.permission_engine import TradeIntent

class EngineGate:
    def require(self,*args,**kwargs): pass
    def pause(self): pass

class MockGuardian:
    def __init__(self): self.messages=[]; self.unknowns=[]; self.fail=False
    def approve(self,intent): return {'decision':'ALLOW'}
    def recheck(self,intent): return {'decision':'ALLOW'}
    def unknown(self,intent): self.unknowns.append(intent)
    def settle(self,message):
        if self.fail: raise OSError('guardian unavailable')
        if message['event_id'] not in [m['event_id'] for m in self.messages]: self.messages.append(message)

class OrderClientMock:
    def __init__(self): self.placed=[]; self.queries=[]; self.timeout=False; self.reply=None; self.trade_rows=[]; self.store=None
    def place(self,**params):
        self.placed.append(params)
        if self.store: assert self.store.get(params['newClientOrderId'])['state']=='SUBMITTING'
        if self.timeout: raise TimeoutError('ACK missing')
        return {'clientOrderId':params['newClientOrderId'],'symbol':'SUIUSDT','side':'BUY','status':'NEW','executedQty':'0','orderId':42,'updateTime':10000}
    def query(self,**params): self.queries.append(params); return dict(self.reply)
    def trades(self,**params): return list(self.trade_rows)
    def cancel(self,**params): raise TimeoutError('cancel unknown')

class IndependentOrderQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'orders.sqlite'; self.store=OrderStore(self.path)
        self.client=OrderClientMock(); self.client.store=self.store; self.guardian=MockGuardian()
        self.engine=self.make_engine()
        self.intent=TradeIntent('intent','signal','SUI-live','SUIUSDT','T1','p2-research-1','1','700','.992',10000,20000,10000,10000)
    def make_engine(self): return ExecutionEngine(self.store,self.client,self.guardian,EngineGate(),filters=lambda *a:None,protection_ready=lambda s:True,clock=lambda:10000)
    def event(self,row,status='PARTIALLY_FILLED',qty='100',**kwargs):
        return dict({'clientOrderId':row['client_id'],'symbol':'SUIUSDT','side':'BUY','status':status,'executedQty':qty,'orderId':42,'updateTime':11000},**kwargs)
    def trade(self,qty='100'): return {'tradeId':1,'id':1,'qty':qty,'price':'1','commission':'.1','commissionAsset':'USDT'}
    def test_submit_persisted_before_io_and_timeout_never_reposts(self):
        self.client.timeout=True; row=self.engine.submit_entry(self.intent)
        self.assertEqual(row['state'],'UNKNOWN')
        self.assertEqual(self.engine.submit_entry(self.intent)['state'],'UNKNOWN')
        self.assertEqual(len(self.client.placed),1)

    def test_duplicate_trade_cannot_duplicate_guardian_settlement(self):
        row=self.engine.submit_entry(self.intent); event=self.event(row,fills=[self.trade()])
        self.engine.observe(event); self.engine.observe(event)
        self.assertEqual(len([m for m in self.guardian.messages if m['kind']=='FILL']),1)
    def test_cancel_timeout_does_not_emit_terminal_release(self):
        row=self.engine.submit_entry(self.intent)
        with self.assertRaises(TimeoutError): self.engine.cancel('SUIUSDT',row['client_id'])
        self.assertEqual(self.store.get(row['client_id'])['state'],'UNKNOWN')
        self.assertFalse(any(m['kind']=='TERMINAL' for m in self.guardian.messages))
    def test_terminal_before_trade_details_recovered_after_restart(self):
        row=self.engine.submit_entry(self.intent)
        self.client.reply=self.event(row,status='FILLED',qty='700')
        self.engine.observe(self.client.reply)
        self.assertFalse(self.guardian.messages)
        self.client.trade_rows=[self.trade('700')]
        self.store=OrderStore(self.path); self.engine=self.make_engine(); self.engine.recover()
        self.assertTrue(self.client.queries)
        self.assertEqual([m['kind'] for m in self.guardian.messages],['FILL','TERMINAL'])
    def test_mismatched_side_rejected_before_fill_outbox(self):
        row=self.engine.submit_entry(self.intent); event=self.event(row,fills=[self.trade()]); event['side']='SELL'
        with self.assertRaises(ValueError): self.engine.observe(event)
        self.assertEqual(self.guardian.messages,[])
    def test_guardian_outage_outbox_survives_and_replays_once(self):
        row=self.engine.submit_entry(self.intent); self.guardian.fail=True
        with self.assertRaises(OSError): self.engine.observe(self.event(row,fills=[self.trade()]))
        self.guardian.fail=False; self.store=OrderStore(self.path); self.engine=self.make_engine()
        self.engine.drain(); self.engine.drain()
        self.assertEqual(len(self.guardian.messages),1)
    def test_filled_is_not_reopened_by_late_new(self):
        row=self.engine.submit_entry(self.intent)
        self.engine.observe(self.event(row,status='FILLED',qty='700',fills=[self.trade('700')]))
        self.engine.observe(self.event(row,status='NEW',qty='0'))
        self.assertEqual(self.store.get(row['client_id'])['state'],'FILLED')
    def test_backup_restores_unknown_without_extra_order(self):
        self.client.timeout=True; row=self.engine.submit_entry(self.intent)
        result=self.store.backup(Path(self.tmp.name)/'backup.sqlite')
        restored=OrderStore(result['restore'])
        self.assertEqual(restored.get(row['client_id'])['state'],'UNKNOWN')
        self.assertEqual(len(self.client.placed),1)

from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.p5_bridge import GuardianBridge
from dataclasses import asdict
from decimal import Decimal

class IndependentSettlementQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger=AllocationLedger(Path(self.tmp.name)/'guardian.sqlite')
        self.at=1800000000000; self.bridge=GuardianBridge(self.ledger,clock=lambda:self.at)
        BalanceReconciliation(self.ledger).reconcile('balance',{'USDT':{'free':'2000','locked':'0'}},self.at,at=self.at)
        DrawdownEngine(self.ledger).mark('mark',{},self.at,at=self.at)
        intent=TradeIntent('buy','signal','SUI-live','SUIUSDT','T1','p2-research-1','1','700','.992',self.at,self.at+60000,self.at,self.at)
        self.assertEqual(self.bridge.approve(asdict(intent))['decision'],'ALLOW')
    def fill(self,asset='SUI',fee='.1'):
        return {'kind':'FILL','intent_id':'buy','client_id':'mock','purpose':'ENTRY','symbol':'SUIUSDT','side':'BUY','event_id':'trade-1','trade':{'id':'1','qty':'100','price':'1','commission':fee,'commissionAsset':asset}}
    def test_base_asset_fee_reduces_owned_quantity_once(self):
        message=self.fill(); self.bridge.settle(message); self.bridge.settle(message)
        snapshot=self.ledger.snapshot()
        self.assertEqual(Decimal(snapshot['cash']),1900)
        self.assertEqual(Decimal(snapshot['positions'][0]['quantity']),Decimal('99.9'))
    def test_unowned_bnb_commission_keeps_evidence_unsettled(self):
        with self.assertRaises(ValueError): self.bridge.settle(self.fill('BNB','.001'))
        self.assertEqual(Decimal(self.ledger.snapshot()['cash']),2000)
        self.assertEqual(self.ledger.snapshot()['positions'],[])
    def test_late_fill_after_confirmed_cancel_cannot_make_negative_reservation(self):
        self.bridge.settle({'kind':'TERMINAL','purpose':'ENTRY','intent_id':'buy','event_id':'terminal','executed_quantity':'0','state':'CANCELED'})
        with self.assertRaises(ValueError): self.bridge.settle(self.fill('USDT','.1'))
        self.assertEqual(Decimal(self.ledger.snapshot()['reservations'][0]['remaining']),0)

    def protective_params(self,key,quantity):
        return {'newClientOrderId':key,'symbol':'SUIUSDT','side':'SELL','type':'STOP_LOSS_LIMIT','quantity':quantity,'stopPrice':'.992','price':'.99','timeInForce':'GTC'}

    def funded_position(self):
        self.bridge.settle(self.fill('USDT','0'))
        self.bridge.reconcile({'event_id':'position-ready','account_id':'SUI-live','balances':{'USDT':{'free':'1900','locked':'0'},'SUI':{'free':'100','locked':'0'}},'observed_at':self.at,'unknown_orders':False,'known_orders':[]})

    def test_acknowledged_locked_protection_not_deducted_twice_from_free(self):
        self.funded_position()
        first=self.protective_params('protect-1','40')
        self.assertEqual(self.bridge.approve_aux(first,'PROTECT','buy')['decision'],'ALLOW')
        self.bridge.reconcile({'event_id':'native-ack','account_id':'SUI-live','balances':{'USDT':{'free':'1900','locked':'0'},'SUI':{'free':'60','locked':'40'}},'observed_at':self.at,'unknown_orders':False,'known_orders':[{'client_id':'protect-1','purpose':'PROTECT','symbol':'SUIUSDT','status':'NEW','remaining_quantity':'40'}]})
        self.assertEqual(self.bridge.approve_aux(self.protective_params('protect-2','60'),'PROTECT','buy')['decision'],'ALLOW')
        with self.assertRaises(PermissionError): self.bridge.approve_aux(self.protective_params('protect-3','1'),'PROTECT','buy')

    def test_unknown_locked_order_does_not_release_local_free_reserve(self):
        self.funded_position()
        self.bridge.approve_aux(self.protective_params('protect-1','40'),'PROTECT','buy')
        self.bridge.reconcile({'event_id':'unmatched-lock','account_id':'SUI-live','balances':{'USDT':{'free':'1900','locked':'0'},'SUI':{'free':'60','locked':'40'}},'observed_at':self.at,'unknown_orders':False,'known_orders':[]})
        with self.assertRaises(PermissionError): self.bridge.approve_aux(self.protective_params('protect-2','60'),'PROTECT','buy')

    def test_protection_permit_is_bound_and_single_use(self):
        self.funded_position(); params=self.protective_params('protect-token','40')
        self.bridge.approve_aux(params,'PROTECT','buy')
        token=self.bridge.bind_order(params,True)
        self.assertFalse(self.bridge.verify_order(dict(params,quantity='41'),token,True))
        self.assertTrue(self.bridge.verify_order(params,token,True))
        self.assertFalse(self.bridge.verify_order(params,token,True))

    def test_protection_permit_rechecks_manual_balance_conflict(self):
        self.funded_position(); params=self.protective_params('protect-conflict','100')
        self.bridge.approve_aux(params,'PROTECT','buy'); token=self.bridge.bind_order(params,True)
        self.bridge.reconcile({'event_id':'manual-sold','account_id':'SUI-live','balances':{'USDT':{'free':'1900','locked':'0'},'SUI':{'free':'10','locked':'0'}},'observed_at':self.at,'unknown_orders':False,'known_orders':[]})
        try: verified=self.bridge.verify_order(params,token,True)
        except (ValueError,PermissionError): verified=False
        self.assertFalse(verified)


from solbot.execution.p5_authorization import LiveSession, MockAuthorization
from solbot.risk.p5_ipc import receive, GuardianIPC, MAX_FRAME

class IndependentAuthorityQA(unittest.TestCase):
    def test_forged_live_state_without_lease_has_no_authority(self):
        session=LiveSession(environment='mock',clock=lambda:1000); session.state='LIVE_ON'
        with self.assertRaises(PermissionError): session.require('TRADE')
    def test_production_rejects_even_mock_issuer(self):
        session=LiveSession()
        with self.assertRaises(PermissionError): session.request_on(MockAuthorization(),lambda s:{})
    def test_pending_only_read_and_off_revokes_protection_operations(self):
        session=LiveSession(environment='mock',clock=lambda:1000)
        def checks(s):
            s.require('PRIVATE_READ')
            with self.assertRaises(PermissionError): s.require('TRADE')
            return dict.fromkeys(('account','permissions','orders','balances','protection','guardian','filters'),True)
        session.request_on(MockAuthorization(),checks)
        session.hard_lock()
        with self.assertRaises(PermissionError): session.require('TRADE')
        session.require('PROTECT')
        session.off()
        with self.assertRaises(PermissionError): session.require('PROTECT')
    def test_ipc_unknown_method_rejected_before_socket_creation(self):
        with self.assertRaises(ValueError): GuardianIPC('unused-path',expected_uid=0).call('administrator_unlock')
    def test_ipc_bounded_frame(self):
        class FakeSocket:
            def recv(self,n): return b'x'*n
        with self.assertRaises(ValueError): receive(FakeSocket())
    def test_ipc_partial_frame_has_no_dispatch(self):
        class FakeSocket:
            def __init__(self): self.responses=[b'{"method":"approve"}',b'']
            def recv(self,n): return self.responses.pop(0)
        with self.assertRaises(ConnectionError): receive(FakeSocket())

if __name__=='__main__': unittest.main()

