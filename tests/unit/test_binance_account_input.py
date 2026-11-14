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

        # Stage jump to S1 (+1.2% >= 1.0%) atomically replaces via /api/v3/order/cancelReplace
        s_raised = step(vault, now=1800000005000, bid=Decimal('101.30'), evidence=ev, tick=Decimal('0.01'))
        self.assertEqual(s_raised['bot_stops']['actions'][0]['status'], 'TRAIL_RAISED')
        self.assertEqual(s_raised['bot_stops']['actions'][0]['stage'], 'S1')
        self.assertEqual(sum(1 for _, p, _ in transport.calls if p == '/api/v3/order/cancelReplace'), cr_before + 1)

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

            # Inside band: uses ask price (min(entry_high, ask) = 82.85), R2 dynamic sizing ($500 cap), stores strategy_id & expires_at
            placed = vault.consider_entry(reader, Decimal('82.80'), Decimal('0.01'), 1800000000000, ask=Decimal('82.85'))
            self.assertTrue(placed['placed'])
            self.assertEqual(placed['strategy_id'], 'R2')
            self.assertEqual(placed['price'], '82.85')
            self.assertEqual(Decimal(placed['quantity']), Decimal('6.035'))
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


if __name__ == '__main__':
    unittest.main()
