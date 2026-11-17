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
        self.assertEqual(self.step(state,'100.79',1).state.stage,'S0_INITIAL_PROTECTION')
        state=self.step(state,'100.80',2).state
        self.assertEqual(state.stage,'S1_PROFIT_PROTECTION')
        state=self.step(state,'101.2',3).state
        self.assertEqual(state.stage,'S1_PROFIT_PROTECTION')
        state=self.step(state,'101.21',4).state
        self.assertEqual(state.stage,'S2_ADAPTIVE_TRAILING')

    def test_s1_dynamic_lock_and_overbought_exhaustion_near_one_point_two_percent(self):
        state=APTSState.create(context(NOW),'71.85','18.912','.01',entry_fee_rate='0.00075',exit_fee_rate='0.00075',slippage_rate='0.0005')
        s1_mid=self.step(state,'72.43',1,NORMAL)
        self.assertEqual(s1_mid.state.stage,'S1_PROFIT_PROTECTION')
        self.assertEqual(s1_mid.stop_price,Dec('72.01'))
        # Below +1.10% stays at fixed net-profit floor (72.01); at >= +1.10% (72.65), dynamic S1 trailing raises stop.
        s1_below=self.step(s1_mid.state,'72.64',2,NORMAL)
        self.assertEqual(s1_below.stop_price,Dec('72.01'))
        s1_dyn=self.step(s1_below.state,'72.65',3,NORMAL)
        self.assertEqual(s1_dyn.state.stage,'S1_PROFIT_PROTECTION')
        self.assertEqual(s1_dyn.stop_price,Dec('72.26'))
        # Climb to 72.74 (+1.24% > +1.20%) enters S2 (< 2.0% exhaustion tighten threshold).
        s2_peak=self.step(s1_dyn.state,'72.74',4,TrendEvidence(ready=True,higher_low_1m=True,higher_low_5m=True,rvol=Dec('1.8'),buy_ratio=Dec('0.60'),previous_buy_ratio=Dec('0.55'),rsi=Dec('89.2'),previous_rsi=Dec('84.0'),atr=Dec('0.25'),previous_atr=Dec('0.20')))
        self.assertEqual(s2_peak.state.stage,'S2_ADAPTIVE_TRAILING')
        exhausted=TrendEvidence(ready=True,higher_low_1m=True,higher_low_5m=True,rvol=Dec('1.5'),buy_ratio=Dec('0.361'),previous_buy_ratio=Dec('0.671'),rsi=Dec('75.2'),previous_rsi=Dec('87.4'),atr=Dec('0.25'),previous_atr=Dec('0.20'))
        s2_no_tight=self.step(s2_peak.state,'72.52',5,exhausted)
        self.assertFalse(s2_no_tight.exit_required)
        self.assertEqual(s2_no_tight.stop_price,Dec('72.26'))
        # Climb to 73.30 (+2.02% >= +2.00%): 1st exhaustion bar holds prior band, 2nd consecutive 1m bar tightens to 0.40% (73.01).
        s2_high=self.step(s2_no_tight.state,'73.30',10,TrendEvidence(ready=True,higher_low_1m=True,higher_low_5m=True,rvol=Dec('1.8'),buy_ratio=Dec('0.60'),previous_buy_ratio=Dec('0.55'),rsi=Dec('85.0'),previous_rsi=Dec('80.0'),atr=Dec('0.25'),previous_atr=Dec('0.20')))
        s2_bar1=self.step(s2_high.state,'73.15',15,exhausted)
        self.assertIn('S2_EXHAUSTION_PENDING_CONFIRMATION',s2_bar1.reasons)
        self.assertLess(s2_bar1.stop_price,Dec('73.01'))
        s2_bar2=self.step(s2_bar1.state,'73.10',60_015,exhausted)
        self.assertFalse(s2_bar2.exit_required)
        self.assertEqual(s2_bar2.stop_price,Dec('73.01'))
        self.assertIn('S2_EXHAUSTION_TRAILING_TIGHTENED',s2_bar2.reasons)
        s2_hit=self.step(s2_bar2.state,'72.95',60_020,exhausted)
        self.assertTrue(s2_hit.exit_required)

    def test_post_5m_intraday_blowoff_sets_failed_new_high(self):
        from solbot.mathematics.trend_exhaustion import from_closed_inputs
        from solbot.storage.models import INTERVAL_MS
        as_of=1794600000000+180_000-1  # 3 minutes into current 5m bar
        base_5m=(as_of//300_000)*300_000  # start of open 5m bar
        candles={}
        for iv,step in INTERVAL_MS.items():
            end_close=((as_of+1)//step)*step-1
            rows=[]
            for idx in range(70):
                c_end=end_close-(69-idx)*step
                c_open=c_end-step+1
                o,h,low,c='71.80','72.10','71.70','72.00'
                if iv=='1m' and c_open>=base_5m:
                    rel=(c_open-base_5m)//60_000
                    if rel==0:o,h,low,c='72.00','72.40','71.98','72.35'
                    elif rel==1:o,h,low,c='72.35','72.75','72.32','72.70'
                    else:o,h,low,c='72.70','72.72','72.38','72.40'
                rows.append({
                    'symbol':'SOLUSDT','interval':iv,'open_time':c_open,'close_time':c_end,'is_closed':True,
                    'open':o,'high':h,'low':low,'close':c,'volume':'100','quote_volume':'7200','taker_buy_quote_volume':'3600',
                })
            candles[iv]=rows
        ev=from_closed_inputs('SOLUSDT',candles,{'symbol':'SOLUSDT','ready':True,'buy_ratio':'0.35'},as_of)
        self.assertTrue(ev.ready)
        self.assertTrue(ev.failed_new_high)
        self.assertEqual(classify(ev),'WEAKENING')

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
        held2=self.step(held.state,'82.85',2,evidence)
        self.assertFalse(held2.exit_required)
        self.assertGreater(held2.stop_price,held.stop_price)
        self.assertIn('S0_REVERSAL_STOP_TIGHTENED',held2.reasons)
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

    def test_intraday_main_force_breakout_survives_minor_pullback_and_locks_climax_top(self):
        state=APTSState.create(context(),'67.89','7.365','.01',stop_distance='0.011')
        main_force=TrendEvidence(
            ready=True,trend_4h=False,trend_1h=False,trend_15m=False,
            higher_low_1m=True,higher_low_5m=True,
            rvol=Dec('2.11'),buy_ratio=Dec('0.6114'),previous_buy_ratio=Dec('0.5201'),
            rsi=Dec('78.5'),previous_rsi=Dec('61.0'),atr=Dec('0.57'),previous_atr=Dec('0.48'),
        )
        self.assertEqual(classify(main_force),'STRONG_BREAKOUT')
        # Surge to 69.72 (+2.70%) enters S2 with 1.20% ATR trailing band (stop 68.89)
        d_surge=self.step(state,'69.72',1,main_force)
        self.assertEqual(d_surge.state.stage,'S2_ADAPTIVE_TRAILING')
        self.assertEqual(d_surge.stop_price,Dec('68.89'))
        # Minor 1m pullback to 69.27 (-0.65% from high, 5m higher-low intact, buy_ratio cooldown to 0.52)
        pullback_ev=replace(main_force,higher_low_1m=False,buy_ratio=Dec('0.52'),previous_buy_ratio=Dec('0.6114'),rsi=Dec('71.0'),previous_rsi=Dec('78.5'))
        self.assertEqual(classify(pullback_ev),'NORMAL_UPTREND')
        d_dip=self.step(d_surge.state,'69.27',2,pullback_ev)
        self.assertFalse(d_dip.exit_required)
        self.assertLess(d_dip.stop_price,Dec('69.27'))
        # Continuation wave to 70.95 (+4.51%)
        d_cont=self.step(d_dip.state,'70.95',3,main_force)
        self.assertFalse(d_cont.exit_required)
        self.assertEqual(d_cont.stop_price,Dec('70.10'))
        # Main-force exhaustion at top (failed_new_high + buy_ratio drops to 0.42): bar 1 holds 1.2% band (70.10), bar 2 confirms 0.40% tighten (70.67)
        exhaust_ev=replace(main_force,failed_new_high=True,adverse_move=True,buy_ratio=Dec('0.42'),previous_buy_ratio=Dec('0.61'),rsi=Dec('62.0'),previous_rsi=Dec('75.0'))
        self.assertEqual(classify(exhaust_ev),'WEAKENING')
        d_pending=self.step(d_cont.state,'70.80',4,exhaust_ev)
        self.assertFalse(d_pending.exit_required)
        self.assertEqual(d_pending.stop_price,Dec('70.10'))
        self.assertIn('S2_EXHAUSTION_PENDING_CONFIRMATION',d_pending.reasons)
        # Same-minute poll does not double-count exhaustion bar.
        d_same_min=self.step(d_pending.state,'70.79',100,exhaust_ev)
        self.assertEqual(d_same_min.state.exhaustion_hits,1)
        self.assertEqual(d_same_min.stop_price,Dec('70.10'))
        # Second consecutive 1m bar confirms tightening to 0.40% (70.67).
        d_tight=self.step(d_same_min.state,'70.80',60_004,exhaust_ev)
        self.assertFalse(d_tight.exit_required)
        self.assertEqual(d_tight.stop_price,Dec('70.67'))
        self.assertIn('S2_EXHAUSTION_TRAILING_TIGHTENED',d_tight.reasons)
        d_exit=self.step(d_tight.state,'70.66',60_005,exhaust_ev)
        self.assertTrue(d_exit.exit_required)

    def test_decoupled_flow_smoothing_removes_45_cliff_and_preserves_s3_reversal(self):
        from solbot.mathematics.trend_exhaustion import FlowSmoother, from_closed_inputs, smooth_micro_features
        from solbot.storage.models import INTERVAL_MS
        as_of=1794600000000+180_000-1
        candles={}
        for iv,step in INTERVAL_MS.items():
            end_close=((as_of+1)//step)*step-1
            rows=[]
            for idx in range(70):
                c_end=end_close-(69-idx)*step
                c_open=c_end-step+1
                base_p=Dec('70.00')+Dec(idx)*Dec('0.02')
                o,h,low,c=str(base_p),str(base_p+Dec('0.12')),str(base_p-Dec('0.03')),str(base_p+Dec('0.08'))
                rows.append({
                    'symbol':'SOLUSDT','interval':iv,'open_time':c_open,'close_time':c_end,'is_closed':True,
                    'open':o,'high':h,'low':low,'close':c,'volume':'200','quote_volume':'14000','taker_buy_quote_volume':'9800',
                })
            candles[iv]=rows
        smoother=FlowSmoother()
        for k,ratio in enumerate(('0.64','0.62','0.60','0.63')):
            smooth_micro_features({'symbol':'SOLUSDT','ready':True,'buy_ratio':ratio,'exchange_time_ms':as_of-10_000+k*1000},smoother)
        # Single-tick 0.44 dip stays STRONG_BREAKOUT under decoupled EWMA + smooth [0.35, 0.55] blend.
        dip=smooth_micro_features({'symbol':'SOLUSDT','ready':True,'buy_ratio':'0.441','exchange_time_ms':as_of},smoother)
        ev_dip=from_closed_inputs('SOLUSDT',candles,dip,as_of)
        self.assertEqual(classify(ev_dip),'STRONG_BREAKOUT')
        self.assertEqual(ev_dip.raw_buy_ratio,Dec('0.441'))
        # When structure breaks + RSI plunges + raw buy_ratio < 0.40, CONFIRMED_REVERSAL fires without EWMA lag.
        ev_rev=replace(ev_dip,structure_broken=True,raw_buy_ratio=Dec('0.34'),previous_buy_ratio=Dec('0.60'),rsi=Dec('40'),previous_rsi=Dec('52'))
        self.assertEqual(classify(ev_rev),'CONFIRMED_REVERSAL')

    def test_legacy_apts_state_from_dict_backfills_new_confirm_fields(self):
        st=APTSState.create(context(),'100','3','.01')
        legacy=st.asdict()
        for k in ('exhaustion_hits','exhaustion_last_bar_ms','exhaustion_confirm_bars','last_normal_distance'):
            legacy.pop(k,None)
        restored=APTSState.from_dict(legacy)
        self.assertEqual(restored.exhaustion_confirm_bars,2)
        self.assertEqual(restored.exhaustion_hits,0)
        self.assertEqual(restored.last_normal_distance,Dec('.005'))


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
