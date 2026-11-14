from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.app_api.application import create_app
from solbot.app_api.auth import AuthStore
from solbot.app_api.binance_account import AccountVault
from solbot.app_api.data import DataReader
from solbot.app_api.notifications import NotificationStore
from solbot.storage.schema import migrations_for
from tests.unit.test_binance_account_input import FakeTransport, restrictions


class AppApiSyncUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 1800000000000

    def test_strategies_prioritizes_t1_over_newer_t2_and_emits_engines_and_gates(self):
        strat_db = self.root / 'sol-shadow-strategy.sqlite'
        with closing(sqlite3.connect(strat_db)) as conn:
            with conn as db:
                db.execute(
                    'CREATE TABLE decisions(id INTEGER PRIMARY KEY, symbol TEXT, strategy_id TEXT, '
                    'signal_time INTEGER, candidate_time INTEGER, accepted INTEGER, payload_json TEXT)'
                )
                t1 = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'score': 41.5, 'priority': 'REJECT',
                    'score_factors': {'trend': 0.0, 'momentum': 10.0, 'participation': 15.0, 'structure': 10.0, 'execution': 6.5},
                    'regime': 'DOWNTREND', 'reasons': ['REGIME_NOT_UPTREND', 'HIGHER_TIMEFRAME_WEAK_TREND', 'SCORE_BELOW_75'],
                    'stop_distance': '0.010', 'stop_price': '79.20', 'entry_low': '80.00', 'entry_high': '80.08',
                    'signal_time': self.now, 'expires_at': self.now + 300000, 'accepted': False, 'signal_id': 'sig-t1',
                    'indicators': {'1h': {'adx14': 19.5}, '4h': {'ema20': 82.0, 'ema50': 84.0}},
                }
                r1 = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'R1', 'score': 12.0, 'priority': 'REJECT',
                    'score_factors': {'trend': 0.0, 'momentum': 0.0, 'participation': 0.0, 'structure': 0.0, 'execution': 12.0},
                    'regime': 'DOWNTREND', 'reasons': ['R1_REQUIRES_RANGE', 'MACRO_DOWNTREND_GUARD', 'SCORE_BELOW_75'],
                    'entry_low': '80.00', 'entry_high': '80.08', 'signal_time': self.now, 'accepted': False, 'signal_id': 'sig-r1',
                }
                t2 = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T2', 'score': 22.9768, 'priority': 'REJECT',
                    'score_factors': {'trend': 0.0, 'momentum': 0.0, 'participation': 0.0, 'structure': 10.0, 'execution': 12.9768},
                    'regime': 'DOWNTREND', 'reasons': ['FOUR_HOUR_DOWNTREND', 'SCORE_BELOW_75'],
                    'stop_distance': '0.008', 'stop_price': '79.36', 'entry_low': '80.00', 'entry_high': '80.08',
                    'signal_time': self.now, 'accepted': False, 'signal_id': 'sig-t2',
                }
                # Accepted T1 10 minutes ago -> triggers 50 min remaining cooldown
                t1_old = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'score': 82.0, 'priority': 'BUY',
                    'score_factors': {'trend': 20.0, 'momentum': 22.0, 'participation': 18.0, 'structure': 12.0, 'execution': 10.0},
                    'regime': 'UPTREND', 'reasons': [], 'stop_distance': '0.010', 'entry_low': '79.00',
                    'signal_time': self.now - 600_000, 'accepted': True, 'signal_id': 'sig-t1-prev',
                }
                db.execute('INSERT INTO decisions VALUES(1,?,?,?,?,?,?)', ('SOLUSDT', 'T1', self.now - 600_000, self.now - 600_000, 1, json.dumps(t1_old)))
                db.execute('INSERT INTO decisions VALUES(2,?,?,?,?,?,?)', ('SOLUSDT', 'T1', self.now, self.now, 0, json.dumps(t1)))
                db.execute('INSERT INTO decisions VALUES(3,?,?,?,?,?,?)', ('SOLUSDT', 'R1', self.now, self.now, 0, json.dumps(r1)))
                db.execute('INSERT INTO decisions VALUES(4,?,?,?,?,?,?)', ('SOLUSDT', 'T2', self.now, self.now, 0, json.dumps(t2)))
        reader = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda: self.now)
        res = reader.strategies('SOLUSDT', 10)
        self.assertTrue(res['available'])
        self.assertEqual(res['live_strategy_id'], 'T1')
        self.assertEqual(res['allowed_live_strategies'], ['T1'])
        self.assertEqual(res['latest']['strategy_id'], 'T1')
        self.assertEqual(res['latest']['regime'], 'DOWNTREND')
        self.assertEqual(res['latest']['stop_distance'], '0.010')
        self.assertIn('HIGHER_TIMEFRAME_WEAK_TREND', res['latest']['reasons'])
        self.assertFalse(res['latest']['gates']['adx_1h_ge_23'])
        self.assertFalse(res['latest']['gates']['cooldown_ok'])
        self.assertEqual(res['latest']['cooldown_remaining_ms'], 3_000_000)
        self.assertEqual(set(res['engines']), {'T1', 'T2', 'R1', 'R2'})
        self.assertTrue(res['engines']['T1']['live_enabled'])
        self.assertFalse(res['engines']['T2']['live_enabled'])
        self.assertFalse(res['engines']['R1']['live_enabled'])
        self.assertFalse(res['engines']['R1']['gates']['macro_4h_guard'])

    def test_markets_reports_bearish_ema_alignment_and_live_micro_snapshot(self):
        market_db = self.root / 'market.sqlite'
        with closing(sqlite3.connect(market_db)) as conn:
            with conn as db:
                for sql in migrations_for('SOLUSDT')[1]:
                    db.execute(sql)
                # Downtrend closes so ema20 < ema50
                for n in range(120):
                    opened = self.now - (119 - n) * 900_000
                    close = 100.0 - n * 0.15
                    db.execute(
                        'INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('SOLUSDT', '15m', opened, opened + 899999, str(close + 0.1), str(close + 0.2), str(close - 0.2),
                         str(close), '50', '4000', 10, '25', '2000', 1, 'rest', self.now, None)
                    )
        micro_file = self.root / 'sol-micro-status.json'
        micro_file.write_text(json.dumps({
            'symbol': 'SOLUSDT', 'ready': True, 'bid': '80.47', 'ask': '80.48',
            'buy_ratio': '0.58', 'imbalance': '0.16', 'spread_bps': '1.24',
            'feature_generated_time_ms': self.now - 500, 'received_time_ms': self.now - 500,
        }), encoding='utf-8')
        reader = DataReader({'SOLUSDT_market': market_db, 'micro_status': micro_file}, clock=lambda: self.now)
        full = reader.markets('SOLUSDT', '15m', 60)
        self.assertEqual(full['ema_alignment'], 'BEARISH')
        self.assertLess(full['ema_spread_pct'], 0)
        self.assertEqual(full['volume_24h_asset'], 'SOL')
        self.assertTrue(full['micro']['ready'])
        self.assertFalse(full['micro']['stale'])
        self.assertEqual(full['micro']['bid'], '80.47')
        self.assertEqual(full['micro']['spread_bps'], '1.24')
        summary = reader.market_summary('SOLUSDT', '15m')
        self.assertEqual(summary['ema_alignment'], 'BEARISH')
        self.assertTrue(summary['micro']['ready'])

    def test_app_endpoints_normalize_identity_expose_readiness_orders_and_shadow_pnl(self):
        from tests.unit.test_p6_independent_qa import DeviceAuthenticationQA, FakeControl
        ledger = AllocationLedger(self.root / 'guardian.sqlite')
        shadow_db = self.root / 'shadow.sqlite'
        with closing(sqlite3.connect(shadow_db)) as conn:
            with conn as db:
                db.execute('CREATE TABLE execution_owner(kind TEXT, account_id TEXT, symbol TEXT)')
                db.execute("INSERT INTO execution_owner VALUES('SHADOW_P3','SOL-shadow','SOLUSDT')")
                db.execute('CREATE TABLE shadow_account(singleton INTEGER, cash TEXT, last_equity TEXT, fee_rate TEXT, bid TEXT, ask TEXT)')
                db.execute("INSERT INTO shadow_account VALUES(1,'1700.00','2006.00','0.00075','82.00','82.01')")
                db.execute('CREATE TABLE shadow_positions(position_id TEXT PRIMARY KEY, account_id TEXT, symbol TEXT, strategy_id TEXT, signal_id TEXT, quantity TEXT, entry_price TEXT, entry_cost TEXT, entry_fee TEXT, status TEXT, opened_at INTEGER, closed_at INTEGER, exit_price TEXT, exit_fee TEXT, realized_pnl TEXT, close_reason TEXT)')
                db.execute("INSERT INTO shadow_positions(position_id,account_id,symbol,strategy_id,signal_id,quantity,entry_price,entry_cost,entry_fee,status,opened_at) VALUES('pos-1','SOL-shadow','SOLUSDT','T1','sig-1','3.0','80.00','240.00','0.18','OPEN',?)", (self.now - 3_600_000,))
                db.execute('CREATE TABLE shadow_orders(order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, status TEXT, fill_price TEXT, limit_price TEXT, quantity TEXT, fee TEXT, created_at INTEGER)')
                db.execute('CREATE TABLE shadow_protection(position_id TEXT PRIMARY KEY, state_json TEXT, status TEXT, updated_at INTEGER)')
                db.execute(
                    'INSERT INTO shadow_protection VALUES(?,?,?,?)',
                    ('pos-1', json.dumps({'stage': 'S1_PROFIT_PROTECTION', 'stop_price': '80.12', 'reversal_streak': 1, 'reversal_confirm_ticks': 2, 'entry_time_ms': self.now - 3_600_000}), 'OPEN', self.now),
                )

        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='1.61', sol_free='0.0', sol_locked='0.0')
        vault = AccountVault(self.root, clock=lambda: self.now, transport=transport)
        vault.save('A' * 64, 'B' * 64)
        vault.enable_live()
        # Simulate one open live bot order
        with vault._db() as db:
            db.execute(
                'INSERT INTO bot_orders(signal_id,client_order_id,exchange_order_id,price,quantity,quote,status,created_at,stop_distance,filled_at,stop_price) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('sig-live-1', 'cbotby1', '9001', '80.00', '4.0', '320.00', 'FILLED', self.now - 1000, '0.010', self.now - 500, '79.20'),
            )
            db.execute(
                'INSERT INTO marks(symbol,price,observed_at) VALUES(?,?,?)',
                ('SOLUSDT', '81.00', self.now),
            )

        fixture = DeviceAuthenticationQA()
        fixture.now = self.now
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        reader = DataReader({'guardian': ledger.path, 'shadow': shadow_db, 'binance_snapshot': vault.snapshot_path}, clock=lambda: self.now)
        app = create_app(fixture.store, reader, NotificationStore(self.root / 'noti.sqlite', clock=lambda: self.now), FakeControl(), vault=vault)
        client = TestClient(app, base_url='https://testserver')
        self.addCleanup(client.close)

        def req(method, path):
            h = fixture.sign(method, path, b'', session=fixture.session)
            return client.request(method, path, headers=h)

        # Account status now accurately reports orders_enabled=True
        acct = req('GET', '/api/v1/app/binance/account').json()
        self.assertTrue(acct['orders_enabled'])
        self.assertTrue(acct['spot_trading_enabled'])

        # /live/status & /live/risk normalize SUI-live -> SOL-live and emit readiness blocker
        live_status = req('GET', '/api/v1/app/live/status').json()
        live_risk = req('GET', '/api/v1/app/live/risk').json()
        self.assertEqual(live_status['account_id'], 'SOL-live')
        self.assertEqual(live_status['symbol'], 'SOLUSDT')
        self.assertEqual(live_risk['account_id'], 'SOL-live')
        self.assertEqual(live_risk['state'], 'LIVE_ON')
        self.assertFalse(live_status['readiness']['can_open_position'])
        self.assertEqual(live_status['readiness']['blocker'], 'INSUFFICIENT_FREE_USDT')
        self.assertEqual(live_status['readiness']['free_usdt'], '1.61')
        self.assertEqual(live_risk['readiness']['hard_circuit_pct'], '5.00')

        # /live/orders surfaces bot_orders and manual_orders cleanly
        live_orders = req('GET', '/api/v1/app/live/orders').json()
        self.assertTrue(live_orders['available'])
        self.assertEqual(live_orders['bot_orders'][0]['signal_id'], 'sig-live-1')
        self.assertEqual(live_orders['bot_orders'][0]['apts']['initial_stop_pct'], '0.010')
        self.assertEqual(len(live_orders['manual_orders']), 2)

        # /live/positions exposes live bot slot with live mark PnL
        live_pos = req('GET', '/api/v1/app/live/positions').json()
        self.assertEqual(live_pos['slots'][0]['strategy_id'], 'T1')
        self.assertEqual(live_pos['slots'][0]['pnl_usdt'], '+4.00')
        self.assertEqual(live_pos['slots'][0]['stop_distance'], '0.010')

        # /shadow/status exposes unrealized_pnl, stop_price, stage, and rules
        shadow = req('GET', '/api/v1/app/shadow/status').json()
        self.assertEqual(shadow['rules']['t1_stop_distance'], '0.010')
        self.assertEqual(shadow['positions'][0]['stage'], 'S1')
        self.assertEqual(shadow['positions'][0]['stop_price'], '80.12')
        self.assertEqual(shadow['positions'][0]['unrealized_pnl'], '+6.00')
        self.assertEqual(shadow['positions'][0]['reversal_hits'], 1)


if __name__ == '__main__':
    unittest.main()
