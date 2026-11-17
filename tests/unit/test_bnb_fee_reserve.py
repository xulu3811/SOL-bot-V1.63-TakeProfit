import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl

from solbot.app_api.binance_account import AccountVault, UrlTransport
from solbot.execution.live_policy import (
    BNB_MAX_HOLDING_USDT,
    BNB_MIN_RESERVE_USDT,
    compute_bnb_refill_order,
)


class _BNBTestTransport:
    def __init__(self, *, usdt_free='2000', sol_free='0', sol_locked='0', bnb_free='0.00000097', bnb_locked='0', bnb_ask='594.30'):
        self.usdt_free = usdt_free
        self.sol_free = sol_free
        self.sol_locked = sol_locked
        self.bnb_free = bnb_free
        self.bnb_locked = bnb_locked
        self.bnb_ask = bnb_ask
        self.open_orders = []
        self.orders_by_cid = {}
        self.calls = []
        self.next_order_id = 500
        self.fail_bnb_post = False

    def request(self, path, query, headers, method='GET'):
        self.calls.append((method, path, query))
        fields = dict(parse_qsl(query, keep_blank_values=True))
        if path == '/api/v3/time':
            return {'serverTime': 1800000000000}
        if path == '/sapi/v1/account/apiRestrictions':
            return {
                'enableReading': True,
                'enableWithdrawals': False,
                'enableInternalTransfer': False,
                'permitsUniversalTransfer': False,
                'enableSpotAndMarginTrading': True,
            }
        if path == '/api/v3/account':
            return {'balances': [
                {'asset': 'SOL', 'free': self.sol_free, 'locked': self.sol_locked},
                {'asset': 'USDT', 'free': self.usdt_free, 'locked': '0'},
                {'asset': 'BNB', 'free': self.bnb_free, 'locked': self.bnb_locked},
            ]}
        if path == '/api/v3/ticker/bookTicker':
            if fields.get('symbol') == 'BNBUSDT':
                bid = Decimal(self.bnb_ask) - Decimal('0.01')
                return {'symbol': 'BNBUSDT', 'bidPrice': format(bid, 'f'), 'askPrice': self.bnb_ask}
            return {'symbol': 'SOLUSDT', 'bidPrice': '72.40', 'askPrice': '72.41'}
        if path == '/api/v3/openOrders':
            sym = fields.get('symbol')
            return [row for row in self.open_orders if not sym or row.get('symbol') == sym]
        if path == '/api/v3/allOrders':
            return []
        if path == '/api/v3/exchangeInfo':
            if fields.get('symbol') == 'BNBUSDT':
                return {'symbols': [{'symbol': 'BNBUSDT', 'orderTypes': ['LIMIT'], 'filters': [
                    {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
                    {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
                    {'filterType': 'NOTIONAL', 'minNotional': '5.00'},
                ]}]}
            return {'symbols': [{'symbol': 'SOLUSDT', 'orderTypes': ['LIMIT', 'STOP_LOSS_LIMIT', 'MARKET'], 'filters': [
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
                {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
                {'filterType': 'NOTIONAL', 'minNotional': '5.00'},
            ]}]}
        if method == 'GET' and path == '/api/v3/order':
            cid = fields.get('origClientOrderId') or ''
            if cid in self.orders_by_cid:
                return dict(self.orders_by_cid[cid])
            raise ValueError('ORDER_GONE')
        if method == 'POST' and path == '/api/v3/order/cancelReplace':
            new_cid = fields.get('newClientOrderId')
            self.next_order_id += 1
            order = {
                'symbol': fields.get('symbol'), 'orderId': self.next_order_id, 'clientOrderId': new_cid,
                'side': fields.get('side'), 'type': fields.get('type'), 'status': 'NEW',
                'origQty': fields.get('quantity'), 'executedQty': '0',
                'price': fields.get('price') or '0', 'stopPrice': fields.get('stopPrice') or '0',
            }
            self.orders_by_cid[new_cid] = order
            return {'cancelResult': 'SUCCESS', 'newOrderResult': 'SUCCESS', 'newOrderResponse': order}
        if method == 'POST' and path == '/api/v3/order':
            sym = fields.get('symbol')
            if sym == 'BNBUSDT' and self.fail_bnb_post:
                raise ValueError('BINANCE_REJECTED')
            self.next_order_id += 1
            cid = fields.get('newClientOrderId')
            qty = Decimal(fields.get('quantity') or '0')
            px = Decimal(fields.get('price') or '0')
            filled = (sym == 'BNBUSDT') or (fields.get('type') == 'MARKET')
            status = 'FILLED' if filled else 'NEW'
            exec_qty = format(qty, 'f') if filled else '0'
            cum_quote = format(qty * (Decimal(self.bnb_ask) if sym == 'BNBUSDT' else (px if px > 0 else Decimal('72.40'))), 'f') if filled else '0'
            if sym == 'BNBUSDT' and filled:
                self.bnb_free = format(Decimal(self.bnb_free) + qty, 'f')
                self.usdt_free = format(max(Decimal('0'), Decimal(self.usdt_free) - Decimal(cum_quote)), 'f')
            order = {
                'symbol': sym, 'orderId': self.next_order_id, 'clientOrderId': cid,
                'side': fields.get('side'), 'type': fields.get('type'), 'status': status,
                'origQty': format(qty, 'f'), 'executedQty': exec_qty,
                'cummulativeQuoteQty': cum_quote, 'price': format(px, 'f'),
                'transactTime': 1800000000000,
            }
            if cid:
                self.orders_by_cid[cid] = order
            return order
        if method == 'DELETE' and path == '/api/v3/order':
            return {'status': 'CANCELED'}
        raise AssertionError(f'Unexpected call: {method} {path} {query}')


class BNBPolicyAndReserveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = 'A' * 64
        self.secret = 'B' * 64

    def _vault(self, transport, now_fn=None):
        vault = AccountVault(self.root, clock=now_fn or (lambda: 1800000000000), transport=transport)
        vault.save(self.key, self.secret)
        vault.enable_live()
        return vault

    def test_policy_computes_5u_refill_clears_min_notional_and_enforces_20u_cap(self):
        # Near-zero BNB at 594.30 -> ceil_step(5.05 / 594.30, 0.001) = 0.009 BNB @ 594.31
        qty, limit_px, notional, err = compute_bnb_refill_order('0.00000097', '594.30', '1897.82')
        self.assertIsNone(err)
        self.assertEqual(qty, Decimal('0.009'))
        self.assertEqual(limit_px, Decimal('594.31'))
        self.assertGreaterEqual(qty * Decimal('594.30'), Decimal('5.00'))
        self.assertLessEqual(notional, BNB_MAX_HOLDING_USDT)

        # Already >= 5 USDT -> sufficient
        qty2, _, _, err2 = compute_bnb_refill_order('0.010', '594.30', '1897.82')
        self.assertIsNone(qty2)
        self.assertEqual(err2, 'BNB_SUFFICIENT')

        # Hard 20 USDT cap prevents exceeding 20 USDT even with custom threshold
        qty3, _, _, err3 = compute_bnb_refill_order(
            '0.028', '600.00', '1000',
            min_threshold_usdt=Decimal('18.00'),
            max_holding_usdt=BNB_MAX_HOLDING_USDT,
        )
        self.assertIsNone(qty3)
        self.assertEqual(err3, 'BNB_MAX_CAP_EXCEEDED')

        # Reserved USDT for primary SOL trade is protected
        qty4, _, _, err4 = compute_bnb_refill_order('0', '594.30', '1502.00', reserved_usdt='1500.00')
        self.assertIsNone(qty4)
        self.assertEqual(err4, 'USDT_INSUFFICIENT_FOR_BNB')

    def test_consider_entry_refills_bnb_below_5u_and_skips_when_sufficient_or_short_usdt(self):
        now = 1800000000000
        transport = _BNBTestTransport(usdt_free='2000', bnb_free='0.00000097', bnb_ask='594.30')
        vault = self._vault(transport, lambda: now)
        payload = {
            'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'accepted': True,
            'signal_id': 'sig-bnb-buy-1', 'expires_at': now + 600_000,
            'entry_low': '72.00', 'score': 80,
        }

        class Reader:
            def read(self, key, sql, args=()):
                return [{'payload_json': json.dumps(payload)}] if 'decisions' in sql else []

        res = vault.consider_entry(Reader(), Decimal('72.40'), Decimal('0.01'), now, ask=Decimal('72.41'))
        self.assertTrue(res['placed'])
        posts = [c for c in transport.calls if c[0] == 'POST' and c[1] == '/api/v3/order']
        self.assertEqual(len(posts), 2)
        self.assertIn('symbol=BNBUSDT', posts[0][2])
        self.assertIn('newClientOrderId=cbotbn', posts[0][2])
        self.assertIn('timeInForce=IOC', posts[0][2])
        self.assertIn('symbol=SOLUSDT', posts[1][2])
        with closing(sqlite3.connect(vault.db_path)) as db:
            refills = db.execute('SELECT status, reason, quantity FROM bnb_refills').fetchall()
        self.assertEqual(refills, [('FILLED', 'SOL_BUY', '0.009')])

        # Next SOL buy after cooldown (now + 70s) has BNB >= 5 USDT (0.009 * 594.30 = 5.349), so skips BNB
        payload2 = dict(payload, signal_id='sig-bnb-buy-2', expires_at=now + 999_999)
        vault.save_trading_config('500', '500', max_slots=3)

        class Reader2:
            def read(self, key, sql, args=()):
                return [{'payload_json': json.dumps(payload2)}] if 'decisions' in sql else []

        res2 = vault.consider_entry(Reader2(), Decimal('72.40'), Decimal('0.01'), now + 960_000, ask=Decimal('72.41'))
        self.assertTrue(res2['placed'])
        posts2 = [c for c in transport.calls if c[0] == 'POST' and c[1] == '/api/v3/order']
        self.assertEqual(len(posts2), 3)
        self.assertIn('symbol=SOLUSDT', posts2[2][2])

        # Tight USDT (1502 free with 1500 SOL order) prioritizes SOL and skips BNB refill
        t_tight = _BNBTestTransport(usdt_free='1502.00', bnb_free='0', bnb_ask='594.30')
        vault_tight = AccountVault(self.root / 'tight', clock=lambda: now, transport=t_tight)
        vault_tight.save(self.key, self.secret)
        vault_tight.enable_live()
        vault_tight.save_trading_config('1500', '1500', max_slots=2)
        res_tight = vault_tight.consider_entry(Reader(), Decimal('72.40'), Decimal('0.01'), now, ask=Decimal('72.41'))
        self.assertTrue(res_tight['placed'])
        posts_tight = [c for c in t_tight.calls if c[0] == 'POST' and c[1] == '/api/v3/order']
        self.assertEqual(len(posts_tight), 1)
        self.assertIn('symbol=SOLUSDT', posts_tight[0][2])

    def test_sell_exits_refill_bnb_respect_open_orders_cooldown_and_never_block_sol_exit(self):
        now = 1800000000000
        # 1. Open BNB order blocks duplicate buy
        t_open = _BNBTestTransport(usdt_free='500', sol_free='10.0', bnb_free='0', bnb_ask='594.30')
        t_open.open_orders.append({'symbol': 'BNBUSDT', 'orderId': 77, 'clientOrderId': 'cbotbnopen', 'status': 'NEW'})
        v_open = self._vault(t_open, lambda: now)
        api_key, secret = v_open._load()
        out_open = v_open.ensure_bnb_fee_reserve(api_key, secret, now, reason='TEST')
        self.assertFalse(out_open['refilled'])
        self.assertEqual(out_open['reason'], 'BNB_ORDER_OPEN')

        # 2. Bot limit exit refills BNB before selling SOL
        t_exit = _BNBTestTransport(usdt_free='500', sol_free='10.0', bnb_free='0.001', bnb_ask='594.30')
        v_exit = AccountVault(self.root / 'exit', clock=lambda: now, transport=t_exit)
        v_exit.save(self.key, self.secret)
        ak, sec = v_exit._load()
        decision = SimpleNamespace(
            quantity=Decimal('10.000'),
            limit_price=Decimal('72.60'),
        )
        v_exit._place_bot_exit_limit(ak, sec, 'sig-exit-1', Decimal('72.00'), None, decision, Decimal('0.01'), Decimal('0.001'), Decimal('72.65'), now)
        posts = [c for c in t_exit.calls if c[0] == 'POST' and c[1] == '/api/v3/order']
        self.assertEqual(len(posts), 2)
        self.assertIn('symbol=BNBUSDT', posts[0][2])
        self.assertIn('symbol=SOLUSDT', posts[1][2])
        self.assertIn('side=SELL', posts[1][2])

        # Cooldown blocks repeat refill within 60s
        again = v_exit.ensure_bnb_fee_reserve(ak, sec, now + 10_000, reason='SECOND')
        self.assertEqual(again['reason'], 'BNB_COOLDOWN')

        # 3. BNB failure MUST NOT block SOL market sell exit
        t_fail = _BNBTestTransport(usdt_free='500', sol_free='10.0', bnb_free='0', bnb_ask='594.30')
        t_fail.fail_bnb_post = True
        v_fail = AccountVault(self.root / 'fail', clock=lambda: now, transport=t_fail)
        v_fail.save(self.key, self.secret)
        ak2, sec2 = v_fail._load()
        mkt_res = v_fail._market_exit_order(
            ak2, sec2, prev_client_id=None, exit_client_id='cbot2xfailtest',
            desired_qty=Decimal('10.000'), step=Decimal('0.001'), now=now,
        )
        self.assertEqual(mkt_res['status'], 'FILLED')

    def test_url_transport_allows_only_bounded_ioc_cbotbn_buy_for_bnbusdt(self):
        ut = UrlTransport()
        # Reject invalid BNBUSDT orders
        for bad_q in (
            'symbol=BNBUSDT&side=SELL&type=LIMIT&timeInForce=IOC&quantity=0.009&price=594.31&newClientOrderId=cbotbn1',
            'symbol=BNBUSDT&side=BUY&type=MARKET&timeInForce=IOC&quantity=0.009&price=594.31&newClientOrderId=cbotbn1',
            'symbol=BNBUSDT&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.009&price=594.31&newClientOrderId=cbotbn1',
            'symbol=BNBUSDT&side=BUY&type=LIMIT&timeInForce=IOC&quantity=0.009&price=594.31&newClientOrderId=manual1',
            'symbol=BNBUSDT&side=BUY&type=LIMIT&timeInForce=IOC&quantity=0.050&price=594.31&newClientOrderId=cbotbn1',
        ):
            with self.assertRaises(ValueError):
                ut.request('/api/v3/order', bad_q, {}, method='POST')
        with self.assertRaises(ValueError):
            ut.request('/api/v3/order', 'symbol=BNBUSDT&origClientOrderId=cbotbn1', {}, method='DELETE')

        # Allow valid bounded IOC cbotbn buy up to network call
        fake_resp = SimpleNamespace(status_code=200, content=b'{"orderId":1,"status":"FILLED"}')
        with patch.object(ut, '_get_client', return_value=SimpleNamespace(request=lambda *a, **kw: fake_resp)):
            out = ut.request(
                '/api/v3/order',
                'symbol=BNBUSDT&side=BUY&type=LIMIT&timeInForce=IOC&quantity=0.009&price=594.31&newClientOrderId=cbotbn1',
                {},
                method='POST',
            )
        self.assertEqual(out['status'], 'FILLED')
