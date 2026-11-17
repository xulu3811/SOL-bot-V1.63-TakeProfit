import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.app_api.binance_account import AccountVault
from solbot.app_api.data import DataReader


class FakeTransport:
    def __init__(self, restrictions, usdt_free='140', sol_free='0.4', sol_locked='0.2'):
        self.restrictions = restrictions
        self.usdt_free = usdt_free
        self.sol_free = sol_free
        self.sol_locked = sol_locked
        self.open_orders = []
        self.extra_orders = []
        self.orders_by_cid = {}
        self.fail_next_with = None
        self.market_sell_status = 'FILLED'
        self.next_order_id = 100
        self.reject_post = False
        self.paths = []
        self.calls = []

    def request(self, path, query, headers, method='GET'):
        from urllib.parse import parse_qsl
        self.calls.append((method, path, query))
        self.paths.append(path)
        fields = dict(parse_qsl(query, keep_blank_values=True))
        if path == '/api/v3/time':
            return {'serverTime': 1800000000000}
        if path == '/sapi/v1/account/apiRestrictions':
            return self.restrictions
        if path == '/api/v3/account':
            return {'balances': [
                {'asset': 'SOL', 'free': self.sol_free, 'locked': self.sol_locked},
                {'asset': 'USDT', 'free': self.usdt_free, 'locked': '0'},
                {'asset': 'BNB', 'free': '0.01', 'locked': '0'},
                {'asset': 'BTC', 'free': '9', 'locked': '0'},
            ]}
        if path == '/api/v3/ticker/bookTicker':
            return {'bidPrice': '82.84', 'askPrice': '82.85'}
        if path == '/api/v3/openOrders':
            return list(self.open_orders)
        if path == '/api/v3/allOrders':
            return [
                {'symbol': 'SOLUSDT', 'orderId': 11, 'clientOrderId': 'manual-a', 'side': 'BUY', 'status': 'FILLED', 'price': '83.12', 'origQty': '0.601', 'executedQty': '0.601', 'time': 1800000000000},
                {'symbol': 'SOLUSDT', 'orderId': 12, 'clientOrderId': 'manual-b', 'side': 'BUY', 'status': 'FILLED', 'price': '101', 'origQty': '0.3', 'executedQty': '0.3', 'time': 1700000000000},
                {'symbol': 'SOLUSDT', 'orderId': 13, 'clientOrderId': 'sb5-robot', 'side': 'BUY', 'status': 'FILLED', 'price': '1', 'origQty': '1', 'executedQty': '1', 'time': 1800000002000},
            ] + list(self.extra_orders)
        if path == '/api/v3/exchangeInfo':
            return {'symbols': [{'symbol': 'SOLUSDT', 'orderTypes': ['STOP_LOSS_LIMIT'], 'filters': [
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
                {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
                {'filterType': 'NOTIONAL', 'minNotional': '5'},
            ]}]}
        if method == 'GET' and path == '/api/v3/order':
            cid = fields.get('origClientOrderId') or ''
            if cid in self.orders_by_cid:
                return dict(self.orders_by_cid[cid])
            for row in self.open_orders:
                if row.get('clientOrderId') == cid:
                    return dict(row)
            raise ValueError('ORDER_GONE')
        if method == 'POST' and path == '/api/v3/order/cancelReplace':
            if self.fail_next_with is not None:
                err = self.fail_next_with
                self.fail_next_with = None
                raise ValueError(err)
            cancel_cid = fields.get('cancelOrigClientOrderId')
            if cancel_cid in self.orders_by_cid:
                self.orders_by_cid[cancel_cid]['status'] = 'CANCELED'
            new_cid = fields.get('newClientOrderId')
            self.next_order_id += 1
            order = {
                'symbol': 'SOLUSDT', 'orderId': self.next_order_id, 'clientOrderId': new_cid,
                'side': fields.get('side'), 'type': fields.get('type'), 'status': 'NEW',
                'origQty': fields.get('quantity'), 'executedQty': '0',
                'price': fields.get('price') or '0', 'stopPrice': fields.get('stopPrice') or '0',
                'time': 1800000000000,
            }
            self.orders_by_cid[new_cid] = dict(order)
            return {'cancelResult': 'SUCCESS', 'newOrderResult': 'SUCCESS', 'newOrderResponse': order}
        if method == 'POST' and path == '/api/v3/order':
            if 'side=BUY' in query and 'newClientOrderId=cbotby' not in query:
                raise AssertionError('buy order')
            if self.fail_next_with is not None:
                err = self.fail_next_with
                self.fail_next_with = None
                raise ValueError(err)
            if self.reject_post:
                self.open_orders.append({'symbol': 'SOLUSDT', 'orderId': 55, 'clientOrderId': fields.get('newClientOrderId'), 'side': 'SELL', 'type': fields.get('type'), 'status': 'NEW', 'origQty': fields.get('quantity'), 'price': fields.get('price') or '0', 'stopPrice': fields.get('stopPrice') or '0', 'time': 1800000000000})
                raise ValueError('PROTECTION_REJECTED')
            cid = fields.get('newClientOrderId')
            status = self.market_sell_status if (fields.get('side') == 'SELL' and fields.get('type') == 'MARKET') else 'NEW'
            self.next_order_id += 1
            order_id = 99 if self.next_order_id == 101 and fields.get('type') == 'STOP_LOSS_LIMIT' else self.next_order_id
            order = {
                'symbol': 'SOLUSDT', 'orderId': order_id, 'clientOrderId': cid,
                'side': fields.get('side'), 'type': fields.get('type'), 'status': status,
                'origQty': fields.get('quantity'), 'executedQty': fields.get('quantity') if status == 'FILLED' else '0',
                'price': fields.get('price') or '0', 'stopPrice': fields.get('stopPrice') or '0',
                'time': 1800000000000,
            }
            if cid:
                self.orders_by_cid[cid] = dict(order)
            return {'orderId': order_id, 'status': status, 'clientOrderId': cid}
        if method == 'DELETE' and path == '/api/v3/order':
            if self.fail_next_with is not None:
                err = self.fail_next_with
                self.fail_next_with = None
                raise ValueError(err)
            cid = fields.get('origClientOrderId')
            if cid in self.orders_by_cid:
                self.orders_by_cid[cid]['status'] = 'CANCELED'
            return {'status': 'CANCELED'}
        raise AssertionError(path)


def restrictions(**extra):
    value = {'enableReading': True, 'enableWithdrawals': False, 'enableInternalTransfer': False, 'permitsUniversalTransfer': False, 'enableSpotAndMarginTrading': False}
    value.update(extra)
    return value


class BinanceAccountInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = 'B' * 64
        self.key = 'A' * 64

    def vault(self, transport):
        return AccountVault(self.root, clock=lambda: 1800000000000, transport=transport)

    def test_user_key_projects_two_manual_orders_without_storing_plaintext(self):
        transport = FakeTransport(restrictions())
        vault = self.vault(transport)
        result = vault.save(self.key, self.secret)
        blob = json.dumps(result) + self.root.joinpath('binance-vault.sqlite').read_text(encoding='utf-8', errors='ignore')
        self.assertNotIn(self.secret, blob)
        self.assertNotIn(self.key, json.dumps(result))
        self.assertEqual(result['key_hint'], 'AAAA')
        self.assertEqual(result['manual_order_count'], 2)
        self.assertEqual(result['orders_enabled'], False)
        self.assertNotIn('/api/v3/order', transport.paths)
        ledger = AllocationLedger(self.root / 'guardian.sqlite')
        positions = DataReader({'guardian': ledger.path, 'binance_snapshot': vault.snapshot_path}, clock=lambda: 1800000000000).live_positions()
        self.assertEqual(Decimal(positions['manual'][0]['quantity']), Decimal('0.6'))
        self.assertEqual([row['order_id'] for row in positions['manual_orders']], ['11', '12'])
        self.assertEqual(positions['account']['USDT']['free'], '140')
        self.assertNotIn('BTC', positions['account'])

    def test_withdrawal_permission_is_rejected_before_storage(self):
        vault = self.vault(FakeTransport(restrictions(enableWithdrawals=True)))
        with self.assertRaises(ValueError) as error:
            vault.save(self.key, self.secret)
        self.assertEqual(str(error.exception), 'WITHDRAWALS_ENABLED')
        self.assertFalse((self.root / 'binance-vault.sqlite').exists())

    def test_custody_places_one_stop_sell_and_cancel_removes_it(self):
        from solbot.app_api.binance_account import UrlTransport
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True))
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        first = vault.protect_manual('0.600399')
        second = vault.protect_manual('0.600399')
        posts = [call for call in transport.calls if call[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('side=SELL', posts[0][2])
        self.assertIn('type=STOP_LOSS_LIMIT', posts[0][2])
        self.assertNotIn('side=BUY', posts[0][2])
        self.assertEqual(first['stop_order_id'], '99')
        self.assertEqual(second['private_order_calls'], 0)
        self.assertLessEqual(Decimal(first['quantity']), Decimal('0.600399'))
        self.assertEqual(Decimal(first['entry_price']), Decimal('82.84'))
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        from solbot.app_api.custody_apts import step
        evidence = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'))
        moved = step(vault, now=1800000005000, bid=Decimal('82.84') * Decimal('1.012'), evidence=evidence, tick=Decimal('0.01'))
        self.assertEqual(moved['stage'], 'S1')
        self.assertGreater(Decimal(moved['stop_price']), Decimal('82.18'))
        sells = [call for call in transport.calls if call[0] == 'POST']
        self.assertTrue(all('side=SELL' in call[2] and 'side=BUY' not in call[2] for call in sells))
        self.assertGreaterEqual(len(sells), 2)
        self.assertTrue(vault.cancel_manual()['cancelled'])
        self.assertGreaterEqual(sum(1 for call in transport.calls if call[0] == 'DELETE'), 1)
        with self.assertRaises(ValueError):
            UrlTransport().request('/api/v3/order', 'symbol=SOLUSDT&side=BUY&type=STOP_LOSS', {}, method='POST')

    def _cancelled(self, vault):
        with vault._db() as db:
            db.execute('INSERT INTO protections VALUES(?,?,?,?,?,?,?,?,?,?)', ('manual:SOL', 'cbotslold', '1', '0.6', '82.46', '82.21', '83.12', 'CANCELLED', 1, 1))
            db.execute('INSERT INTO custody_apts(intent_id,state_json,stage,status,updated_at) VALUES(?,?,?,?,?)', ('manual:SOL', '{}', 'S3', 'EXIT_REQUIRED', 1))

    def test_cancelled_row_uses_latest_fill_and_keeps_one_stop(self):
        from solbot.mathematics.protective_stop import initial_stop
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='1.2', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        transport.extra_orders.append({'symbol': 'SOLUSDT', 'orderId': 21, 'clientOrderId': 'and_new', 'side': 'BUY', 'status': 'FILLED', 'price': '83.31', 'origQty': '1.2', 'executedQty': '1.2', 'time': 1800000003000})
        self._cancelled(vault)
        result = vault.protect_manual('1.200399')
        # Custody baseline anchors to the click-time market price (82.84) rather than historical buy (83.31)
        self.assertEqual(Decimal(result['entry_price']), Decimal('82.84'))
        self.assertEqual(Decimal(result['stop_price']), initial_stop('82.84', '0.01', stop_distance=Decimal('0.008')))
        self.assertEqual(len([call for call in transport.calls if call[0] == 'POST']), 1)
        self.assertEqual(vault.protection()['status'], 'OPEN')
        with vault._db() as db:
            self.assertIsNone(db.execute("SELECT stage FROM custody_apts").fetchone())

    def test_open_stop_is_adopted_instead_of_placing_another(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='0.000399', sol_locked='1.2')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        transport.open_orders.append({'symbol': 'SOLUSDT', 'orderId': 77, 'clientOrderId': 'cbotslready', 'side': 'SELL', 'type': 'STOP_LOSS_LIMIT', 'status': 'NEW', 'origQty': '1.200', 'price': '82.40', 'stopPrice': '82.65', 'time': 1800000004000})
        self._cancelled(vault)
        result = vault.protect_manual('1.200399')
        self.assertEqual(result['stop_order_id'], '77')
        self.assertEqual(result['private_order_calls'], 0)
        self.assertFalse(any(call[0] == 'POST' for call in transport.calls))
        self.assertEqual(vault.protection()['status'], 'OPEN')
        self.assertEqual(vault.protect_manual('1.200399')['private_order_calls'], 0)

    def test_exchange_acceptance_is_kept_when_the_reply_fails(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='1.2', sol_locked='0')
        transport.reject_post = True
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        self._cancelled(vault)
        result = vault.protect_manual('1.200399')
        self.assertEqual(result['stop_order_id'], '55')
        self.assertEqual(vault.protection()['status'], 'OPEN')
        self.assertEqual(len([call for call in transport.calls if call[0] == 'POST']), 1)
        self.assertEqual(vault.protect_manual('1.200399')['stop_order_id'], '55')
        self.assertEqual(len([call for call in transport.calls if call[0] == 'POST']), 1)

    def test_locked_balance_reports_the_existing_stop(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='0.0001', sol_locked='1.2')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        transport.open_orders.append({'symbol': 'SOLUSDT', 'orderId': 78, 'clientOrderId': 'cbotslstuck', 'side': 'SELL', 'status': 'NEW', 'origQty': '1.2', 'price': '0', 'stopPrice': '0', 'time': 1})
        with self.assertRaises(ValueError) as error:
            vault.protect_manual('1.200399')
        self.assertEqual(str(error.exception), 'STOP_LOCKING_SOL')
        self.assertFalse(any(call[0] == 'POST' for call in transport.calls))

    def test_user_switch_places_one_sol_buy_and_off_stops_new_buys(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='2000')
        vault = self.vault(transport)
        self.assertEqual(vault.consider_entry(None, '83', '0.001', 1800000000000)['placed'], False)
        vault.save(self.key, self.secret)
        turned = vault.enable_live()
        self.assertEqual((turned['accepted'], turned['state']), (True, 'LIVE_ON'))
        payload = {'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'accepted': True, 'signal_id': 'sig-live-1', 'expires_at': 1900000000000, 'entry_low': '80', 'score': 75}

        class Reader:
            def read(self, key, sql, args=()):
                return [{'payload_json': json.dumps(payload)}]

        placed = vault.consider_entry(Reader(), Decimal('83'), Decimal('0.001'), 1800000000000)
        self.assertTrue(placed['placed'])
        self.assertGreaterEqual(Decimal(placed['quantity']) * Decimal(placed['price']), Decimal('300'))
        self.assertLessEqual(Decimal(placed['quantity']) * Decimal(placed['price']), Decimal('700'))
        buys = [call for call in transport.calls if call[0] == 'POST' and 'side=BUY' in call[2]]
        self.assertEqual(len(buys), 1)
        self.assertIn('newClientOrderId=cbotby', buys[0][2])
        self.assertTrue(vault.disable_live()['accepted'])
        self.assertEqual(vault.live_state(), 'LIVE_OFF')
        again = vault.consider_entry(Reader(), Decimal('83'), Decimal('0.001'), 1800000001000)
        self.assertFalse(again['placed'])
        self.assertEqual(len([call for call in transport.calls if call[0] == 'POST' and 'side=BUY' in call[2]]), 1)

    def test_refresh_replaces_a_stale_sol_balance_and_cost(self):
        from solbot.app_api.data import DataReader
        orders = [
            {'side': 'BUY', 'status': 'FILLED', 'price': '83.12', 'executed_qty': '0.601', 'time': 1},
            {'side': 'SELL', 'status': 'FILLED', 'price': '82.65', 'executed_qty': '0.600', 'time': 2},
            {'side': 'BUY', 'status': 'FILLED', 'price': '83.31', 'executed_qty': '1.200', 'time': 3},
            {'side': 'SELL', 'status': 'FILLED', 'price': '83.02', 'executed_qty': '1.200', 'time': 4},
            {'side': 'BUY', 'status': 'FILLED', 'price': '83.01', 'executed_qty': '1.980', 'time': 5},
        ]
        self.assertEqual(DataReader._average_entry(orders), Decimal('83.01'))
        transport = FakeTransport(restrictions(), sol_free='0.000399', sol_locked='1.200000')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        transport.sol_free = '1.980399'
        transport.sol_locked = '0'
        transport.extra_orders.extend([
            {'symbol': 'SOLUSDT', 'orderId': 31, 'clientOrderId': 'and_sell', 'side': 'SELL', 'status': 'FILLED', 'price': '83.02', 'origQty': '1.2', 'executedQty': '1.2', 'time': 1800000001000},
            {'symbol': 'SOLUSDT', 'orderId': 32, 'clientOrderId': 'and_new', 'side': 'BUY', 'status': 'FILLED', 'price': '83.01', 'origQty': '1.98', 'executedQty': '1.98', 'time': 1800000005000},
        ])
        from solbot.app_api.custody_apts import step
        self.assertFalse(step(vault, now=1800000006000)['advanced'])
        ledger = AllocationLedger(self.root / 'guardian.sqlite')
        positions = DataReader({'guardian': ledger.path, 'binance_snapshot': vault.snapshot_path}, clock=lambda: 1800000006000).live_positions()
        self.assertEqual(Decimal(positions['manual'][0]['quantity']), Decimal('1.980399'))
        self.assertEqual(positions['account']['SOL']['total'], '1.980399')
        self.assertEqual(Decimal(positions['manual'][0]['entry_price']), Decimal('83.01'))

    def test_clear_removes_saved_key(self):
        vault = self.vault(FakeTransport(restrictions()))
        vault.save(self.key, self.secret)
        self.assertTrue(vault.clear()['cleared'])
        self.assertFalse(vault.status()['configured'])
        with self.assertRaises(ValueError):
            vault.sync()

    def test_t2_is_gated_off_by_default_while_t1_gets_apts_atomic_protection_even_when_live_off(self):
        from solbot.app_api.custody_apts import step
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='2000', sol_free='0', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        t2_only = {'symbol': 'SOLUSDT', 'strategy_id': 'T2', 'accepted': True, 'signal_id': 'sig-t2-skip',
                   'expires_at': 1900000000000, 'entry_low': '100', 'score': 90, 'stop_distance': '0.012'}
        t1_sig = {'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'accepted': True, 'signal_id': 'sig-t1-live',
                  'expires_at': 1900000000000, 'entry_low': '100', 'score': 85, 'stop_distance': '0.010'}

        class T2Reader:
            def read(self, key, sql, args=()):
                return [{'payload_json': json.dumps(t2_only)}] if 'decisions' in sql else []

        self.assertFalse(vault.consider_entry(T2Reader(), Decimal('100.5'), Decimal('0.01'), 1800000000000)['placed'])

        class BothReader:
            def read(self, key, sql, args=()):
                return [{'payload_json': json.dumps(t2_only)}, {'payload_json': json.dumps(t1_sig)}] if 'decisions' in sql else []

        placed = vault.consider_entry(BothReader(), Decimal('100.5'), Decimal('0.01'), 1800000000000)
        self.assertTrue(placed['placed'])
        self.assertEqual(placed['strategy_id'], 'T1')
        buy_cid = [row for row in transport.orders_by_cid if row.startswith('cbotby')][0]
        transport.orders_by_cid[buy_cid].update({'status': 'FILLED', 'executedQty': placed['quantity'], 'price': '100', 'updateTime': 1800000001000})
        transport.sol_free = placed['quantity']
        vault.disable_live()  # Even with LIVE_OFF, open bot positions remain protected!

        ev = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'))
        s1 = step(vault, now=1800000002000, bid=Decimal('100.2'), evidence=ev, tick=Decimal('0.01'))
        self.assertEqual(s1['bot_stops']['actions'][0]['status'], 'STOP_PLACED')
        self.assertEqual(Decimal(s1['bot_stops']['actions'][0]['stop']), Decimal('99.00'))

        # Small move < 0.15% within 15s is throttled (no cancelReplace spam)
        cr_before = sum(1 for _, p, _ in transport.calls if p == '/api/v3/order/cancelReplace')
        s_throttled = step(vault, now=1800000003000, bid=Decimal('100.25'), evidence=ev, tick=Decimal('0.01'))
        self.assertEqual(s_throttled['bot_stops']['actions'][0]['status'], 'STOP_RESTING')
        self.assertEqual(sum(1 for _, p, _ in transport.calls if p == '/api/v3/order/cancelReplace'), cr_before)

        # Stage jump to S1 (+1.0% >= +0.8%) atomically replaces via /api/v3/order/cancelReplace
        s_raised = step(vault, now=1800000005000, bid=Decimal('101.00'), evidence=ev, tick=Decimal('0.01'))
        self.assertEqual(s_raised['bot_stops']['actions'][0]['status'], 'TRAIL_RAISED')
        self.assertEqual(s_raised['bot_stops']['actions'][0]['stage'], 'S1')
        self.assertEqual(sum(1 for _, p, _ in transport.calls if p == '/api/v3/order/cancelReplace'), cr_before + 1)

        # Stage jump to S2 (> +1.2%) advances stage to S2 and replaces via /api/v3/order/cancelReplace
        s2_raised = step(vault, now=1800000007000, bid=Decimal('101.30'), evidence=ev, tick=Decimal('0.01'))
        self.assertEqual(s2_raised['bot_stops']['actions'][0]['stage'], 'S2')

    def test_exit_pending_timeout_escalates_to_market_sell_for_manual_and_bot(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='1.0', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        with vault._db() as db:
            db.execute('INSERT INTO protections VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('manual:SOL', 'cbotexstuck', '88', '1.0', '82.0', '83.0', '83.0', 'EXITING', 1800000000000, 1800000000000))
        transport.orders_by_cid['cbotexstuck'] = {'symbol': 'SOLUSDT', 'orderId': 88, 'clientOrderId': 'cbotexstuck', 'status': 'NEW'}
        # Under 5s stays EXITING without Market order
        early = vault.advance('82.9', None, '0.01', 1800000003000)
        self.assertFalse(early['advanced'])
        self.assertFalse(any('type=MARKET' in q for _, _, q in transport.calls))
        # At >= 5s cancels LIMIT and executes SELL MARKET
        late = vault.advance('82.5', None, '0.01', 1800000006000)
        self.assertTrue(late['advanced'])
        self.assertTrue(late['escalated'])
        self.assertEqual(late['stage'], 'CLOSED')
        self.assertTrue(any(m == 'POST' and p == '/api/v3/order' and 'type=MARKET' in q and 'side=SELL' in q for m, p, q in transport.calls))

    def test_insufficient_free_sol_2010_and_order_gone_handled_safely(self):
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='0.600', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.protect_manual('0.600')
        ev = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'))
        # Simulate -2010 when actual sellable free+locked is slightly less due to missing BNB fee deduction
        transport.sol_free = '0.000'
        transport.sol_locked = '0.599'
        cid = vault.protection()['client_order_id']
        transport.orders_by_cid[cid].update({'origQty': '0.599', 'executedQty': '0'})
        transport.fail_next_with = 'INSUFFICIENT_FREE_SOL'
        moved = vault.advance(Decimal('83.12') * Decimal('1.012'), ev, Decimal('0.01'), 1800000005000)
        self.assertTrue(moved['advanced'])
        self.assertEqual(Decimal(vault.protection()['quantity']), Decimal('0.599'))

        # Simulate ORDER_GONE because previous stop already FILLED on Binance
        cid2 = vault.protection()['client_order_id']
        transport.orders_by_cid[cid2]['status'] = 'FILLED'
        transport.fail_next_with = 'ORDER_GONE'
        closed = vault.advance(Decimal('83.12') * Decimal('1.020'), ev, Decimal('0.01'), 1800000025000)
        self.assertIsNone(vault.protection())
        self.assertEqual(closed['stage'], 'CLOSED')

    def test_market_fill_price_bot_prefix_exclusion_url_transport_and_micro_feed(self):
        from solbot.app_api.binance_account import UrlTransport
        from solbot.app_api.custody_apts import step
        from solbot.app_api.micro_feed import read_micro_snapshot
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        import solbot.strategy as strat_mod

        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='2.0', sol_locked='0')
        # Add a MARKET BUY (price='0', cummulativeQuoteQty='300') and bot orders to exclude from manual_orders
        transport.extra_orders.extend([
            {'symbol': 'SOLUSDT', 'orderId': 41, 'clientOrderId': 'app-mkt-buy', 'side': 'BUY', 'type': 'MARKET',
             'status': 'FILLED', 'price': '0', 'origQty': '2.0', 'executedQty': '2.0', 'cummulativeQuoteQty': '300.0', 'time': 1800000009000},
            {'symbol': 'SOLUSDT', 'orderId': 42, 'clientOrderId': 'cbot2s1234', 'side': 'SELL', 'status': 'NEW', 'price': '140', 'origQty': '1', 'executedQty': '0', 'time': 1800000008000},
            {'symbol': 'SOLUSDT', 'orderId': 43, 'clientOrderId': 'p5p-99', 'side': 'SELL', 'status': 'NEW', 'price': '140', 'origQty': '1', 'executedQty': '0', 'time': 1800000008000},
        ])
        vault = self.vault(transport)
        summary = vault.save(self.key, self.secret)
        # Only manual-a, manual-b, and app-mkt-buy count; sb5-, cbot2s, p5p- excluded
        self.assertEqual(summary['manual_order_count'], 3)
        api_key, secret = vault._load()
        self.assertEqual(vault._live_entry(api_key, secret, 1800000009000), Decimal('150'))

        # MicroFeed tests
        micro_path = self.root / 'sol-micro-status.json'
        micro_path.write_text(json.dumps({
            'symbol': 'SOLUSDT', 'ready': True, 'bid': '151.25', 'ask': '151.27',
            'buy_ratio': '0.72', 'imbalance': '0.4', 'spread_bps': '1.32',
            'feature_generated_time_ms': 1800000010000, 'received_time_ms': 1800000010000,
        }), encoding='utf-8')
        self.assertEqual(read_micro_snapshot(micro_path, now_ms=1800000011000)['bid'], '151.25')
        self.assertIsNone(read_micro_snapshot(micro_path, now_ms=1800000020000))  # stale > 3s

        vault.protect_manual('1.0')
        before_ticker = sum(1 for _, p, _ in transport.calls if p == '/api/v3/ticker/bookTicker')
        ev = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'))
        step(vault, now=1800000011000, evidence=ev, tick=Decimal('0.01'), micro_path=micro_path, sync_account=False)
        self.assertEqual(sum(1 for _, p, _ in transport.calls if p == '/api/v3/ticker/bookTicker'), before_ticker)
        self.assertEqual(vault.mark()['mark_price'], '151.25')

        # UrlTransport security gate checks
        ut = UrlTransport()
        with self.assertRaises(ValueError):
            ut.request('/api/v3/order', 'symbol=SOLUSDT&side=BUY&type=MARKET&quantity=1&newClientOrderId=cbotby1', {}, method='POST')
        with self.assertRaises(ValueError):
            ut.request('/api/v3/order', 'symbol=SOLUSDT&side=SELL&type=MARKET&quantity=1&newClientOrderId=manualsell', {}, method='POST')
        with self.assertRaises(ValueError):
            ut.request('/api/v3/order/cancelReplace', 'symbol=SOLUSDT&side=SELL&type=STOP_LOSS_LIMIT&newClientOrderId=cbot2s1&cancelOrigClientOrderId=cbot2s0', {}, method='POST')

        # strategy._execution_factor and stop_distance emission checks
        reasons = []
        val, observed = strat_mod._execution_factor(0.01, {'ready': True, 'spread_bps': '2.0', 'imbalance': '0.4'}, reasons)
        self.assertTrue(observed)
        self.assertGreater(val, 0.5)
        self.assertFalse(reasons)

    def test_r2_live_execution_breakout_pricing_expiry_and_env_gating(self):
        from solbot.mathematics.trend_exhaustion import TrendEvidence

        strat_db = self.root / 'decisions.sqlite'
        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
        r2_db = self.root / 'r2.sqlite'
        with closing(sqlite3.connect(r2_db)) as db, db:
            db.execute('CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY, payload TEXT)')
            r2_sig = {
                'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'signal_id': 'r2-live-1',
                'accepted': True, 'entry_low': '82.70', 'entry_high': '82.90',
                'stop_distance': '0.012', 'score': '82',
                'signal_time': 1800000000000, 'expires_at': 1800000060000,
            }
            db.execute('INSERT INTO r2_outbox VALUES(?,?)', ('r2-live-1', json.dumps(r2_sig)))

        reader = DataReader({'SOLUSDT_strategy': strat_db, 'r2_state': r2_db}, clock=lambda: 1800000000000)
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='4000', sol_free='0', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        # Without SOLBOT_LIVE_STRATEGIES, default only enables T1 so R2 signal stays gated off.
        with patch.dict(os.environ, {'SOLBOT_LIVE_STRATEGIES': ''}):
            res_default = vault.consider_entry(reader, Decimal('82.80'), Decimal('0.01'), 1800000000000, ask=Decimal('82.85'))
            self.assertEqual(res_default, {'placed': False, 'buy_orders': 0})

        # Enable T1,T2,R2 via SOLBOT_LIVE_STRATEGIES
        with patch.dict(os.environ, {'SOLBOT_LIVE_STRATEGIES': 'T1,T2,R2'}):
            # Chase guard rejects when bid exceeds breakout entry_high
            chased = vault.consider_entry(reader, Decimal('82.95'), Decimal('0.01'), 1800000000000, ask=Decimal('82.97'))
            self.assertEqual(chased['reason'], 'CHASE_EXCEEDED')

            # Inside band: uses buffered ask price capped by entry_high (min(entry_high, ceil_step(ask*1.0005)) = 82.90), R2 dynamic sizing ($500-$1000), stores strategy_id & expires_at
            placed = vault.consider_entry(reader, Decimal('82.80'), Decimal('0.01'), 1800000000000, ask=Decimal('82.85'))
            self.assertTrue(placed['placed'])
            self.assertEqual(placed['strategy_id'], 'R2')
            self.assertEqual(placed['price'], '82.90')
            self.assertEqual(Decimal(placed['quantity']), Decimal('7.720'))
            orders = vault.bot_orders()
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0]['strategy_id'], 'R2')
            self.assertEqual(orders[0]['expires_at'], 1800000060000)
            self.assertTrue(vault.has_open_bots())

            # Expired unfilled NEW entry order is auto-canceled on sync and frees slot
            ev = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'))
            synced_exp = vault.sync_bot_stops(Decimal('82.80'), Decimal('0.01'), 1800000060000, evidence=ev)
            self.assertEqual(synced_exp['actions'][0]['status'], 'EXPIRED')
            self.assertFalse(vault.has_open_bots())
            self.assertEqual(vault.bot_orders()[0]['status'], 'EXPIRED')

            # Cancel-race fill detection for T2: DELETE raises ORDER_GONE after fill -> becomes FILLED and keeps strategy_id='T2'
            with closing(sqlite3.connect(strat_db)) as db, db:
                t2_sig = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T2', 'signal_id': 't2-live-1',
                    'accepted': True, 'entry_low': '82.70', 'entry_high': '82.90',
                    'stop_distance': '0.010', 'score': '85',
                    'signal_time': 1800000061000, 'expires_at': 1800000120000,
                }
                db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                           ('SOLUSDT', 'T2', 't2-live-1', json.dumps(t2_sig)))
            t2_placed = vault.consider_entry(reader, Decimal('82.80'), Decimal('0.01'), 1800000061000, ask=Decimal('82.85'))
            self.assertTrue(t2_placed['placed'])
            self.assertEqual(t2_placed['strategy_id'], 'T2')

            buy_cid = [row['client_order_id'] for row in vault.bot_orders() if row['signal_id'] == 't2-live-1'][0]
            transport.sol_free = t2_placed['quantity']
            orig_request = transport.request
            calls_seen = {'del': 0}

            def race_request(path, query, headers, method='GET'):
                if method == 'DELETE' and path == '/api/v3/order':
                    calls_seen['del'] += 1
                    transport.orders_by_cid[buy_cid].update({'status': 'FILLED', 'executedQty': t2_placed['quantity'], 'price': '82.85'})
                    raise ValueError('ORDER_GONE')
                return orig_request(path, query, headers, method)

            transport.request = race_request
            race_res = vault.sync_bot_stops(Decimal('82.85'), Decimal('0.01'), 1800000120000, evidence=ev)
            transport.request = orig_request
            self.assertEqual(calls_seen['del'], 1)
            self.assertIn(race_res['actions'][0]['status'], ('STOP_PLACED', 'STOP_RESTING'))
            with vault._db() as db:
                row = db.execute("SELECT state_json FROM custody_apts WHERE intent_id='bot:t2-live-1'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row[0])['context']['strategy_id'], 'T2')

    def test_cross_strategy_same_wave_prefers_t1_and_blocks_duplicate_slot_within_15m(self):
        strat_db = self.root / 'decisions_wave.sqlite'
        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
            t1_sig = {
                'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': 't1-wave-1',
                'accepted': True, 'entry_low': '75.37', 'entry_high': '75.45',
                'stop_distance': '0.010', 'score': '91.87',
                'signal_time': 1800000000000, 'expires_at': 1800000300000,
            }
            db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                       ('SOLUSDT', 'T1', 't1-wave-1', json.dumps(t1_sig)))
        r2_db = self.root / 'r2_wave.sqlite'
        with closing(sqlite3.connect(r2_db)) as db, db:
            db.execute('CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY, payload TEXT)')
            r2_sig = {
                'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'signal_id': 'r2-wave-1',
                'accepted': True, 'entry_low': '75.37', 'entry_high': '75.59',
                'stop_distance': '0.008', 'score': '75',
                'signal_time': 1800000000000, 'expires_at': 1800000060000,
            }
            db.execute('INSERT INTO r2_outbox VALUES(?,?)', ('r2-wave-1', json.dumps(r2_sig)))

        reader = DataReader({'SOLUSDT_strategy': strat_db, 'r2_state': r2_db}, clock=lambda: 1800000000000)
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='4000', sol_free='0', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        with patch.dict(os.environ, {'SOLBOT_LIVE_STRATEGIES': 'T1,T2,R2'}):
            first = vault.consider_entry(reader, Decimal('75.37'), Decimal('0.01'), 1800000000600, ask=Decimal('75.37'))
            self.assertTrue(first['placed'])
            self.assertEqual(first['strategy_id'], 'T1')
            # 7 seconds later, R2 from the same 5m bar must NOT open a second slot
            second = vault.consider_entry(reader, Decimal('75.36'), Decimal('0.01'), 1800000007800, ask=Decimal('75.36'))
            self.assertFalse(second['placed'])
            self.assertEqual(len(vault.bot_orders()), 1)

    def test_cross_strategy_same_wave_prefers_r2_over_t2_in_downtrend_and_uncertain(self):
        for reg, expect_sid in (('DOWNTREND', 'R2'), ('UNCERTAIN', 'R2'), ('UPTREND', 'T2')):
            strat_db = self.root / f'decisions_pri_{reg}.sqlite'
            with closing(sqlite3.connect(strat_db)) as db, db:
                db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
                t2_sig = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T2', 'signal_id': f't2-{reg}',
                    'accepted': True, 'regime': reg, 'entry_low': '75.37', 'entry_high': '75.50',
                    'stop_distance': '0.012', 'score': '92.5',
                    'signal_time': 1800000000000, 'expires_at': 1800000900000,
                }
                db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                           ('SOLUSDT', 'T2', t2_sig['signal_id'], json.dumps(t2_sig)))
            r2_db = self.root / f'r2_pri_{reg}.sqlite'
            with closing(sqlite3.connect(r2_db)) as db, db:
                db.execute('CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY, payload TEXT)')
                r2_sig = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'signal_id': f'r2-{reg}',
                    'accepted': True, 'regime': reg, 'entry_low': '75.37', 'entry_high': '75.59',
                    'stop_distance': '0.008', 'score': '80',
                    'signal_time': 1800000000000, 'expires_at': 1800000300000,
                }
                db.execute('INSERT INTO r2_outbox VALUES(?,?)', (r2_sig['signal_id'], json.dumps(r2_sig)))

            reader = DataReader({'SOLUSDT_strategy': strat_db, 'r2_state': r2_db}, clock=lambda: 1800000000000)
            transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='4000', sol_free='0', sol_locked='0')
            vault = AccountVault(self.root / f'vault_pri_{reg}', clock=lambda: 1800000000000, transport=transport)
            vault.save(self.key, self.secret)
            vault.enable_live()
            with patch.dict(os.environ, {'SOLBOT_LIVE_STRATEGIES': 'T1,T2,R2'}):
                res = vault.consider_entry(reader, Decimal('75.37'), Decimal('0.01'), 1800000000600, ask=Decimal('75.37'))
            self.assertTrue(res['placed'])
            self.assertEqual(res['strategy_id'], expect_sid)

    def test_manual_custody_calculates_tp_sl_from_click_time_price_not_buy_price(self):
        from solbot.mathematics.protective_stop import initial_stop
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='10.0', sol_locked='0')
        # Historical manual buy was at $90.00, much higher than current market price ($75.20)
        transport.extra_orders.append({
            'symbol': 'SOLUSDT', 'orderId': 991, 'clientOrderId': 'manual_high_buy',
            'side': 'BUY', 'status': 'FILLED', 'price': '90.00', 'origQty': '10.0', 'executedQty': '10.0',
            'time': 1800000002000,
        })
        orig_req = transport.request

        def click_time_ticker(path, query, headers, method='GET'):
            if path == '/api/v3/ticker/bookTicker':
                return {'bidPrice': '75.20', 'askPrice': '75.21'}
            return orig_req(path, query, headers, method)

        transport.request = click_time_ticker
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        res = vault.protect_manual('10.0')
        self.assertEqual(Decimal(res['entry_price']), Decimal('75.20'))
        self.assertEqual(Decimal(res['stop_price']), initial_stop('75.20', '0.01', stop_distance=Decimal('0.008')))
        self.assertEqual(Decimal(res['stop_price']), Decimal('74.60'))

        ev = TrendEvidence(ready=True, atr=Decimal('0.35'), previous_atr=Decimal('0.35'))
        # +1.01% from click-time price ($75.20 -> $75.96) triggers S1 break-even
        s1 = vault.advance(Decimal('75.96'), ev, Decimal('0.01'), 1800000005000)
        self.assertEqual(s1['stage'], 'S1')
        self.assertGreater(Decimal(s1['stop_price']), Decimal('75.20'))
        # +1.60% from click-time price ($75.20 -> $76.40) triggers S2 adaptive trailing
        s2 = vault.advance(Decimal('76.40'), ev, Decimal('0.01'), 1800000025000)
        self.assertEqual(s2['stage'], 'S2')

    def test_live_sizing_500_to_1000_fallback_below_1000_and_reject_below_500(self):
        def make_strat(name, sid, score, ts):
            p = self.root / name
            with closing(sqlite3.connect(p)) as db, db:
                db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
                sig = {
                    'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': sid,
                    'accepted': True, 'entry_low': '80.00', 'entry_high': '80.50',
                    'stop_distance': '0.010', 'score': score,
                    'signal_time': ts, 'expires_at': ts + 60000,
                }
                db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                           ('SOLUSDT', 'T1', sid, json.dumps(sig)))
            return p

        # Below 500 USDT available rejects with USDT_BELOW_500
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='480', sol_free='0', sol_locked='0')
        vault_low = self.vault(transport)
        vault_low.save(self.key, self.secret)
        vault_low.enable_live()
        r_low = DataReader({'SOLUSDT_strategy': make_strat('s_low.sqlite', 't1-low', '90', 1800000000000)}, clock=lambda: 1800000000000)
        rejected = vault_low.consider_entry(r_low, Decimal('80.00'), Decimal('0.01'), 1800000000000, ask=Decimal('80.00'))
        self.assertFalse(rejected['placed'])
        self.assertEqual(rejected['reason'], 'USDT_BELOW_500')

        # 500 <= free < 1000 (720 USDT): respects target_notional (500 USDT -> 6.250 SOL for score=75) and caps at free (720 USDT -> 9.000 SOL for score=95)
        transport.usdt_free = '720'
        fb_dir = self.root / 'fb'
        fb_dir.mkdir(exist_ok=True)
        vault_fb = AccountVault(fb_dir, clock=lambda: 1800000000000, transport=transport)
        vault_fb.save(self.key, self.secret)
        vault_fb.enable_live()
        r_fb75 = DataReader({'SOLUSDT_strategy': make_strat('s_fb75.sqlite', 't1-fb75', '75', 1800000000000)}, clock=lambda: 1800000000000)
        fb75 = vault_fb.consider_entry(r_fb75, Decimal('80.00'), Decimal('0.01'), 1800000000000, ask=Decimal('80.00'))
        self.assertTrue(fb75['placed'])
        self.assertEqual(Decimal(fb75['quantity']), Decimal('6.250'))

        fb2_dir = self.root / 'fb2'
        fb2_dir.mkdir(exist_ok=True)
        vault_fb2 = AccountVault(fb2_dir, clock=lambda: 1800000000000, transport=transport)
        vault_fb2.save(self.key, self.secret)
        vault_fb2.enable_live()
        r_fb95 = DataReader({'SOLUSDT_strategy': make_strat('s_fb95.sqlite', 't1-fb95', '95', 1800000000000)}, clock=lambda: 1800000000000)
        fb95 = vault_fb2.consider_entry(r_fb95, Decimal('80.00'), Decimal('0.01'), 1800000000000, ask=Decimal('80.00'))
        self.assertTrue(fb95['placed'])
        self.assertEqual(Decimal(fb95['quantity']), Decimal('9.000'))
        self.assertEqual(
            Decimal(AccountVault._fill_price({'price': '72.03', 'executedQty': '6.854', 'cummulativeQuoteQty': '495.2015'})),
            Decimal('72.25'),
        )

        # Plenty of cash: score=75 opens ~500 USDT (6.25 SOL), score=100 opens ~1000 USDT (12.5 SOL), 3rd signal blocked by SLOTS_FULL
        transport.usdt_free = '3000'
        full_dir = self.root / 'full'
        full_dir.mkdir(exist_ok=True)
        vault_full = AccountVault(full_dir, clock=lambda: 1800000000000, transport=transport)
        vault_full.save(self.key, self.secret)
        vault_full.enable_live()
        strat_db = make_strat('s_full.sqlite', 't1-min', '75', 1800000000000)
        r0 = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda: 1800000000000)
        p75 = vault_full.consider_entry(r0, Decimal('80.00'), Decimal('0.01'), 1800000000000, ask=Decimal('80.00'))
        self.assertTrue(p75['placed'])
        self.assertEqual(Decimal(p75['quantity']), Decimal('6.250'))

        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                       ('SOLUSDT', 'T1', 't1-max', json.dumps({
                           'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': 't1-max',
                           'accepted': True, 'entry_low': '80.00', 'entry_high': '80.50',
                           'stop_distance': '0.010', 'score': '100',
                           'signal_time': 1800001000000, 'expires_at': 1800001060000,
                       })))
        r1 = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda: 1800001000000)
        p100 = vault_full.consider_entry(r1, Decimal('80.00'), Decimal('0.01'), 1800001000000, ask=Decimal('80.00'))
        self.assertTrue(p100['placed'])
        self.assertEqual(Decimal(p100['quantity']), Decimal('12.500'))

        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                       ('SOLUSDT', 'T1', 't1-third', json.dumps({
                           'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': 't1-third',
                           'accepted': True, 'entry_low': '80.00', 'entry_high': '80.50',
                           'stop_distance': '0.010', 'score': '90',
                           'signal_time': 1800002000000, 'expires_at': 1800002060000,
                       })))
        r2 = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda: 1800002000000)
        blocked = vault_full.consider_entry(r2, Decimal('80.00'), Decimal('0.01'), 1800002000000, ask=Decimal('80.00'))
        self.assertFalse(blocked['placed'])
        self.assertEqual(blocked['reason'], 'SLOTS_FULL')

    def test_sub_5_usdt_dust_order_market_closed_and_excluded_from_slots_live_and_shadow(self):
        from solbot.execution.p3_shadow import P3ShadowExecutor
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        from test_p3_apts_shadow import NOW, NORMAL, RULES, features, signal

        strat_db = self.root / 'decisions_dust.sqlite'
        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
            db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                       ('SOLUSDT', 'T1', 't1-dust-seed', json.dumps({
                           'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': 't1-dust-seed',
                           'accepted': True, 'entry_low': '79.00', 'entry_high': '81.00',
                           'stop_distance': '0.010', 'score': '75',
                           'signal_time': 1800000000000, 'expires_at': 1800000060000,
                       })))

        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='3000', sol_free='20', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        r0 = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda: 1800000000000)
        seed = vault.consider_entry(r0, Decimal('80.00'), Decimal('0.01'), 1800000000000, ask=Decimal('80.00'))
        self.assertTrue(seed['placed'])
        # Shrink seeded bot order to residual 0.04 SOL (< $5 at $80) -> must NOT count toward slots
        with vault._db() as db:
            db.execute("UPDATE bot_orders SET status='FILLED', quantity='0.04', quote='3.20' WHERE signal_id='t1-dust-seed'")
        self.assertEqual(len(vault.open_bot_slots()), 0)
        self.assertFalse(vault.has_open_bots(include_dust=False))
        self.assertTrue(vault.has_open_bots(include_dust=True))

        # Two full bot slots can still open despite the residual < 5 USDT order
        for idx in (1, 2):
            sid = f't1-full-{idx}'
            ts = 1800000000000 + idx * 1000000
            with closing(sqlite3.connect(strat_db)) as db, db:
                db.execute('INSERT INTO decisions(symbol,strategy_id,signal_id,payload_json) VALUES(?,?,?,?)',
                           ('SOLUSDT', 'T1', sid, json.dumps({
                               'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'signal_id': sid,
                               'accepted': True, 'entry_low': '79.00', 'entry_high': '81.00',
                               'stop_distance': '0.010', 'score': '75',
                               'signal_time': ts, 'expires_at': ts + 60000,
                           })))
            ri = DataReader({'SOLUSDT_strategy': strat_db}, clock=lambda t=ts: t)
            res = vault.consider_entry(ri, Decimal('80.00'), Decimal('0.01'), ts, ask=Decimal('80.00'))
            self.assertTrue(res['placed'])

        # sync_bot_stops / advance liquidates the < 5 USDT dust order at MARKET SELL and marks CLOSED
        ev = TrendEvidence(ready=True, atr=Decimal('0.3'), previous_atr=Decimal('0.3'))
        synced = vault.sync_bot_stops(Decimal('80.00'), Decimal('0.01'), 1800002010000, evidence=ev)
        self.assertTrue(any(a.get('signal_id') == 't1-dust-seed' and a.get('status') == 'CLOSED' and a.get('reason') == 'DUST_BELOW_5_USDT' for a in synced['actions']))
        self.assertTrue(any(m == 'POST' and p == '/api/v3/order' and 'side=SELL' in q and 'type=MARKET' in q for m, p, q in transport.calls))
        with vault._db() as db:
            dust_row = db.execute("SELECT status FROM bot_orders WHERE signal_id='t1-dust-seed'").fetchone()
        self.assertEqual(dust_row[0], 'CLOSED')

        # Shadow P3ShadowExecutor: < 5 USDT open position excluded from _occupied_slots and market-closed on process_market
        shadow = P3ShadowExecutor(self.root / 'p3_dust.sqlite', rules=RULES, fee_rate='0.00075', minimum_notional='500', maximum_notional='1000')
        with shadow._transaction() as db:
            db.execute(
                """INSERT INTO shadow_positions(position_id,account_id,symbol,strategy_id,signal_id,quantity,entry_price,entry_cost,entry_fee,status,opened_at)
                   VALUES ('p-dust','SOL-shadow','SOLUSDT','T1','sig-dust','0.04','100','4','0.003','OPEN',?)""",
                (NOW - 5000,),
            )
            self.assertEqual(shadow._occupied_slots(db), 0)
        o1 = shadow.submit_candidate(signal(1, price='100'), notional='500', now_ms=NOW)
        o2 = shadow.submit_candidate(signal(2, price='100'), notional='500', now_ms=NOW)
        self.assertEqual(o1['status'], 'PENDING')
        self.assertEqual(o2['status'], 'PENDING')
        mk = shadow.process_market(features(1000, bid='100', ask='100.01', quantity='100'), NORMAL, now_ms=NOW + 1000)
        self.assertTrue(any(f.get('close_reason') == 'dust_below_5_usdt_market_exit' for f in mk['fills']))
        with shadow._transaction() as db:
            st = db.execute("SELECT status, close_reason FROM shadow_positions WHERE position_id='p-dust'").fetchone()
        self.assertEqual(tuple(st), ('CLOSED', 'dust_below_5_usdt_market_exit'))

    def test_manual_trade_loss_excluded_from_daily_loss_live_risk(self):
        guardian_db = self.root / 'guardian_risk.sqlite'
        AllocationLedger(guardian_db, account_id='SOL-live', symbol='SOLUSDT')
        with closing(sqlite3.connect(guardian_db)) as db, db:
            # Manual spot loss dropped equity from 2000 to 1700 (-15%) -> guardian locked, but bot had $0 loss
            db.execute("UPDATE account_allocations SET allocated='2000', live_state='HARD_LOCKED', equity_at=?", (1800000000000,))
            db.execute("UPDATE risk_high_watermarks SET high='2000', equity='1700'")

        market_db = self.root / 'market_risk.sqlite'
        with closing(sqlite3.connect(market_db)) as db, db:
            db.execute("CREATE TABLE market_klines(symbol TEXT, interval TEXT, is_closed INTEGER, close_time INTEGER, close TEXT)")
            db.execute("INSERT INTO market_klines VALUES('SOLUSDT','1m',1,1800000000000,'80.00')")

        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='1700', sol_free='0', sol_locked='0')
        vault_dir = self.root / 'vault_risk'
        vault_dir.mkdir(exist_ok=True)
        vault = AccountVault(vault_dir, clock=lambda: 1800000005000, transport=transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        reader = DataReader({'guardian': guardian_db, 'binance_vault': vault_dir / 'binance-vault.sqlite', 'SOLUSDT_market': market_db}, clock=lambda: 1800000005000)
        bot_risk = vault.bot_daily_risk(mark_price=Decimal('80.00'))
        self.assertEqual(bot_risk['daily_loss_usdt'], '0.00')
        self.assertEqual(bot_risk['loss_scope'], 'BOT_ONLY_EXCLUDES_MANUAL')
        risk_doc = reader.live_risk()
        self.assertEqual(risk_doc['daily_loss_usdt'], '0.00')
        self.assertEqual(risk_doc['drawdown_pct'], '0.00')
        self.assertEqual(risk_doc['state'], 'NORMAL')
        self.assertEqual(risk_doc['loss_scope'], 'BOT_ONLY_EXCLUDES_MANUAL')

        # Now record a real bot closed loss of $12.50 today -> bot daily loss reflects ONLY the $12.50 bot loss, not $300 manual loss
        with vault._db() as db:
            db.execute(
                """INSERT INTO bot_orders(client_order_id,signal_id,exchange_order_id,strategy_id,status,price,quantity,quote,exit_price,realized_pnl,closed_at,created_at)
                   VALUES('cbot-loss-1','sig-loss-1','9001','T1','CLOSED','80.00','12.5','1000','79.00','-12.50',?,?)""",
                (1800000002000, 1800000001000),
            )
        bot_risk_after = vault.bot_daily_risk(mark_price=Decimal('80.00'))
        self.assertEqual(bot_risk_after['daily_loss_usdt'], '12.50')
        risk_after = reader.live_risk()
        self.assertEqual(risk_after['daily_loss_usdt'], '12.50')
        self.assertEqual(risk_after['drawdown_pct'], '0.62')

    def test_manual_cost_matches_latest_lots_and_custody_does_not_force_dump_above_stop(self):
        from solbot.mathematics.trend_exhaustion import TrendEvidence
        orders = [
            {'side': 'BUY', 'status': 'FILLED', 'price': '82.11', 'executed_qty': '12.571', 'time': 100},
            {'side': 'BUY', 'status': 'FILLED', 'price': '75.88', 'executed_qty': '25.384', 'time': 200},
            {'side': 'BUY', 'status': 'FILLED', 'price': '70.79', 'executed_qty': '11.294', 'time': 300},
            {'side': 'BUY', 'status': 'FILLED', 'price': '70.92', 'executed_qty': '9.873', 'time': 400},
        ]
        # Even when earlier lots were closed by cbotsl (not in manual_orders), target_qty=9.873881 matches the latest buy lot ($70.92)
        self.assertEqual(DataReader._average_entry(orders, target_qty=Decimal('9.873881')), Decimal('70.92'))
        orders.append({'side': 'BUY', 'status': 'FILLED', 'price': '71.90', 'executed_qty': '15.949', 'time': 500})
        self.assertEqual(DataReader._average_entry(orders, target_qty=Decimal('25.822881')), Decimal('71.53'))

        # Verify manual custody in S1/S2 does NOT cancel the resting stop to market-dump when CONFIRMED_REVERSAL hits above limit_price
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='10.0', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.protect_manual('10.0')
        rev_ev = TrendEvidence(
            ready=True, higher_low_1m=False, higher_low_5m=False,
            rvol=Decimal('2.0'), atr=Decimal('0.80'), previous_atr=Decimal('0.35'),
            buy_ratio=Decimal('0.30'), previous_buy_ratio=Decimal('0.60'), adverse_move=True,
        )
        # Advance into S1 (+1.0%) with severe adverse move: must stay OPEN and raise stop, NOT switch to EXITING/cbotex
        s1 = vault.advance(Decimal('82.84') * Decimal('1.010'), rev_ev, Decimal('0.01'), 1800000020000)
        self.assertEqual(vault.protection()['status'], 'OPEN')
        self.assertTrue(vault.protection()['client_order_id'].startswith('cbotsl'))
        self.assertEqual(s1['stage'], 'S1')

    def test_manual_custody_tightens_top_exhaustion_and_clamps_trailing_touch(self):
        from solbot.mathematics.trend_exhaustion import TrendEvidence

        class AnchoredTransport(FakeTransport):
            def request(self, path, query, headers, method='GET'):
                if path == '/api/v3/ticker/bookTicker':
                    return {'bidPrice': '71.85', 'askPrice': '71.86'}
                return super().request(path, query, headers, method)

        transport = AnchoredTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='18.912', sol_locked='0')
        vault = self.vault(transport)
        vault.save(self.key, self.secret)
        vault.protect_manual('18.912')

        strong_ev = TrendEvidence(
            ready=True, higher_low_1m=True, higher_low_5m=True, rvol=Decimal('1.8'),
            atr=Decimal('0.25'), previous_atr=Decimal('0.20'),
            buy_ratio=Decimal('0.60'), previous_buy_ratio=Decimal('0.55'),
            rsi=Decimal('89.2'), previous_rsi=Decimal('84.0'),
        )
        # Peak $73.30 (+2.02% >= +2.00%) enters S2; 1st bar exhaustion stays un-tightened, 2nd consecutive 1m bar tightens stop to 0.40% ($73.01)
        s2_peak = vault.advance('73.30', strong_ev, '0.01', 1800000010000)
        self.assertEqual(s2_peak['stage'], 'S2')

        # Exhaustion turn-down (RSI 87.4 -> 75.2, buy_ratio 0.361)
        exhaust_ev = TrendEvidence(
            ready=True, higher_low_1m=True, higher_low_5m=True, rvol=Decimal('1.5'),
            atr=Decimal('0.25'), previous_atr=Decimal('0.20'),
            buy_ratio=Decimal('0.361'), previous_buy_ratio=Decimal('0.671'),
            rsi=Decimal('75.2'), previous_rsi=Decimal('87.4'),
        )
        bar1 = vault.advance('73.15', exhaust_ev, '0.01', 1800000015000)
        self.assertLess(Decimal(bar1['stop_price']), Decimal('73.01'))
        tight = vault.advance('73.12', exhaust_ev, '0.01', 1800000065000)
        self.assertEqual(Decimal(tight['stop_price']), Decimal('73.01'))
        self.assertEqual(vault.protection()['status'], 'OPEN')

        # If a lagged poll lands just below the candidate while still above resting limit_floor, clamp cbotsl up to bid-tick
        vault2 = AccountVault(self.root / 'vault_lag', clock=lambda: 1800000000000, transport=AnchoredTransport(restrictions(enableSpotAndMarginTrading=True), sol_free='18.912', sol_locked='0'))
        vault2.save(self.key, self.secret)
        vault2.protect_manual('18.912')
        vault2.advance('73.30', strong_ev, '0.01', 1800000010000)
        vault2.advance('73.15', exhaust_ev, '0.01', 1800000015000)
        lagged = vault2.advance('72.95', exhaust_ev, '0.01', 1800000065000)
        self.assertEqual(vault2.protection()['status'], 'OPEN')
        self.assertTrue(vault2.protection()['client_order_id'].startswith('cbotsl'))
        self.assertEqual(Decimal(lagged['stop_price']), Decimal('72.94'))

    def test_trading_config_customizes_order_usdt_and_max_slots_for_live_and_shadow(self):
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='5000', sol_free='0', sol_locked='0')
        vault_dir = self.root / 'vault_cfg'
        vault_dir.mkdir(exist_ok=True)
        vault = AccountVault(vault_dir, clock=lambda: 1800000005000, transport=transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        # Default config is 500-1000 USDT, 2 slots
        default_cfg = vault.trading_config()
        self.assertEqual(default_cfg['min_order_usdt'], '500.00')
        self.assertEqual(default_cfg['max_order_usdt'], '1000.00')
        self.assertEqual(default_cfg['max_slots'], 2)

        # Save custom fixed $1200 per order and 3 slots
        saved = vault.save_trading_config('1200', '1200', 3)
        self.assertEqual(saved['min_order_usdt'], '1200.00')
        self.assertEqual(saved['max_order_usdt'], '1200.00')
        self.assertTrue(saved['fixed_mode'])
        self.assertEqual(saved['max_slots'], 3)
        self.assertTrue((vault_dir / 'trading-config.json').exists())

        class Reader:
            def read(self, key, sql, args=()):
                return [{
                    'payload_json': json.dumps({
                        'symbol': 'SOLUSDT', 'strategy_id': 'T1', 'accepted': True,
                        'signal_id': 'sig-cfg-1', 'expires_at': 1900000000000,
                        'entry_low': '80', 'score': 80,
                    }),
                }]

        placed = vault.consider_entry(Reader(), Decimal('80'), Decimal('0.01'), 1800000005000)
        self.assertTrue(placed['placed'])
        self.assertEqual(Decimal(placed['quantity']) * Decimal(placed['price']), Decimal('1200.00'))

        # Verify P3ShadowExecutor and R2ShadowExecutor sync with the custom config (including virtual cash scaling for 3 x $1200)
        from solbot.execution.p3_shadow import P3ShadowExecutor
        from solbot.execution.r2_shadow import R2ShadowExecutor
        from solbot.mathematics.contracts import SymbolRules
        rules = SymbolRules(
            'SOLUSDT', Decimal('0.01'), Decimal('0.001'), Decimal('0.01'), Decimal('100000'),
            Decimal('0.001'), Decimal('100000'), Decimal('5'), Decimal('1000000'),
        )
        p3_exec = P3ShadowExecutor(self.root / 'p3_cfg.sqlite', rules=rules)
        p3_exec.apply_trading_config('1200', '1200', 3)
        self.assertEqual(p3_exec.minimum_notional, Decimal('1200'))
        self.assertEqual(p3_exec.maximum_notional, Decimal('1200'))
        self.assertEqual(p3_exec.maximum_slots, 3)
        self.assertGreaterEqual(Decimal(p3_exec.snapshot()['cash']), Decimal('3600'))

        r2_exec = R2ShadowExecutor(self.root / 'r2_cfg.sqlite', rules=rules)
        r2_exec.apply_trading_config('1200', '1200', 3)
        self.assertEqual(r2_exec.minimum_notional, Decimal('1200'))
        self.assertEqual(r2_exec.maximum_notional, Decimal('1200'))
        self.assertEqual(r2_exec.maximum_slots, 3)

    def test_r2_live_chase_requote_candidate_low_guard_expired_reentry_and_fast_entry(self):
        from solbot.app_api.custody_apts import step
        from solbot.mathematics.trend_exhaustion import TrendEvidence

        r2_db = self.root / 'r2_chase_live.sqlite'
        strat_db = self.root / 'strat_chase_live.sqlite'
        with closing(sqlite3.connect(strat_db)) as db, db:
            db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy_id TEXT, signal_id TEXT, payload_json TEXT)')
        with closing(sqlite3.connect(r2_db)) as db, db:
            db.execute('CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY, payload TEXT)')
            sig = {
                'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'signal_id': 'r2-chase-live-1',
                'accepted': True, 'entry_low': '71.85', 'entry_high': '72.06555',
                'candidate_low': '71.17', 'stop_distance': '0.010', 'score': '85',
                'signal_time': 1800000000000, 'expires_at': 1800000300000,
            }
            db.execute('INSERT INTO r2_outbox VALUES(?,?)', ('r2-chase-live-1', json.dumps(sig)))

        reader = DataReader({'SOLUSDT_strategy': strat_db, 'r2_state': r2_db}, clock=lambda: 1800000000000)
        transport = FakeTransport(restrictions(enableSpotAndMarginTrading=True), usdt_free='3000', sol_free='0', sol_locked='0')
        vault_dir = self.root / 'vault_chase'
        vault_dir.mkdir(exist_ok=True)
        vault = AccountVault(vault_dir, clock=lambda: 1800000000000, transport=transport)
        vault.save(self.key, self.secret)
        vault.enable_live()

        with patch.dict(os.environ, {'SOLBOT_LIVE_STRATEGIES': 'T1,T2,R2'}):
            # 1. Entry executes with cached _require_spot (0 extra /sapi/v1/account/apiRestrictions calls in consider_entry!)
            calls_before = list(transport.calls)
            placed = vault.consider_entry(reader, Decimal('71.84'), Decimal('0.01'), 1800000000000, ask=Decimal('71.85'))
            self.assertTrue(placed['placed'])
            self.assertEqual(placed['price'], '71.89')
            entry_paths = [p for _, p, _ in transport.calls[len(calls_before):]]
            self.assertNotIn('/sapi/v1/account/apiRestrictions', entry_paths)
            self.assertNotIn('/api/v3/time', entry_paths)

            # 2. Under 5s -> does not chase yet
            ev = TrendEvidence(ready=True, atr=Decimal('0.3'), previous_atr=Decimal('0.3'))
            early = vault.sync_bot_stops(Decimal('71.92'), Decimal('0.01'), 1800000003000, evidence=ev, ask=Decimal('71.93'))
            self.assertEqual(early['actions'][0]['status'], 'NEW')

            # 3. At +5s ask moved to 71.93 (<= 72.06555) -> cancels old order & replaces at 71.97 (preserving quote!)
            chased = vault.sync_bot_stops(Decimal('71.92'), Decimal('0.01'), 1800000005000, evidence=ev, ask=Decimal('71.93'))
            self.assertEqual(chased['actions'][0]['status'], 'CHASE_REPLACED')
            self.assertEqual(chased['actions'][0]['price'], '71.97')
            row1 = vault.bot_orders()[0]
            self.assertEqual(row1['price'], '71.97')
            self.assertLessEqual(Decimal(row1['quantity']) * Decimal(row1['price']), Decimal('1000.00'))

            # 4. If exchange expires early (e.g. EXPIRE_MAKER) while now < expires_at, 1 re-entry is allowed (and at most 1)
            transport.orders_by_cid[row1['client_order_id']]['status'] = 'EXPIRED'
            exp_res = vault.sync_bot_stops(Decimal('71.90'), Decimal('0.01'), 1800000010000, evidence=ev, ask=Decimal('71.91'))
            self.assertEqual(exp_res['actions'][0]['status'], 'EXPIRED')
            reentered = vault.consider_entry(reader, Decimal('71.90'), Decimal('0.01'), 1800000011000, ask=Decimal('71.91'))
            self.assertTrue(reentered['placed'])
            row2 = vault.bot_orders()[0]
            self.assertNotEqual(row2['client_order_id'], row1['client_order_id'])

            # Simulate a 2nd early expiry -> must NOT re-enter a 2nd time
            transport.orders_by_cid[row2['client_order_id']]['status'] = 'EXPIRED'
            vault.sync_bot_stops(Decimal('71.90'), Decimal('0.01'), 1800000015000, evidence=ev, ask=Decimal('71.91'))
            blocked = vault.consider_entry(reader, Decimal('71.90'), Decimal('0.01'), 1800000016000, ask=Decimal('71.91'))
            self.assertFalse(blocked['placed'])

            # 5. Candidate low break cancels pending NEW buy and blocks re-entry; also verify step() enters before refresh_account()
            with closing(sqlite3.connect(r2_db)) as db, db:
                sig2 = dict(sig, signal_id='r2-cand-low-break', signal_time=1800001000000, expires_at=1800001300000)
                db.execute('INSERT INTO r2_outbox VALUES(?,?)', ('r2-cand-low-break', json.dumps(sig2)))

            call_order = []
            orig_consider = vault.consider_entry
            orig_refresh = vault.refresh_account
            def wrap_consider(*a, **kw):
                call_order.append('consider_entry')
                return orig_consider(*a, **kw)
            def wrap_refresh(*a, **kw):
                call_order.append('refresh_account')
                return orig_refresh(*a, **kw)
            with patch.object(vault, 'consider_entry', side_effect=wrap_consider), \
                 patch.object(vault, 'refresh_account', side_effect=wrap_refresh):
                step_res = step(vault, reader=reader, now=1800001000000, bid=Decimal('71.84'), tick=Decimal('0.01'), evidence=ev, sync_account=True)
            self.assertEqual(call_order[:2], ['consider_entry', 'refresh_account'])
            self.assertTrue(step_res['entry']['placed'])

            # Break candidate_low (71.17 -> bid=71.10)
            brk = vault.sync_bot_stops(Decimal('71.10'), Decimal('0.01'), 1800001002000, evidence=ev, ask=Decimal('71.11'))
            self.assertEqual(brk['actions'][0]['status'], 'CANCELED')
            self.assertEqual(brk['actions'][0]['reason'], 'CANDIDATE_LOW_BROKEN')


if __name__ == '__main__':
    unittest.main()
