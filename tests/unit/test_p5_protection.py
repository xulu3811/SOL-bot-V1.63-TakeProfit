import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from solbot.execution.p5_protection import ProtectionManager,ProtectionError
from solbot.execution.p5_bnb import BNBExecution,account_fill,commission_value

INFO={'symbol':'SUIUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['STOP_LOSS_LIMIT'],'allowTrailingStop':False,'filters':[{'filterType':'PRICE_FILTER','tickSize':'.001','minPrice':'.001','maxPrice':'1000'},{'filterType':'LOT_SIZE','stepSize':'.1','minQty':'.1','maxQty':'100000'}]}
class Gate:
    def __init__(self):self.off=False
    def require(self,*args,**kwargs):
        if self.off:raise PermissionError('OFF')
class Engine:
    def __init__(self):self.calls=[];self.mode='NEW';self.params={}
    def submit(self,params,purpose,position_id=None):
        self.calls.append(('submit',params));self.params[params['newClientOrderId']]=params
        if self.mode=='TIMEOUT':raise TimeoutError()
        return {'clientOrderId':params['newClientOrderId'],'symbol':params['symbol'],'side':params['side'],'status':self.mode,'orderId':123,'executedQty':'0'}
    def cancel(self,symbol,client_id):
        self.calls.append(('cancel',client_id))
        return {'clientOrderId':client_id,'symbol':symbol,'side':'SELL','status':'CANCELED','orderId':123,'executedQty':'0'}

class ProtectionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'protection.sqlite'
        self.gate=Gate();self.engine=Engine();self.m=ProtectionManager(self.path,self.engine,self.gate)
    def install(self,owned='100'):
        return self.m.initial('p','100',owned,'1',INFO)
    def test_native_ack_and_initial_stop(self):
        r=self.install();self.assertEqual(r['stop'],'0.992');self.assertEqual(r['state'],'NEW');self.assertTrue(self.m.entry_safe())
    def test_off_no_submit_and_keeps_native_order(self):
        row=self.install();self.gate.off=True
        with self.assertRaises(PermissionError):self.m.update(row['client_id'],'1.01','1','200',INFO)
        self.assertEqual(len(self.engine.calls),1);self.assertEqual(self.m.snapshot(row['client_id'])['state'],'NEW')
    def test_submission_timeout_persists_unknown_no_retry(self):
        self.engine.mode='TIMEOUT'
        with self.assertRaises(TimeoutError):self.install()
        self.assertEqual(self.m.snapshot()[0]['state'],'UNKNOWN');self.assertFalse(self.m.entry_safe())
        self.install();self.assertEqual(len(self.engine.calls),1)
    def test_rejected_not_protected(self):
        self.engine.mode='REJECTED';self.install();self.assertFalse(self.m.entry_safe())
    def test_replace_never_cancels_without_extra_inventory(self):
        row=self.install()
        with self.assertRaises(ProtectionError):self.m.update(row['client_id'],'1.01','1','100',INFO)
        self.assertEqual(len(self.engine.calls),1)
    def test_replace_new_ack_precedes_cancel_old(self):
        row=self.install();new=self.m.update(row['client_id'],'1.01','1','200',INFO)
        self.assertEqual([c[0] for c in self.engine.calls],['submit','submit','cancel'])
        self.assertEqual(new['previous_id'],row['client_id'])
        self.assertTrue(self.m.entry_safe())
    def test_stop_never_loosened(self):
        row=self.install()
        with self.assertRaises(ProtectionError):self.m.update(row['client_id'],'.99','.98','200',INFO)
    def test_unsupported_trailing_rejected(self):
        with self.assertRaises(ProtectionError):self.m.plan('SUIUSDT','100','.99','.98',INFO,trailing_delta=100)
    def test_supported_trailing_uses_below_filter(self):
        info=copy.deepcopy(INFO);info['allowTrailingStop']=True;info['filters'].append({'filterType':'TRAILING_DELTA','minTrailingBelowDelta':10,'maxTrailingBelowDelta':200})
        self.assertEqual(self.m.plan('SUIUSDT','100','.99','.98',info,trailing_delta=100)['trailingDelta'],100)
    def test_partial_protection_and_event_dedup(self):
        row=self.install();event={'clientOrderId':row['client_id'],'symbol':'SUIUSDT','side':'SELL','status':'PARTIALLY_FILLED','orderId':123,'executedQty':'20'}
        self.m.observe('e',event);self.m.observe('e',event)
        self.assertEqual(self.m.snapshot(row['client_id'])['executed'],'20')
        self.m.observe('older',dict(event,executedQty='10'))
        self.assertEqual(self.m.snapshot(row['client_id'])['executed'],'20')
    def test_gap_and_disconnect_not_false_fill(self):
        row=self.install();self.assertEqual(self.m.snapshot(row['client_id'])['executed'],'0')
        self.m.disconnect();self.assertFalse(self.m.entry_safe());self.assertEqual(self.m.snapshot(row['client_id'])['state'],'UNKNOWN')
        reopened=ProtectionManager(self.path,self.engine,self.gate);self.assertEqual(reopened.snapshot(row['client_id'])['state'],'UNKNOWN')
    def test_unknown_market_type_rejected(self):
        info=copy.deepcopy(INFO);info['orderTypes']=['LIMIT']
        with self.assertRaises(ProtectionError):self.m.plan('SUIUSDT','100','.99','.98',info)
    def test_equal_partial_tranches_keep_position_identity(self):
        first=self.m.install('p','SUIUSDT','100','200','.992','.990',INFO,generation='100')
        second=self.m.install('p','SUIUSDT','100','200','.992','.990',INFO,generation='200')
        self.assertNotEqual(first['client_id'],second['client_id'])
        self.assertEqual(first['position_id'],second['position_id'])
        self.m.install('p','SUIUSDT','100','200','.992','.990',INFO,generation='200')
        self.assertEqual(len(self.engine.calls),2)
    def decision(self,exit_required=False):
        from types import SimpleNamespace as S
        return S(context=S(symbol='SUIUSDT',account_id='SUI-live',decision_time_ms=1800000000000),status='EXIT_REQUIRED' if exit_required else 'PROTECTION_MATH_ONLY',exit_required=exit_required,stop_price='1.01',limit_price='1')
    def test_trend_exit_guarded_and_native_not_cancelled(self):
        row=self.install();result=self.m.apply_apts(row['client_id'],self.decision(True),'100',INFO)
        self.assertEqual(result['exit']['status'],'NEW')
        self.assertEqual([c[0] for c in self.engine.calls],['submit','submit'])
        with self.assertRaises(ProtectionError):self.m.apply_apts(row['client_id'],self.decision(True),'100',INFO)
    def test_trend_exit_failure_retains_old_protection(self):
        row=self.install();self.engine.mode='TIMEOUT'
        with self.assertRaises(TimeoutError):self.m.apply_apts(row['client_id'],self.decision(True),'100',INFO)
        self.assertEqual(self.m.snapshot(row['client_id'])['state'],'NEW');self.assertFalse(self.m.entry_safe())
    def test_breakout_apts_update_without_inventory_keeps_old_stop(self):
        row=self.install()
        with self.assertRaises(ProtectionError):self.m.apply_apts(row['client_id'],self.decision(),'100',INFO)
        self.assertEqual(self.m.snapshot(row['client_id'])['stop'],'0.992')

class BNBTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.gate=Gate();self.engine=Engine()
        def guardian(p):return {'approved':True,'intent_hash':hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()}
        self.m=BNBExecution(Path(t.name)/'bnb.sqlite',self.engine,self.gate,guardian)
    def refill(self,id='b'):
        return self.m.refill(id,robot_bnb='0',free_bnb='0',price='500',free_usdt='100',robot_cash='100',step='.001',min_notional='5')
    def test_refill_duplicate_and_pending_block(self):
        self.refill();self.refill();self.assertEqual(len(self.engine.calls),1)
        with self.assertRaises(ValueError):self.refill('second')
    def test_off_no_bnb_order(self):
        self.gate.off=True
        with self.assertRaises(PermissionError):self.refill()
        self.assertFalse(self.engine.calls)
    def test_unknown_bnb_no_second_order(self):
        self.engine.mode='TIMEOUT'
        with self.assertRaises(TimeoutError):self.refill()
        with self.assertRaises(ValueError):self.refill('second')
        self.assertEqual(len(self.engine.calls),1)
    def test_guardian_required(self):
        self.m.guardian=lambda p:{'approved':True,'intent_hash':'wrong'}
        with self.assertRaises(ValueError):self.refill()
        self.assertFalse(self.engine.calls)
    def test_true_commission_asset_and_idempotence(self):
        row=self.refill();e={'clientOrderId':row['client_id'],'qty':'.02','quoteQty':'10','commission':'.00002','commissionAsset':'BNB'}
        result=self.m.record_trade('trade1',e,{'USDT':'100','BNB':'0'})
        self.assertEqual(Decimal(result['BNB']),Decimal('.01998'));self.assertEqual(Decimal(result['USDT']),90)
        self.assertEqual(self.m.record_trade('trade1',e),result)
    def test_fee_in_quote_base_or_bnb(self):
        for asset in ('USDT','SUI','BNB'):
            result=account_fill({'USDT':'100','SUI':'0','BNB':'1'},side='BUY',base='SUI',quote='USDT',quantity='10',quote_quantity='10',commission='.01',commission_asset=asset)
            self.assertEqual(Decimal(result[asset]),{'USDT':Decimal('89.99'),'SUI':Decimal('9.99'),'BNB':Decimal('.99')}[asset])
    def test_unknown_fee_valuation_rejected(self):
        with self.assertRaises(ValueError):commission_value('.1','BNB',{})
        self.assertEqual(commission_value('.1','BNB',{'BNB':'500'}),50)

if __name__=='__main__':unittest.main()
