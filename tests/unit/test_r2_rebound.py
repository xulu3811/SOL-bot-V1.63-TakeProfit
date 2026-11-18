import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from solbot.strategy.r2_rebound import advance
from solbot.storage.r2_store import R2Store
from solbot.storage.read_snapshot import publish

BASE = 1794500000000

def inputs(now, five_time=None, low='99', rsi=(15, 18), close='100'):
    five_time = now if five_time is None else five_time
    five = [dict(close_time=five_time-600000+i*300000, low=low, high='100', open='99', close=close) for i in range(3)]
    one = [dict(close_time=now-120000+i*60000, low=low, high='100', open='99', close=close) for i in range(3)]
    return {'5m':five,'1m':one}, {'5m':{'rsi6':list(rsi),'vwap20':[99.5],'atr14':[.2]}}

class R2Tests(unittest.TestCase):
    def test_rebound_order_below_legacy_minimum_respects_risk_budget(self):
        from decimal import Decimal
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from solbot.execution.shadow import ShadowRejected
        from test_p3_apts_shadow import RULES, NOW
        with tempfile.TemporaryDirectory() as folder:
            ledger = R2ShadowExecutor(Path(folder) / 'r2.sqlite', rules=RULES, fee_rate='0.00075')
            intent = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': 'r2-rebound-2',
                      'signal_id': 'rebound-risk-test', 'accepted': True, 'reasons': [],
                      'signal_time': NOW, 'expires_at': NOW+60000, 'entry_low': '78.75',
                      'entry_high': '78.98625', 'candidate_low': '77.92', 'stop_price': '77.86346', 'score': '75'}
            order = ledger.submit_intent(intent, NOW)
            price = Decimal(order['limit_price'])
            notional = Decimal(order['quantity']) * price
            risk = notional * ((price - Decimal('77.86')) / price + Decimal('.0025'))
            self.assertGreaterEqual(notional, 499)
            self.assertLessEqual(notional, 1000)
            self.assertLessEqual(risk, 25)
            # Aligning with SOL-bot Live: score=75 keeps score_cap=500 even when cash=750;
            # score=100 caps cleanly at available cash (~749 USDT).
            with ledger._transaction() as db:
                db.execute("UPDATE shadow_account SET cash='750',reserved='0'")
            intent_fb = dict(intent, signal_id='rebound-fallback-test', score='75')
            fb = ledger.submit_intent(intent_fb, NOW)
            fb_notional = Decimal(fb['quantity']) * Decimal(fb['limit_price'])
            self.assertGreaterEqual(fb_notional, Decimal('495'))
            self.assertLessEqual(fb_notional, Decimal('505'))
            with ledger._transaction() as db:
                db.execute("UPDATE shadow_account SET cash='750',reserved='0'")
                db.execute("DELETE FROM shadow_orders")
            intent_fb_max = dict(intent, signal_id='rebound-fallback-max', score='100')
            fb_max = ledger.submit_intent(intent_fb_max, NOW)
            fb_max_notional = Decimal(fb_max['quantity']) * Decimal(fb_max['limit_price'])
            self.assertGreater(fb_max_notional, Decimal('740'))
            self.assertLess(fb_max_notional, Decimal('755'))
            # Below 500 USDT available cash rejects opening
            with ledger._transaction() as db:
                db.execute("UPDATE shadow_account SET cash='450',reserved='0'")
            with self.assertRaises(ShadowRejected):
                ledger.submit_intent(dict(intent, signal_id='rebound-low-cash'), NOW)

    def test_paper_execution_retry_and_structure_stop(self):
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from test_p3_apts_shadow import RULES, NOW, features, NORMAL
        with tempfile.TemporaryDirectory() as folder:
            ledger=R2ShadowExecutor(Path(folder)/'r2.sqlite',rules=RULES,fee_rate='0.00075')
            intent={'symbol':'SOLUSDT','strategy_id':'R2','strategy_version':'r2-rebound-1',
                    'signal_id':'r2-test','accepted':True,'reasons':[], 'signal_time':NOW,
                    'expires_at':NOW+60000,'entry_low':'100','entry_high':'100.1',
                    'candidate_low':'99.1','stop_price':'99.05','stop_distance':'0.0095'}
            order=ledger.submit_intent(intent,NOW)
            ledger.process_market(features(1000,quantity='100'),NORMAL,now_ms=NOW+1000)
            repeat=ledger.submit_intent(intent,NOW+70000)
            self.assertEqual(order['order_id'],repeat['order_id'])
            ledger.process_market(features(2000,quantity='100'),NORMAL,now_ms=NOW+2000)
            with ledger._transaction() as db:
                pos = db.execute("SELECT entry_price FROM shadow_positions WHERE status='OPEN'").fetchone()
                state=json.loads(db.execute('SELECT state_json FROM shadow_protection').fetchone()[0])
                from decimal import Decimal
                from solbot.mathematics.protective_stop import initial_stop
                expected_stop = initial_stop(pos['entry_price'], RULES.tick_size, stop_distance='0.0095')
                self.assertEqual(Decimal(str(state['stop_price'])), expected_stop)

    def test_memory_and_restart_and_single_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'r2.sqlite'
            store=R2Store(path)
            candles,metrics=inputs(BASE)
            state,intent=store.step(candles,metrics,'DOWNTREND',BASE)
            self.assertEqual(state['state'],'WATCHING')
            store.close();store=R2Store(path)
            candles,metrics=inputs(BASE+1800000,low='99.1',rsi=(45,55))
            state,intent=store.step(candles,metrics,'DOWNTREND',BASE+1800000)
            self.assertEqual(state['state'],'ARMED')
            candles,metrics=inputs(BASE+1860000,five_time=BASE+1800000,low='99.1',rsi=(45,55),close='100.2')
            state,intent=store.step(candles,metrics,'DOWNTREND',BASE+1860000)
            self.assertIsNotNone(intent)
            self.assertEqual(store.step(candles,metrics,'DOWNTREND',BASE+1860000)[1],None)
            store.acknowledge(intent['signal_id'],{'order_id':'one'})
            store.acknowledge(intent['signal_id'],{'order_id':'one'})
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM r2_outbox').fetchone()[0],1)
            store.close()

    def test_expiry_and_low_invalidation(self):
        candles,metrics=inputs(BASE)
        state,_=advance(None,candles,metrics,'DOWNTREND',BASE)
        candles,metrics=inputs(BASE+7200000)
        self.assertEqual(advance(state,candles,metrics,'DOWNTREND',BASE+7200000)[0]['state'],'EXPIRED')
        state.update(state='ARMED')
        candles,metrics=inputs(BASE+60000,five_time=BASE,close='98')
        self.assertEqual(advance(state,candles,metrics,'DOWNTREND',BASE+60000)[0]['state'],'INVALIDATED')

    def test_range_regime_accepts_standard_5m_oversold_into_watching(self):
        candles, metrics = inputs(BASE, rsi=(24.0, 16.2))
        state, intent = advance(None, candles, metrics, 'RANGE', BASE)
        self.assertEqual(state['state'], 'WATCHING')
        self.assertIsNone(intent)

    def test_snapshot_contains_wal_commits_and_is_standalone(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.sqlite';target=Path(folder)/'read.sqlite'
            writer=sqlite3.connect(source)
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE items(value INTEGER)')
            writer.execute('INSERT INTO items VALUES(1)');writer.commit()
            publish(source,target)
            reader=sqlite3.connect(target.as_uri()+'?mode=ro&immutable=1',uri=True)
            self.assertEqual(reader.execute('SELECT value FROM items').fetchone()[0],1)
            self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0],'delete')
            reader.close();writer.close()

    def test_attach_reference_price_satisfies_percent_price_filter(self):
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from solbot.mathematics.contracts import SymbolRules
        from solbot.p3_runtime import attach_reference_price
        from test_p3_apts_shadow import NOW
        rules_doc = {
            'symbol': 'SOLUSDT',
            'status': 'TRADING',
            'isSpotTradingAllowed': True,
            'filters': [
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.01', 'minPrice': '0.01', 'maxPrice': '10000'},
                {'filterType': 'LOT_SIZE', 'stepSize': '0.01', 'minQty': '0.01', 'maxQty': '10000'},
                {'filterType': 'NOTIONAL', 'minNotional': '5', 'maxNotional': '1000000'},
                {'filterType': 'PERCENT_PRICE_BY_SIDE', 'bidMultiplierDown': '0.5', 'bidMultiplierUp': '1.2',
                 'askMultiplierDown': '0.8', 'askMultiplierUp': '2', 'avgPriceMins': 5},
            ],
        }
        rules = SymbolRules.from_exchange_info(rules_doc, 'SOLUSDT')
        with tempfile.TemporaryDirectory() as folder:
            ledger = R2ShadowExecutor(Path(folder) / 'r2.sqlite', rules=rules, fee_rate='0.00075')
            intent = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': 'r2-rebound-2',
                      'signal_id': 'percent-ref-test', 'accepted': True, 'reasons': [],
                      'signal_time': NOW, 'expires_at': NOW + 60000, 'entry_low': '78.09',
                      'entry_high': '78.32', 'candidate_low': '77.19', 'stop_price': '77.14'}
            attach_reference_price(intent, ledger.rules, {'midpoint': '78.10'}, None, NOW)
            order = ledger.submit_intent(intent, NOW)
            self.assertEqual(order['status'], 'PENDING')

    def test_climax_exhaustion_rebound_triggers_without_waiting_for_rsi_48_or_vwap(self):
        five = [
            {'close_time': BASE - 600000, 'open': '76.50', 'high': '76.60', 'low': '75.02', 'close': '75.20',
             'quote_volume': '100000', 'taker_buy_quote_volume': '35000'},
            {'close_time': BASE - 300000, 'open': '75.20', 'high': '75.79', 'low': '75.34', 'close': '75.60',
             'quote_volume': '60000', 'taker_buy_quote_volume': '31000'},
            {'close_time': BASE, 'open': '75.60', 'high': '75.92', 'low': '75.53', 'close': '75.85',
             'quote_volume': '50000', 'taker_buy_quote_volume': '28000'},
        ]
        one = [
            {'close_time': BASE - 120000, 'open': '75.60', 'high': '75.70', 'low': '75.55', 'close': '75.68'},
            {'close_time': BASE - 60000, 'open': '75.68', 'high': '75.80', 'low': '75.65', 'close': '75.78'},
            {'close_time': BASE, 'open': '75.78', 'high': '75.92', 'low': '75.75', 'close': '75.85'},
        ]
        # Step 1: enter WATCHING on the plunge bar (BASE - 600000)
        c0 = {'5m': [five[0], five[0], five[0]], '1m': [dict(one[0], close_time=BASE - 600000)] * 3}
        m0 = {'5m': {'rsi6': [25.0, 18.0, 11.4], 'rvol20': [1.2, 1.8, 2.8], 'vwap20': [77.50], 'atr14': [0.35]},
              '15m': {'rsi6': [18.5]}, '1h': {'rsi6': [22.0]}}
        state, intent = advance(None, c0, m0, 'DOWNTREND', BASE - 600000)
        self.assertEqual(state['state'], 'WATCHING')
        self.assertIsNone(intent)
        # Step 2: exhaustion turn bar at BASE triggers immediately even though RSI(6)=32.8 < 48 and close=75.85 < vwap20=77.20
        c1 = {'5m': five, '1m': one}
        m1 = {'5m': {'rsi6': [11.4, 21.5, 32.8], 'rvol20': [2.8, 1.4, 1.1], 'vwap20': [77.20], 'atr14': [0.35]},
              '15m': {'rsi6': [19.0, 20.5]}, '1h': {'rsi6': [21.0, 22.5]}}
        state, intent = advance(state, c1, m1, 'DOWNTREND', BASE)
        self.assertEqual(state['state'], 'TRIGGERED')
        self.assertIsNotNone(intent)
        self.assertEqual(intent['entry_low'], '75.85')
        from solbot.mathematics.decimal_utils import json_safe
        self.assertIsInstance(json_safe({'candidate': state}), dict)

    def test_data_reader_merges_r2_shadow_orders_and_positions(self):
        from solbot.app_api.data import DataReader
        from solbot.execution.p3_shadow import P3ShadowExecutor
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from test_p3_apts_shadow import RULES, NOW, features, NORMAL
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p3_db = root / 'p3.sqlite'
            r2_db = root / 'r2.sqlite'
            P3ShadowExecutor(p3_db, rules=RULES, fee_rate='0.00075')
            r2 = R2ShadowExecutor(r2_db, rules=RULES, fee_rate='0.00075')
            intent = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': 'r2-rebound-2',
                      'signal_id': 'r2-merge-1', 'accepted': True, 'reasons': [], 'signal_time': NOW,
                      'expires_at': NOW + 60000, 'entry_low': '100', 'entry_high': '100.1',
                      'candidate_low': '99.1', 'stop_price': '99.05', 'stop_distance': '0.0095'}
            r2.submit_intent(intent, NOW)
            r2.process_market(features(1000, quantity='100'), NORMAL, now_ms=NOW + 1000)
            status_path = root / 'shadow-status.json'
            status_path.write_text(json.dumps({'symbol': 'SOLUSDT', 'status': 'ready', 'updated_at': NOW + 1000,
                                               'shadow': {'last_quote_ms': NOW + 1000}}), encoding='utf-8')
            reader = DataReader(paths={'shadow': p3_db, 'r2_shadow': r2_db, 'shadow_status': status_path}, clock=lambda: NOW + 1000)
            orders = reader.shadow_orders()
            self.assertTrue(orders['available'])
            self.assertEqual(len(orders['items']), 1)
            self.assertEqual(orders['items'][0]['strategy_id'], 'R2')
            st = reader.shadow_status()
            self.assertEqual(st['total_trades'], 1)
            self.assertEqual(st['active_positions'], 1)
            self.assertEqual(len(st['positions']), 1)

    def test_cross_shadow_entry_guard_prevents_double_buy_and_syncs_with_live(self):
        from decimal import Decimal
        from solbot.app_api.binance_account import AccountVault
        from solbot.execution.p3_shadow import P3ShadowExecutor
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from solbot.execution.shadow import ShadowRejected
        from solbot.mathematics.apts_exit import APTSState, update_protection
        from solbot.p3_runtime import acquire_cross_shadow_entry_guard, release_cross_shadow_entry_guard
        from test_p3_apts_shadow import RULES, NOW, context, NORMAL

        # 1. Live AccountVault rejects T1 in DOWNTREND while accepting R2
        t1_down = {
            'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'accepted': True,
            'regime': 'DOWNTREND', 'signal_id': 't1-down-1', 'signal_time': NOW,
            'expires_at': NOW + 300_000, 'entry_low': '74.08', 'entry_high': '74.15', 'score': '94.3',
        }
        self.assertIsNone(AccountVault._parse_signal_payload(t1_down, set(), ('T1', 'R2'), NOW))
        t1_up = dict(t1_down, regime='UPTREND', signal_id='t1-up-1')
        self.assertIsNotNone(AccountVault._parse_signal_payload(t1_up, set(), ('T1', 'R2'), NOW))

        # 2. APTS S1 triggers at +0.80% (e.g. 75.45 -> 76.19 is +0.98% >= +0.80%), locking profit floor 75.63
        st_1616 = APTSState.create(
            context(NOW), '75.45', '6.555', '0.01',
            entry_fee_rate='0.00075', exit_fee_rate='0.00075', slippage_rate='0.00075',
            stop_distance='0.0067', entry_time_ms=NOW,
        )
        dec_1745 = update_protection(st_1616, context(NOW + 1000), '76.19', NORMAL, tick_size='0.01')
        self.assertEqual(dec_1745.state.stage, 'S1_PROFIT_PROTECTION')
        self.assertEqual(dec_1745.stop_price, Decimal('75.63'))

        # 3. Cross-shadow guard blocks simultaneous T1 + R2 double buy across separate DBs
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_dir = root / 'database'
            r2_dir = root / 'r2'
            db_dir.mkdir(parents=True, exist_ok=True)
            r2_dir.mkdir(parents=True, exist_ok=True)
            market_db = db_dir / 'market.sqlite'
            market_db.touch()
            p3_db = db_dir / 'sol-shadow.sqlite'
            r2_db = r2_dir / 'r2-v2-shadow.sqlite'
            P3ShadowExecutor(p3_db, rules=RULES, fee_rate='0.00075', minimum_notional='500', maximum_notional='1000')
            R2ShadowExecutor(r2_db, rules=RULES, fee_rate='0.00075')

            # First strategy claims entry at NOW
            claimed = acquire_cross_shadow_entry_guard(
                p3_db, market_db, strategy_id='T1', signal_id='sig-t1-1', signal_time=NOW, now_ms=NOW
            )
            self.assertTrue(claimed)
            # Second strategy (R2) at the same millisecond is blocked by CROSS_STRATEGY_COOLDOWN
            with self.assertRaises(ShadowRejected) as ctx_err:
                acquire_cross_shadow_entry_guard(
                    r2_db, market_db, strategy_id='R2', signal_id='sig-r2-1', signal_time=NOW, now_ms=NOW + 50
                )
            self.assertEqual(ctx_err.exception.code, 'CROSS_STRATEGY_COOLDOWN')

            # If T1 submission fails and releases guard, R2 can then acquire
            release_cross_shadow_entry_guard(market_db, 'sig-t1-1')
            claimed_r2 = acquire_cross_shadow_entry_guard(
                r2_db, market_db, strategy_id='R2', signal_id='sig-r2-1', signal_time=NOW, now_ms=NOW + 100
            )
            self.assertTrue(claimed_r2)

    def test_r2_order_ttl_300s_and_shadow_chase_pending_fills_breakout(self):
        from decimal import Decimal
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from test_p3_apts_shadow import RULES, NOW, NORMAL, features
        candles, metrics = inputs(BASE)
        state, _ = advance(None, candles, metrics, 'DOWNTREND', BASE)
        candles, metrics = inputs(BASE + 1800000, low='99.1', rsi=(45, 55))
        state, _ = advance(state, candles, metrics, 'DOWNTREND', BASE + 1800000)
        candles, metrics = inputs(BASE + 1860000, five_time=BASE + 1800000, low='99.1', rsi=(45, 55), close='100.2')
        state, intent = advance(state, candles, metrics, 'DOWNTREND', BASE + 1860000)
        self.assertIsNotNone(intent)
        self.assertEqual(intent['expires_at'], (BASE + 1860000) + 300_000)

        # Reproduce 10:08 R2 scenario in shadow: initial ask=71.85, jumps to 71.93 -> chase_pending lifts limit to 71.97 and fills
        with tempfile.TemporaryDirectory() as folder:
            r2 = R2ShadowExecutor(Path(folder) / 'r2_chase.sqlite', rules=RULES, fee_rate='0.00075')
            r2.apply_trading_config('1500', '1500', 2)
            sig = {
                'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': 'r2-rebound-2',
                'signal_id': 'r2-shadow-chase-1', 'accepted': True, 'reasons': [],
                'signal_time': NOW, 'expires_at': NOW + 300_000,
                'entry_low': '71.85', 'entry_high': '72.06555',
                'candidate_low': '71.17', 'stop_price': '71.12',
                'stop_distance': '0.010', 'score': '85',
            }
            order = r2.submit_intent(sig, NOW, bid='71.84', ask='71.85')
            self.assertEqual(Decimal(order['limit_price']), Decimal('71.89'))

            # At +1s ask jumped to 71.93 (above 71.89) -> still resting before 5s chase interval
            f1 = features(1000, bid='71.92', ask='71.93', quantity='300')
            self.assertEqual(r2.chase_pending(f1, NOW + 1000), [])
            res1 = r2.process_market(f1, NORMAL, now_ms=NOW + 1000)
            self.assertEqual(res1['fills'], [])

            # At +5s ask=71.93 (<= entry_high 72.06555) -> chase_pending lifts limit_price to 71.97 and fills!
            f2 = features(5000, bid='71.92', ask='71.93', quantity='300')
            chased = r2.chase_pending(f2, NOW + 5000)
            self.assertEqual(len(chased), 1)
            self.assertEqual(Decimal(chased[0]['limit_price']), Decimal('71.97'))
            res2 = r2.process_market(f2, NORMAL, now_ms=NOW + 5000)
            self.assertEqual(len(res2['fills']), 1)
            self.assertEqual(res2['fills'][0]['side'], 'BUY')

    def test_shadow_downtrend_uncertain_r2_outranks_t2(self):
        from contextlib import closing
        import json
        import sqlite3
        from solbot.execution.p3_shadow import P3ShadowExecutor
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from solbot.execution.shadow import ShadowRejected
        from solbot.p3_runtime import _has_higher_priority_strategy_signal, acquire_cross_shadow_entry_guard
        from test_p3_apts_shadow import RULES, NOW

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_dir = root / 'database'
            r2_dir = root / 'r2'
            db_dir.mkdir(parents=True, exist_ok=True)
            r2_dir.mkdir(parents=True, exist_ok=True)
            market_db = db_dir / 'market.sqlite'
            market_db.touch()
            p3_db = db_dir / 'sol-shadow.sqlite'
            r2_db = r2_dir / 'r2-v2-shadow.sqlite'
            strat_db = db_dir / 'sol-shadow-strategy.sqlite'
            r2_state_db = r2_dir / 'r2-state.sqlite'
            P3ShadowExecutor(p3_db, rules=RULES, fee_rate='0.00075')
            R2ShadowExecutor(r2_db, rules=RULES, fee_rate='0.00075')
            with closing(sqlite3.connect(strat_db)) as db, db:
                db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_time INTEGER, accepted INTEGER, payload_json TEXT)')
                t2_down = {'symbol': 'SOLUSDT', 'strategy_id': 'T2', 'regime': 'DOWNTREND', 'signal_id': 't2-d', 'signal_time': NOW}
                db.execute('INSERT INTO decisions(symbol,strategy_id,signal_time,accepted,payload_json) VALUES(?,?,?,?,?)',
                           ('SOLUSDT', 'T2', NOW, 1, json.dumps(t2_down)))
            with closing(sqlite3.connect(r2_state_db)) as db, db:
                db.execute("CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', outcome TEXT)")
                r2_sig = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'regime': 'DOWNTREND', 'signal_id': 'r2-d', 'signal_time': NOW}
                db.execute("INSERT INTO r2_outbox(signal_id,payload,status) VALUES(?,?,?)", ('r2-d', json.dumps(r2_sig), 'PENDING'))

            # T2 in DOWNTREND/UNCERTAIN must NOT block R2, while active R2 DOES block T2.
            self.assertFalse(_has_higher_priority_strategy_signal(strat_db, NOW, 900_000))
            with self.assertRaises(ShadowRejected) as ctx_err:
                acquire_cross_shadow_entry_guard(
                    p3_db, market_db, strategy_id='T2', signal_id='t2-d', signal_time=NOW, now_ms=NOW + 100, regime='DOWNTREND',
                )
            self.assertEqual(ctx_err.exception.code, 'CROSS_STRATEGY_COOLDOWN')
            self.assertTrue(acquire_cross_shadow_entry_guard(
                r2_db, market_db, strategy_id='R2', signal_id='r2-d', signal_time=NOW, now_ms=NOW + 150, strategy_db=strat_db,
            ))

    def test_overextended_bounce_rsi_and_uncertain_stop_distance_gates(self):
        # 1. Enter WATCHING at 15:04:59 on 74.60 candidate_low
        candles, metrics = inputs(BASE, low='74.60', rsi=(22.0, 13.2), close='74.71')
        state, intent = advance(None, candles, metrics, 'UNCERTAIN', BASE)
        self.assertEqual(state['state'], 'WATCHING')
        self.assertIsNone(intent)

        # 2. At +5m (15:09:59), RSI crosses 48 (45.7 -> 64.1), setting recovery_time; at +10m (15:14:59), 3rd bar closes at 75.24 above VWAP (74.98) with RSI(6)=74.1
        state['recovery_time'] = BASE + 300_000
        five_1514 = [
            {'close_time': BASE, 'open': '74.68', 'high': '74.87', 'low': '74.60', 'close': '74.85'},
            {'close_time': BASE + 300_000, 'open': '74.86', 'high': '75.07', 'low': '74.86', 'close': '75.05'},
            {'close_time': BASE + 600_000, 'open': '75.06', 'high': '75.24', 'low': '75.04', 'close': '75.24'},
        ]
        one_1514 = [
            {'close_time': BASE + 480_000, 'open': '75.10', 'high': '75.18', 'low': '75.08', 'close': '75.15'},
            {'close_time': BASE + 540_000, 'open': '75.15', 'high': '75.20', 'low': '75.14', 'close': '75.19'},
            {'close_time': BASE + 600_000, 'open': '75.19', 'high': '75.24', 'low': '75.18', 'close': '75.24'},
        ]
        m_1514 = {
            '5m': {'rsi6': [45.7, 64.1, 74.1], 'vwap20': [74.979], 'atr14': [0.19]},
            '15m': {'rsi6': [61.0]},
            '1m': {'rsi6': [91.0]},
        }
        state, intent = advance(state, {'5m': five_1514, '1m': one_1514}, m_1514, 'UNCERTAIN', BASE + 600_000)
        self.assertEqual(state['state'], 'ARMED')
        self.assertIsNone(intent)

        # 3. At 15:15:59 (+60s), 1m breaks out to 75.29 with 5m RSI=74.1 > 68.0 -> blocked by RSI_OVEREXTENDED
        one_1515 = [
            one_1514[1],
            one_1514[2],
            {'close_time': BASE + 660_000, 'open': '75.25', 'high': '75.32', 'low': '75.24', 'close': '75.29'},
        ]
        m_1515 = {
            '5m': {'rsi6': [45.7, 64.1, 74.1], 'vwap20': [74.979], 'atr14': [0.19]},
            '15m': {'rsi6': [61.0]},
            '1m': {'rsi6': [94.7]},
        }
        state_over, intent_over = advance(state, {'5m': five_1514, '1m': one_1515}, m_1515, 'UNCERTAIN', BASE + 660_000)
        self.assertIsNone(intent_over)
        self.assertEqual(state_over['state'], 'WATCHING')
        self.assertEqual(state_over['reason'], 'RSI_OVEREXTENDED')

        # 4. Even if RSI is moderate (5m=59.0, 15m=45.0, 1m=75.0), classic breakout in UNCERTAIN with stop_dist=0.95% > 0.85% is blocked
        m_wide = {
            '5m': {'rsi6': [45.7, 59.0], 'vwap20': [74.979], 'atr14': [0.19]},
            '15m': {'rsi6': [45.0]},
            '1m': {'rsi6': [75.0]},
        }
        state_wide, intent_wide = advance(state, {'5m': five_1514, '1m': one_1515}, m_wide, 'UNCERTAIN', BASE + 660_000)
        self.assertIsNone(intent_wide)
        self.assertEqual(state_wide['state'], 'WATCHING')
        self.assertEqual(state_wide['reason'], 'STOP_DISTANCE_TOO_WIDE')

        # 5. Tight classic breakout in UNCERTAIN (candidate_low=74.75 -> stop_dist ~0.76% <= 0.85%) with moderate RSI triggers cleanly
        state_tight_in = dict(state, candidate_low='74.75')
        state_tight, intent_tight = advance(state_tight_in, {'5m': five_1514, '1m': one_1515}, m_wide, 'UNCERTAIN', BASE + 660_000)
        self.assertIsNotNone(intent_tight)
        self.assertEqual(state_tight['state'], 'TRIGGERED')



