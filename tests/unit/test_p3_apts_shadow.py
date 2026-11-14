from contextlib import closing
from dataclasses import replace
from decimal import Decimal as Dec
from pathlib import Path
import sqlite3
import tempfile
import unittest

from solbot.mathematics.contracts import DecisionContext,SymbolRules
from solbot.mathematics.decimal_utils import canonical_hash
from solbot.mathematics.apts_exit import APTSState,update_protection
from solbot.mathematics.protective_stop import initial_stop,profit_floor
from solbot.mathematics.trend_exhaustion import TrendEvidence,classify
from solbot.mathematics.volatility_regime import trailing_distance,decimal_atr
from solbot.execution.shadow import ShadowExecutor,ShadowRejected
from solbot.execution.p3_shadow import P3ShadowExecutor

NOW=1794400000000
RULES=SymbolRules('SOLUSDT',Dec('.01'),Dec('.001'),Dec('.01'),Dec('1000000'),Dec('.001'),Dec('1000000'),Dec('5'))

def context(now=NOW):
    return DecisionContext('SOLUSDT','SOL-shadow','T1','p3-research-1','test-signal',now,now,now+5000,'a'*64)

def features(offset,bid='100',ask='100.01',quantity='10',update=None):
    return {'symbol':'SOLUSDT','ready':True,'synced':True,'exchange_time_ms':NOW+offset,'received_time_ms':NOW+offset,
            'bid':bid,'ask':ask,'bids':[[bid,quantity]],'asks':[[ask,quantity]],'update_id':update or offset,
            'input_hash':canonical_hash([offset,bid,ask,quantity])}

def signal(index=0,price='100',size=None):
    return {'symbol':'SOLUSDT','strategy_id':'T1','signal_id':'test-'+str(index),'signal_time':NOW,'expires_at':NOW+300000,
            'accepted':True,'reasons':[],'risk_flags':['RESEARCH_PARAMETERS_UNVALIDATED'],
            'entry_low':'90','entry_high':price,'strategy_version':'p3-research-1'}

NORMAL=TrendEvidence(ready=True,atr=Dec('.2'))
STRONG=TrendEvidence(ready=True,trend_4h=True,trend_1h=True,trend_15m=True,higher_low_1m=True,higher_low_5m=True,
                     rvol=Dec(3),buy_ratio=Dec('.7'),previous_buy_ratio=Dec('.65'),atr=Dec('.5'))


class APTSTests(unittest.TestCase):
    def state(self,**kwargs):return APTSState.create(context(),'100','3','.01',**kwargs)
    def step(self,state,bid,offset,evidence=NORMAL):
        return update_protection(state,context(NOW+offset),bid,evidence,tick_size='.01')

    def test_initial_tick_and_fee_floor_independent_inequalities(self):
        self.assertEqual(initial_stop('100.03','.05'),Dec('99.25'))
        with self.assertRaises(ValueError):initial_stop('1','1')
        floor=profit_floor('100','.001','.002','.003','.01')
        self.assertGreater(floor*Dec('.997')*Dec('.998'),Dec('100.1'))
        self.assertEqual(floor%Dec('.01'),0)

    def test_stage_boundaries_and_latched_high_stage(self):
        state=self.state()
        self.assertEqual(self.step(state,'100.99',1).state.stage,'S0_INITIAL_PROTECTION')
        state=self.step(state,'101',2).state
        self.assertEqual(state.stage,'S1_PROFIT_PROTECTION')
        state=self.step(state,'101.5',3).state
        self.assertEqual(state.stage,'S1_PROFIT_PROTECTION')
        state=self.step(state,'101.51',4).state
        self.assertEqual(state.stage,'S2_ADAPTIVE_TRAILING')

    def test_strong_breakout_normal_retracement_survives_and_continues(self):
        normal=self.step(self.state(),'102',1).state
        strong=self.step(self.state(),'102',1,STRONG).state
        self.assertTrue(self.step(normal,'101.38',2).exit_required)
        retrace=self.step(strong,'101.38',2,STRONG)
        self.assertFalse(retrace.exit_required)
        higher=self.step(retrace.state,'103',3,STRONG)
        self.assertGreater(higher.stop_price,strong.stop_price)

    def test_no_retroactive_widening_trigger_and_remaining_quantity(self):
        state=self.step(self.state(),'102',1).state
        triggered=self.step(state,'101.48',2,STRONG)
        self.assertTrue(triggered.exit_required)
        after=update_protection(triggered.state,context(NOW+3),'104',STRONG,tick_size='.01',quantity='1')
        self.assertTrue(after.exit_required)
        self.assertEqual(after.stop_price,triggered.stop_price)
        self.assertEqual(after.quantity,1)

    def test_data_loss_keeps_stop_and_fresh_price_can_trigger_without_trend(self):
        state=self.step(self.state(),'102',1).state
        stale=replace(context(NOW+10000),book_time_ms=NOW)
        result=update_protection(state,stale,'90',NORMAL,tick_size='.01')
        self.assertEqual(result.status,'DATA_UNAVAILABLE')
        self.assertEqual(result.stop_price,state.stop_price)
        self.assertFalse(result.exit_required)
        self.assertTrue(self.step(state,'99',10001,TrendEvidence()).exit_required)

    def test_volume_alone_and_exhaustion_combinations(self):
        self.assertEqual(classify(TrendEvidence(ready=True,rvol=Dec(10))),'NORMAL_UPTREND')
        evidence=replace(NORMAL,structure_broken=True,buy_ratio=Dec('.3'),previous_buy_ratio=Dec('.7'),rsi=Dec('40'),previous_rsi=Dec('50'))
        result=self.step(self.state(),'101.2',1,evidence)
        self.assertTrue(result.exit_required)
        self.assertEqual(result.state.stage,'S3_TREND_EXHAUSTION')
        held_state=APTSState.create(context(),'83.12','0.6','.01')
        held=self.step(held_state,'82.90',1,evidence)
        self.assertFalse(held.exit_required)
        self.assertEqual(held.state.stage,'S0_INITIAL_PROTECTION')
        self.assertEqual(held.stop_price,initial_stop('83.12','.01'))
        self.assertIn('REVERSAL_OBSERVED_S0_STOP_RETAINED',held.reasons)
        self.assertTrue(self.step(self.state(),'99.20',2,evidence).exit_required)
        weak=replace(NORMAL,failed_new_high=True,rvol=Dec(4),rsi=Dec(45),previous_rsi=Dec(70))
        self.assertEqual(classify(weak),'WEAKENING')

    def test_stop_never_falls_under_width_changes_and_hash_changes(self):
        state=self.state()
        for index,(bid,evidence) in enumerate([('102',NORMAL),('102.1',STRONG),('103',NORMAL),('103.1',STRONG)],1):
            result=self.step(state,bid,index,evidence)
            self.assertGreaterEqual(result.stop_price,state.stop_price)
            state=result.state
        self.assertNotEqual(self.step(self.state(),'101',1).context.input_hash,self.step(self.state(),'102',1).context.input_hash)
        self.assertFalse(result.live_authorized)

    def test_decimal_atr_constant_true_ranges_and_research_distances(self):
        rows=[{'high':'101','low':'99','close':'100'} for _ in range(30)]
        self.assertEqual(decimal_atr(rows),2)
        self.assertEqual(trailing_distance('STRONG_BREAKOUT','0.5','100'),Dec('.01'))
        self.assertEqual(trailing_distance('WEAKENING','1','100'),Dec('.004'))


class P3ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'shadow.sqlite'
        self.ledger=P3ShadowExecutor(self.path,rules=RULES,slippage_rate='0')

    def event(self,offset,bid='99.99',ask='100',quantity='10'):
        return self.ledger.process_market(features(offset,bid,ask,quantity),NORMAL,now_ms=NOW+offset)

    def test_partial_buy_protection_sell_and_decimal_accounting(self):
        self.ledger.submit_candidate(signal(),notional='300',now_ms=NOW)
        first=self.event(1)
        self.assertEqual(Dec(first['fills'][0]['quantity']),1)
        state=self.ledger.snapshot()
        self.assertEqual(state['pending_orders'][0]['fill_state'],'PARTIALLY_FILLED')
        self.assertEqual(Dec(state['positions'][0]['quantity']),1)
        self.assertEqual(Dec(state['protection'][0]['state']['stop_price']),Dec('99.20'))
        self.event(2);self.event(3)
        self.assertEqual(Dec(self.ledger.snapshot()['cash']),Dec('1699.7'))
        self.event(4,'102','102.01')
        self.event(5,'101.4','101.41')
        self.assertEqual(len(self.ledger.snapshot()['pending_orders']),1)
        self.event(6,'101.4','101.41')
        self.assertEqual(Dec(self.ledger.snapshot()['positions'][0]['quantity']),2)
        self.event(7,'101.4','101.41');self.event(8,'101.4','101.41')
        self.assertEqual(self.ledger.snapshot()['positions'],[])
        self.assertEqual(Dec(self.ledger.snapshot()['cash']),Dec('2003.5958'))

    def test_gap_stop_limit_unfilled_and_not_cancelled(self):
        self.ledger.submit_candidate(signal(),now_ms=NOW)
        self.event(1,quantity='30')
        self.event(2,'97','97.01',quantity='30')
        snap=self.ledger.snapshot()
        order=snap['pending_orders'][0]
        self.assertTrue(order['protection_order'])
        with self.assertRaises(ShadowRejected):self.ledger.cancel(order['order_id'])
        self.assertEqual(self.event(3,'96','96.01',quantity='30')['fills'],[])
        self.assertEqual(len(self.ledger.snapshot()['positions']),1)
        stale=features(4);stale['ready']=False;stale['synced']=False
        result=self.ledger.process_market(stale,NORMAL,now_ms=NOW+4)
        self.assertEqual(result['code'],'DATA_UNAVAILABLE_KEEP_PROTECTION')

    def test_hard_lock_only_blocks_entries_and_keeps_protection(self):
        self.ledger.submit_candidate(signal(),notional='700',now_ms=NOW)
        self.ledger.submit_candidate(signal(1),notional='700',now_ms=NOW)
        self.event(1,quantity='70')
        result=self.event(2,'80','80.01',quantity='70')
        snap=self.ledger.snapshot()
        self.assertTrue(snap['hard_locked'])
        self.assertGreater(len(snap['positions']),0)
        self.assertEqual(result['fills'],[])
        self.assertFalse(any(r['side']=='BUY' for r in snap['pending_orders']))
        restarted=P3ShadowExecutor(self.path,rules=RULES,slippage_rate='0')
        self.assertTrue(restarted.snapshot()['hard_locked'])
        with self.assertRaises(ShadowRejected):restarted.submit_candidate(signal(3),now_ms=NOW+3)
        self.assertFalse(any(r['close_reason']=='hard_drawdown_lock' for r in restarted.orders()))

    def test_two_slots_with_partial_entry_idempotency_and_duplicate_book(self):
        original=self.ledger.submit_candidate(signal(),now_ms=NOW)
        self.event(1)
        self.ledger.submit_candidate(signal(1),now_ms=NOW+1)
        with self.assertRaises(ShadowRejected):self.ledger.submit_candidate(signal(2),now_ms=NOW+1)
        self.assertEqual(self.ledger.submit_candidate(signal(),now_ms=NOW+2)['order_id'],original['order_id'])
        before=self.ledger.snapshot()['cash']
        self.assertFalse(self.event(1)['processed'])
        self.assertEqual(before,self.ledger.snapshot()['cash'])

    def test_p2_migration_preserves_lock_cash_and_prevents_legacy_writer(self):
        other=Path(self.temp.name)/'old.sqlite'
        old=ShadowExecutor(other)
        with old._transaction() as db:
            db.execute("UPDATE shadow_account SET cash='1900',last_equity='1900',hard_locked=1,lock_reason='maximum_drawdown_5_percent'")
        upgraded=P3ShadowExecutor(other,rules=RULES)
        self.assertEqual(upgraded.snapshot()['cash'],'1900')
        self.assertTrue(upgraded.snapshot()['hard_locked'])
        with self.assertRaises(ValueError):ShadowExecutor(other)
        with self.assertRaises(ValueError):upgraded.process_quote('SOLUSDT','99','100',NOW)

    def test_symbol_and_partial_remaining_order_expiry(self):
        self.ledger.submit_candidate(signal(),now_ms=NOW)
        self.event(1)
        stale=features(300001);stale['ready']=False;stale['synced']=False
        self.ledger.process_market(stale,NORMAL,now_ms=NOW+300001)
        self.assertEqual(Dec(self.ledger.snapshot()['reserved']),0)
        self.assertEqual(len(self.ledger.snapshot()['positions']),1)
        foreign=features(300002);foreign['symbol']='SUIUSDT'
        with self.assertRaises(ValueError):self.ledger.process_market(foreign,NORMAL,now_ms=NOW+300002)

    def test_fresh_depth_protects_during_trade_window_warmup(self):
        self.ledger.submit_candidate(signal(),now_ms=NOW)
        self.event(1,quantity='30')
        book=features(2,'99.1','99.11','30');book['ready']=False
        book['reasons']=['TRADE_WINDOW_INCOMPLETE']
        result=self.ledger.process_market(book,NORMAL,now_ms=NOW+2)
        self.assertTrue(result['processed'])
        self.assertTrue(self.ledger.snapshot()['protection'][0]['state']['trigger_latched'])
        book=features(3,'99.1','99.11','30');book['ready']=False
        self.ledger.process_market(book,NORMAL,now_ms=NOW+3)
        self.assertEqual(self.ledger.snapshot()['positions'],[])
