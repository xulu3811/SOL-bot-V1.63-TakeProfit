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
        from test_p3_apts_shadow import RULES, NOW
        with tempfile.TemporaryDirectory() as folder:
            ledger = R2ShadowExecutor(Path(folder) / 'r2.sqlite', rules=RULES, fee_rate='0.00075')
            intent = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': 'r2-rebound-2',
                      'signal_id': 'rebound-risk-test', 'accepted': True, 'reasons': [],
                      'signal_time': NOW, 'expires_at': NOW+60000, 'entry_low': '78.75',
                      'entry_high': '78.98625', 'candidate_low': '77.92', 'stop_price': '77.86346'}
            order = ledger.submit_intent(intent, NOW)
            price = Decimal(order['limit_price'])
            notional = Decimal(order['quantity']) * price
            risk = notional * ((price - Decimal('77.86')) / price + Decimal('.0025'))
            self.assertGreaterEqual(notional, 100)
            self.assertLess(notional, 300)
            self.assertLessEqual(risk, 4)

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
                state=json.loads(db.execute('SELECT state_json FROM shadow_protection').fetchone()[0])
                self.assertEqual(float(state['stop_price']),99.05)

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
