import tempfile
import unittest
from pathlib import Path
from solbot.exchange.binance_private import BinancePrivate,Credentials,MockTransport
from solbot.exchange.account_reader import AccountReader
from solbot.exchange.p5_reconciliation import PrivateHandshake
from solbot.execution.p5_store import OrderStore
from solbot.execution.p5_authorization import LiveSession,MockAuthorization,AuthorizationDenied

NOW=1800000000000
def account(cash='2000'):
    return {'canTrade':True,'permissions':['SPOT'],'updateTime':NOW,'balances':[{'asset':'USDT','free':cash,'locked':'0'},{'asset':'SUI','free':'300','locked':'0'}]}
def permissions():
    return dict(enableReading=True,enableSpotAndMarginTrading=True,**{k:False for k in ('enableWithdrawals','enableInternalTransfer','permitsUniversalTransfer','enableMargin','enableFutures','enableVanillaOptions','enablePortfolioMarginTrading')})
class Guardian:
    def __init__(self): self.events=[]
    def reconcile(self,event):
        self.events.append(event)
        return 'UNRECONCILED' if event['unknown_orders'] else 'RECONCILED'

class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=OrderStore(Path(self.tmp.name)/'orders.sqlite')
    def setup_handshake(self,*,cash='2000',manual=(),perms=None,final=None):
        self.session=LiveSession(environment='mock',clock=lambda:NOW)
        responses=[account(cash),permissions() if perms is None else perms,list(manual),{},[],{},account(cash) if final is None else final]
        transport=MockTransport([(200,{},value) for value in responses])
        self.client=BinancePrivate(lambda:Credentials('mock-key','mock-secret'),transport,self.session,clock=lambda:NOW)
        self.client.sync_time(NOW,sent_at=NOW,received_at=NOW)
        self.guardian=Guardian()
        self.handshake=PrivateHandshake(AccountReader(self.client),self.store,self.guardian,recover=lambda:None,protection_checker=lambda orders:True,filters_checker=lambda:True,clock=lambda:NOW)
    def test_pending_read_handshake_and_manual_inventory_retained(self):
        self.setup_handshake(); self.session.request_on(MockAuthorization(),self.handshake)
        self.assertEqual(self.session.state,'LIVE_ON')
        self.assertEqual(self.guardian.events[0]['balances']['SUI']['free'],'300')
        self.assertEqual(self.guardian.events[0]['known_orders'],[])
    def test_initial_2000_not_assumed_funded(self):
        self.setup_handshake(cash='1999')
        with self.assertRaises(AuthorizationDenied): self.session.request_on(MockAuthorization(),self.handshake)
        self.assertEqual(self.session.state,'LIVE_OFF'); self.assertTrue(self.client.closed)
    def test_transfer_capability_is_rejected(self):
        perms=permissions(); perms['enableWithdrawals']=True
        self.setup_handshake(perms=perms)
        with self.assertRaises(ValueError): self.session.request_on(MockAuthorization(),self.handshake)
        self.assertEqual(self.session.state,'LIVE_OFF'); self.assertTrue(self.client.closed)
    def test_manual_orders_are_preserved_and_block_initial_entry(self):
        self.setup_handshake(manual=[{'orderId':1,'clientOrderId':'manual','status':'NEW'}])
        with self.assertRaises(AuthorizationDenied): self.session.request_on(MockAuthorization(),self.handshake)
        self.assertEqual(self.guardian.events[0]['manual_orders'][0]['clientOrderId'],'manual')
        self.assertTrue(self.guardian.events[0]['unknown_orders'])
    def test_concurrent_account_change_invalidates_snapshot(self):
        self.setup_handshake(final=account('1990'))
        with self.assertRaises(AuthorizationDenied): self.session.request_on(MockAuthorization(),self.handshake)
        self.assertTrue(self.guardian.events[0]['unknown_orders'])
    def test_off_does_not_start_handshake_reads(self):
        self.setup_handshake()
        with self.assertRaises(AuthorizationDenied): self.handshake(self.session)
        self.assertEqual(self.client.transport.calls,[])
    def test_matching_trade_id_cannot_hide_changed_commission(self):
        import json
        self.setup_handshake()
        payload={'symbol':'SUIUSDT','side':'BUY'}
        stored={'id':'2','qty':'1','price':'1','commission':'.001','commissionAsset':'USDT'}
        with self.store.tx() as db:
            db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('robot','intent','hash',json.dumps(payload),'ENTRY','FILLED','1','1','1',NOW,'FILLED',NOW))
            db.execute('INSERT INTO fills VALUES(?,?,?)',('SUIUSDT:1:2','robot',json.dumps(stored)))
        order={'clientOrderId':'robot','orderId':1,'symbol':'SUIUSDT','side':'BUY','status':'FILLED','executedQty':'1','origQty':'1','type':'LIMIT'}
        changed=dict(stored,orderId=1,commission='.002')
        self.client.transport.responses=[(200,{},v) for v in (account(),permissions(),[],{},[],{},order,[changed],account())]
        with self.assertRaises(AuthorizationDenied): self.session.request_on(MockAuthorization(),self.handshake)
        self.assertTrue(self.guardian.events[0]['unknown_orders'])

if __name__=='__main__': unittest.main()
