import unittest
from unittest.mock import patch
from solbot.exchange.binance_private import BinancePrivate, Credentials, MockTransport, HTTPTransport, AuthorizationDenied, ExchangeError
from solbot.exchange.account_reader import AccountReader
from solbot.exchange.order_client import OrderClient,EngineClient
from solbot.exchange.exchange_filters import ExchangeFilters
from solbot.exchange.commission_service import CommissionService
from solbot.exchange.user_stream import UserStream

class Gate:
    def __init__(self): self.state='LIVE_ON'
    def require(self,scope,account_id,symbol=None):
        if self.state=='LIVE_OFF' or (scope!='PRIVATE_READ' and self.state=='LIVE_PENDING'): raise AuthorizationDenied('not authorized')

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.loads=[]; self.gate=Gate(); self.transport=MockTransport()
        def load(): self.loads.append(1); return Credentials('synthetic-key','synthetic-secret')
        self.client=BinancePrivate(load,self.transport,self.gate,clock=lambda:1800000000000)
        self.client.sync_time(1800000000000,sent_at=1800000000000,received_at=1800000000000)
    def test_off_no_credentials_or_transport(self):
        self.gate.state='LIVE_OFF'
        with self.assertRaises(AuthorizationDenied): AccountReader(self.client).account()
        self.assertEqual(self.loads,[]); self.assertEqual(self.transport.calls,[])
    def test_pending_read_only(self):
        self.gate.state='LIVE_PENDING'; self.transport.responses=[(200,{}, {'balances':[]})]
        self.assertEqual(AccountReader(self.client).account(),{'balances':[]})
        with self.assertRaises(AuthorizationDenied): self.client.request('POST','/api/v3/order',{},scope='TRADE')
        self.assertEqual(len(self.transport.calls),1)
    def test_forged_production_gate_cannot_load_credentials(self):
        self.client.transport=HTTPTransport()
        with self.assertRaises(AuthorizationDenied): AccountReader(self.client).account()
        self.assertEqual(self.loads,[])
    def test_mock_subclass_cannot_insert_network_callback(self):
        class Evil(MockTransport):
            def request(self,*args): raise AssertionError('network reached')
        self.client.transport=Evil()
        with self.assertRaises(AuthorizationDenied): AccountReader(self.client).account()
    def test_hmac_protocol_uses_exact_encoded_payload(self):
        import hmac,hashlib
        self.transport.responses=[(200,{}, {'ok':True})]
        original=self.transport.request
        observed=[]
        def record(method,path,query,headers):
            raw,signature=query.rsplit('&signature=',1)
            observed.append(hmac.compare_digest(signature,hmac.new(b'synthetic-secret',raw.encode(),hashlib.sha256).hexdigest()))
            return original(method,path,query,headers)
        with patch.object(self.transport,'request',record): AccountReader(self.client).order('synthetic-order')
        self.assertEqual(observed,[True])
    def test_timeout_never_retries_and_is_unknown(self):
        self.transport.responses=[TimeoutError(),(200,{}, {})]
        with self.assertRaises(ExchangeError) as error: self.client.request('POST','/api/v3/order',{'symbol':'SUIUSDT'},scope='TRADE')
        self.assertTrue(error.exception.unknown); self.assertEqual(len(self.transport.calls),1)
    def test_throttle_prevents_second_call(self):
        self.transport.responses=[(429,{'Retry-After':'60'},{'code':-1003})]
        for _ in range(2):
            with self.assertRaises(ExchangeError): AccountReader(self.client).account()
        self.assertEqual(len(self.transport.calls),1)
    def test_credentials_and_remote_errors_redacted(self):
        self.transport.responses=[(500,{}, {'code':'synthetic-secret','msg':'synthetic-key'})]
        with self.assertRaises(ExchangeError) as error: AccountReader(self.client).account()
        self.assertNotIn('synthetic',str(error.exception)); self.assertNotIn('synthetic',repr(Credentials('synthetic-key','synthetic-secret')))
    def test_forbidden_transfer_endpoint(self):
        with self.assertRaises(AuthorizationDenied): self.client.request('POST','/sapi/v1/asset/transfer',{},scope='TRADE')
        self.assertEqual(self.loads,[])
    def test_unapproved_order_rejected(self):
        orders=OrderClient(self.client,lambda *args:False)
        with self.assertRaises(AuthorizationDenied): orders.place({'newClientOrderId':'order','symbol':'SUIUSDT'},None)
        self.assertEqual(self.loads,[])
    def test_clock_error_invalidates_sync(self):
        self.transport.responses=[(400,{}, {'code':-1021})]
        with self.assertRaises(ExchangeError): AccountReader(self.client).account()
        self.assertIsNone(self.client.synced_at)
    def test_stream_off_blocks_subscription_and_delivery(self):
        stream=UserStream(self.client); self.gate.state='LIVE_OFF'
        with self.assertRaises(AuthorizationDenied): stream.subscribe_request('one')
        self.assertEqual(self.loads,[])
    def test_stream_balance_delta_requires_reconcile(self):
        stream=UserStream(self.client); request=stream.subscribe_request('one')
        self.assertEqual(request['method'],'userDataStream.subscribe.signature')
        stream.acknowledge({'status':200,'result':{'subscriptionId':1}})
        self.assertEqual(stream.receive({'subscriptionId':1,'event':{'e':'balanceUpdate','d':'10'}})['kind'],'RECONCILE_REQUIRED')
    def test_sol_manual_fill_stays_out_of_robot_orders(self):
        stream=UserStream(self.client); stream.acknowledge({'status':200,'result':{'subscriptionId':1}})
        manual=stream.receive({'subscriptionId':1,'event':{'e':'executionReport','s':'SOLUSDT','c':'web-1','X':'FILLED'}})
        self.assertEqual((manual['kind'],manual['symbol'],manual['client_order_id']),('MANUAL_ORDER','SOLUSDT','web-1'))
        robot=stream.receive({'subscriptionId':1,'event':{'e':'executionReport','s':'SOLUSDT','c':'sb5-abc','X':'FILLED','i':9,'x':'TRADE','t':3,'E':1,'T':1,'q':'1','z':'1','Z':'100','l':'1','L':'100','n':'0','N':'BNB'}})
        self.assertEqual(robot['kind'],'ORDER');self.assertEqual(robot['symbol'],'SOLUSDT')
        self.assertEqual(stream.disconnected()['kind'],'RECONCILE_REQUIRED')
    def test_actual_fee_asset_needs_price(self):
        with self.assertRaises(ValueError): CommissionService.quote_value('0.01','BNB',{})
        self.assertEqual(str(CommissionService.quote_value('0.01','BNB',{'BNB':'600'})),'6.00')
    def test_filters_bnb_and_stop_price_precision(self):
        data={'symbols':[{'symbol':'BNBUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['LIMIT','STOP_LOSS_LIMIT'],'filters':[{'filterType':'PRICE_FILTER','minPrice':'1','maxPrice':'10000','tickSize':'.01'},{'filterType':'LOT_SIZE','minQty':'.001','maxQty':'1000','stepSize':'.001'},{'filterType':'MIN_NOTIONAL','minNotional':'5'}]}]}
        rules=ExchangeFilters.from_exchange_info(data,'BNBUSDT',1800000000000)
        params={'symbol':'BNBUSDT','type':'STOP_LOSS_LIMIT','side':'SELL','price':'600','quantity':'.02','stopPrice':'599.999','timeInForce':'GTC'}
        self.assertIn('PRICE_FILTER',rules.validate(params,at=1800000000000))
        params['stopPrice']='599.99'; self.assertEqual(rules.validate(params,at=1800000000000),())
    def test_unknown_filter_fails_closed(self):
        data={'symbols':[{'symbol':'SUIUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['LIMIT'],'filters':[{'filterType':'UNANNOUNCED'}]}]}
        f=ExchangeFilters.from_exchange_info(data,'SUIUSDT',1800000000000)
        self.assertIn('UNKNOWN_FILTER:UNANNOUNCED',f.validate({'symbol':'SUIUSDT','side':'BUY','type':'LIMIT','quantity':'400','price':'1','timeInForce':'GTC'},at=1800000000000))
    def test_recovery_trade_history_pages_beyond_1000(self):
        class Reader:
            def __init__(self): self.cursors=[]
            def trades(self,symbol,*,order_id,from_id,limit):
                self.cursors.append(from_id)
                return [{'id':i,'orderId':7} for i in range(from_id,min(from_id+limit,1001))]
        reader=Reader(); engine=EngineClient(None,reader,None)
        self.assertEqual(len(engine.trades(symbol='SUIUSDT',orderId=7)),1001)
        self.assertEqual(reader.cursors,[0,1000])
    def test_off_closes_registered_private_client(self):
        class RevocableGate(Gate):
            def __init__(self): super().__init__(); self.callbacks=[]
            def register_close(self,callback): self.callbacks.append(callback)
            def off(self):
                self.state='LIVE_OFF'
                for callback in self.callbacks: callback()
        gate=RevocableGate(); transport=MockTransport(); closed=[]
        transport.close=lambda:closed.append(True)
        client=BinancePrivate(lambda:Credentials('mock','mock'),transport,gate)
        gate.off(); gate.off()
        self.assertTrue(client.closed); self.assertEqual(closed,[True])
    def test_off_actively_closes_idle_websocket(self):
        import asyncio
        async def scenario():
            callbacks=[]
            self.gate.register_close=callbacks.append
            stream=UserStream(self.client); stream.connected=True
            closed=asyncio.Event()
            class Socket:
                async def close(self): closed.set()
            stream._bind_socket_close(Socket(),asyncio.get_running_loop())
            self.gate.state='LIVE_OFF'; callbacks[0]()
            await asyncio.wait_for(closed.wait(),timeout=1)
            self.assertFalse(stream.connected)
        asyncio.run(scenario())

if __name__=='__main__': unittest.main()
