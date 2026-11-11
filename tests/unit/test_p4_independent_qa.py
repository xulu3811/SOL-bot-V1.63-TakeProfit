"""Independent P4 adversarial QA: synthetic inputs, isolated SQLite only."""
from pathlib import Path
from contextlib import closing
from decimal import Decimal
import sqlite3
import tempfile
import unittest

from solbot.accounts.allocation_ledger import AllocationLedger

NOW = 1_800_000_000_000


class IndependentP4LedgerQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'guardian.sqlite'
        self.ledger = AllocationLedger(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_allocation_is_off_and_unverified(self):
        state = self.ledger.snapshot()
        self.assertEqual(Decimal(state['cash']), Decimal(2000))
        self.assertEqual(state['live_state'], 'LIVE_OFF')
        self.assertTrue(state['recovery_required'])
        self.assertFalse(state['live_ready'])

    def test_foreign_schema_does_not_gain_guardian_tables(self):
        foreign = Path(self.tmp.name) / 'foreign.sqlite'
        with closing(sqlite3.connect(foreign)) as db, db:
            db.execute('CREATE TABLE unrelated(v TEXT)')
            db.execute("INSERT INTO unrelated VALUES('preserve')")
        with self.assertRaises(ValueError):
            AllocationLedger(foreign)
        with closing(sqlite3.connect(foreign)) as db, db:
            self.assertEqual(db.execute('SELECT * FROM unrelated').fetchall(), [('preserve',)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('unrelated',)])

    def test_stale_writer_cannot_mutate_changed_owner(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE guardian_owner SET schema_version=5')
        with self.assertRaises(ValueError):
            with self.ledger.transaction() as db:
                db.execute("UPDATE account_allocations SET cash='0'")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT cash FROM account_allocations').fetchone()[0], '2000')

    def test_event_retry_is_noop_and_changed_payload_is_rejected(self):
        with self.ledger.transaction() as db:
            self.assertTrue(self.ledger.event(db, 'qa-event', 'cash', {'delta':'10'}, NOW))
        with self.ledger.transaction() as db:
            self.assertFalse(self.ledger.event(db, 'qa-event', 'cash', {'delta':'10'}, NOW+1))
        with self.assertRaises(ValueError):
            with self.ledger.transaction() as db:
                self.ledger.event(db, 'qa-event', 'cash', {'delta':'20'}, NOW+1)
        with self.ledger.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM risk_events').fetchone()[0], 1)

    def test_failure_rolls_back_financial_and_audit_writes_together(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.ledger.transaction() as db:
                db.execute("UPDATE account_allocations SET cash='1900'")
                self.ledger.event(db, 'rollback', 'cash', {'delta':'-100'}, NOW)
                db.execute("INSERT INTO risk_events VALUES('rollback','x','{}',1)")
        self.assertEqual(Decimal(self.ledger.snapshot()['cash']), Decimal(2000))
        with self.ledger.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM risk_events').fetchone()[0], 0)

    def test_isolated_restore_preserves_lock_high_watermark_and_audit(self):
        with self.ledger.transaction() as db:
            db.execute("UPDATE risk_high_watermarks SET high='2100',equity='1995',nav_high='1.05'")
            db.execute("UPDATE risk_hard_locks SET locked=1,reason='qa-boundary'")
            self.ledger.event(db, 'qa-lock', 'lock', {'dd':'.05'}, NOW)
        result = self.ledger.backup_restore(Path(self.tmp.name) / 'backup.sqlite')
        restored = AllocationLedger(result['restored'])
        self.assertEqual(restored.snapshot(), self.ledger.snapshot())
        self.assertEqual(result['integrity'], 'ok')





from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.guardian import Guardian
from solbot.risk.permission_engine import TradeIntent


def intent(index=0, **changes):
    value = TradeIntent('qa-'+str(index), 'signal-'+str(index), 'SUI-live', 'SUIUSDT',
                        'T1', 'p2-research-1', '1', '700', '.992', NOW,
                        NOW+10000, NOW, NOW)
    return replace(value, **changes)


class IndependentP4RiskQA(unittest.TestCase):
    def setUp(self):
        IndependentP4LedgerQA.setUp(self)
        self.risk = Guardian(self.ledger)
        self.dd = DrawdownEngine(self.ledger)
        self.recon = BalanceReconciliation(self.ledger)
        self.recon.reconcile('initial', {'USDT':{'free':'2000','locked':'0'}}, NOW, at=NOW)
        self.dd.mark('initial-mark', {}, NOW, at=NOW)

    def test_initial_allocation_is_off_and_unverified(self):
        # This fixture has reconciled a synthetic account, never production.
        self.assertEqual(self.ledger.snapshot()['live_state'], 'LIVE_OFF')
        self.assertFalse(self.ledger.snapshot()['live_ready'])

    def tearDown(self):
        self.tmp.cleanup()

    def test_parallel_two_700_orders_then_third_denied(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda i: self.risk.evaluate(intent(i), at=NOW, reserve=True), range(3)))
        self.assertEqual(sum(r.decision=='ALLOW' for r in results), 2)
        rows = self.ledger.snapshot()['reservations']
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(Decimal(r['remaining'])+Decimal(r['fees']) for r in rows), Decimal('1401.4'))
        self.assertTrue(all(not r.live_authorized for r in results))

    def test_idempotent_reservation_binds_full_intent(self):
        first = self.risk.evaluate(intent(), at=NOW, reserve=True)
        second = self.risk.evaluate(intent(), at=NOW+1, reserve=True)
        self.assertEqual(first.intent_hash, second.intent_hash)
        self.assertEqual(len(self.ledger.snapshot()['reservations']), 1)
        changed = self.risk.evaluate(intent(quantity='600'), at=NOW+1, reserve=True)
        self.assertEqual(changed.decision, 'DENY')
        self.assertEqual(self.ledger.snapshot()['reservations'][0]['principal'], '700')
        duplicate = self.risk.evaluate(intent(1, signal_id='signal-0'), at=NOW, reserve=True)
        self.assertEqual(duplicate.decision, 'DENY')

    def test_cross_account_symbol_r1_and_expired_are_denied(self):
        for changes in ({'account_id':'other'}, {'symbol':'SOLUSDT'}, {'strategy_id':'R1'},
                        {'expires_at':NOW}, {'market_at':NOW-2001}, {'math_at':NOW-5001},
                        {'market_at':NOW+1}, {'strategy_version':'unaudited'}):
            with self.subTest(changes=changes):
                result = self.risk.evaluate(intent(**changes), at=NOW, reserve=True)
                self.assertNotEqual(result.decision, 'ALLOW')
        self.assertEqual(self.ledger.snapshot()['reservations'], [])

    def test_exact_five_percent_hardlocks_and_preserves_reserved_cash(self):
        self.risk.evaluate(intent(), at=NOW, reserve=True)
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET cash='1900.0001'")
        self.dd.mark('just-above', {}, NOW+1, at=NOW+1)
        self.assertFalse(self.ledger.snapshot()['locked'])
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET cash='1900'")
        self.dd.mark('exact', {}, NOW+2, at=NOW+2)
        state = self.ledger.snapshot()
        self.assertTrue(state['locked'])
        self.assertEqual(state['live_state'], 'HARD_LOCKED')
        self.assertEqual(state['reservations'][0]['status'], 'CANCEL_PENDING')
        self.assertEqual(Decimal(state['reservations'][0]['remaining']), Decimal('700'))
        self.assertEqual(state['pending_actions'][0]['kind'], 'CANCEL_ENTRY')
        self.assertTrue(AllocationLedger(self.path).snapshot()['locked'])
        self.assertNotEqual(self.risk.evaluate(intent(1), at=NOW+2).decision, 'ALLOW')

    def test_external_deposit_and_withdrawal_preserve_drawdown_fraction(self):
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET cash='1950'")
        self.dd.mark('loss', {}, NOW+1, at=NOW+1)
        self.dd.external_flow('deposit', '500', actor='qa-admin', reason='synthetic', at=NOW+2)
        state = self.ledger.snapshot()
        self.assertAlmostEqual(Decimal(state['equity'])/Decimal(state['high']), Decimal('.975'), places=25)
        self.dd.external_flow('withdraw', '-500', actor='qa-admin', reason='synthetic', at=NOW+3)
        state = self.ledger.snapshot()
        self.assertAlmostEqual(Decimal(state['equity'])/Decimal(state['high']), Decimal('.975'), places=25)
        self.assertEqual(Decimal(state['cash']), Decimal('1950'))

    def test_manual_assets_never_inflate_robot_equity(self):
        self.recon.reconcile('manual', {'USDT':{'free':'8000','locked':'0'},
                                      'SUI':{'free':'9000','locked':'0'}}, NOW+1, at=NOW+1)
        self.dd.mark('mark-manual', {'SUI':'2'}, NOW+1, at=NOW+1)
        self.assertEqual(Decimal(self.ledger.snapshot()['equity']), Decimal('2000'))
        status = self.recon.reconcile('manual-reduced', {'USDT':{'free':'8000','locked':'0'},
                                                       'SUI':{'free':'8000','locked':'0'}}, NOW+2, at=NOW+2)
        self.assertEqual(status, 'UNRECONCILED')
        self.assertNotEqual(self.risk.evaluate(intent(), at=NOW+2).decision, 'ALLOW')

    def test_unknown_order_and_missing_price_pause_new_entry(self):
        self.recon.reconcile('unknown-order', {'USDT':{'free':'2000','locked':'0'}}, NOW,
                             unknown_orders=True, at=NOW)
        self.assertNotEqual(self.risk.evaluate(intent(), at=NOW).decision, 'ALLOW')
        with self.ledger.transaction() as db:
            self.ledger.set_owned(db, 'BNB', 'ROBOT_OWNED', '0.01')
        self.dd.mark('missing-bnb', {}, NOW+1, at=NOW+1)
        self.assertEqual(self.ledger.snapshot()['equity_at'], 0)
        self.assertNotEqual(self.risk.evaluate(intent(), at=NOW+1).decision, 'ALLOW')

    def test_final_live_gate_denies_even_forged_live_on(self):
        self.assertEqual(self.risk.authorize_live(intent(), at=NOW).decision, 'DENY')
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET live_state='LIVE_ON'")
        final = self.risk.authorize_live(intent(), at=NOW)
        self.assertEqual(final.code, 'DENY_NOT_READY')
        self.assertFalse(final.live_authorized)

    def test_less_than_300_risk_capacity_is_not_rounded_up(self):
        request = intent(quantity='300', stop_price='.950')
        self.assertEqual(self.risk.evaluate(request, at=NOW).code, 'DENY_RISK_BUDGET')
        self.assertEqual(self.ledger.snapshot()['reservations'], [])


from solbot.accounts.custody_permissions import CustodyPermissions
from solbot.risk.exposure_manager import ExposureManager
from solbot.risk.recovery_guard import RecoveryGuard


class IndependentP4LifecycleQA(unittest.TestCase):
    def setUp(self):
        IndependentP4RiskQA.setUp(self)
        self.exposure = ExposureManager(self.ledger)
        self.custody = CustodyPermissions(self.ledger)
        self.recovery = RecoveryGuard(self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_fill_cash_plus_inventory_plus_fee_is_conserved(self):
        self.risk.evaluate(intent(), at=NOW, reserve=True)
        self.exposure.fill('fill-1', 'qa-0', '200', '.99', '.198', at=NOW+1)
        state = self.ledger.snapshot()
        self.assertEqual(Decimal(state['cash']), Decimal('1801.802'))
        self.assertEqual(Decimal(state['cash'])+Decimal(state['positions'][0]['cost'])+Decimal('.198'), Decimal(2000))
        self.assertEqual(Decimal(state['reservations'][0]['remaining']), Decimal(500))
        self.assertEqual(Decimal(state['positions'][0]['risk'])+Decimal(state['reservations'][0]['risk']), Decimal('7.7'))
        self.exposure.fill('fill-1', 'qa-0', '200', '.99', '.198', at=NOW+2)
        self.assertEqual(self.ledger.snapshot(), state)
        with self.assertRaises(ValueError):
            self.exposure.fill('fill-1', 'qa-0', '201', '.99', '.198', at=NOW+2)
        self.exposure.cancel_request('cancel', 'qa-0', at=NOW+2)
        self.assertEqual(Decimal(self.ledger.snapshot()['reservations'][0]['remaining']), Decimal(500))
        with self.assertRaises(ValueError):
            self.exposure.cancel_ack('bad-ack', 'qa-0', executed_quantity='201', at=NOW+3)
        self.exposure.cancel_ack('ack', 'qa-0', executed_quantity='200', at=NOW+3)
        after = self.ledger.snapshot()
        self.assertEqual(Decimal(after['reservations'][0]['remaining']), Decimal(0))
        self.assertEqual(after['positions'], state['positions'])
        self.assertEqual(after['cash'], state['cash'])

    def test_unknown_order_survives_balance_only_reconciliation(self):
        self.risk.evaluate(intent(), at=NOW, reserve=True)
        self.exposure.unknown('unknown', 'qa-0', at=NOW+1)
        self.recon.reconcile('balances-again', {'USDT':{'free':'2000','locked':'0'}}, NOW+2, at=NOW+2)
        result = self.risk.evaluate(intent(1), at=NOW+2, reserve=True)
        self.assertNotEqual(result.decision, 'ALLOW')

    def test_custody_cannot_reuse_approval_after_revocation(self):
        self.recon.reconcile('manual', {'USDT':{'free':'2000','locked':'0'},
                                      'SUI':{'free':'100','locked':'0'}}, NOW, at=NOW)
        self.custody.grant('grant', 'SUI', '20', actor='qa-admin', reason='synthetic', expires_at=NOW+1000, at=NOW)
        self.custody.reserve_sell('exit', 'SUI', '10', category='MANUAL_CUSTODIED', authorization_id='grant', at=NOW)
        self.custody.revoke('revoke', 'grant', actor='qa-admin', reason='synthetic', at=NOW+1)
        with self.assertRaises(ValueError):
            self.custody.reserve_sell('exit', 'SUI', '10', category='MANUAL_CUSTODIED', authorization_id='grant', at=NOW+2)

    def test_unmanaged_asset_and_oversell_are_denied(self):
        self.recon.reconcile('manual', {'USDT':{'free':'2000','locked':'0'},
                                      'SUI':{'free':'100','locked':'0'}}, NOW, at=NOW)
        for category in ('ROBOT_OWNED','MANUAL_UNMANAGED','UNRECONCILED'):
            with self.subTest(category=category), self.assertRaises(ValueError):
                self.custody.reserve_sell('sell-'+category, 'SUI', '1', category=category, at=NOW)
        self.custody.grant('grant', 'SUI', '20', actor='qa-admin', reason='synthetic', expires_at=NOW+1000, at=NOW)
        with self.assertRaises(ValueError):
            self.custody.reserve_sell('oversell', 'SUI', '21', category='MANUAL_CUSTODIED', authorization_id='grant', at=NOW)

    def test_restart_retains_protection_and_authorization_intent_but_pauses(self):
        self.risk.evaluate(intent(), at=NOW, reserve=True)
        self.exposure.fill('fill', 'qa-0', '200', '1', '.2', at=NOW+1)
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET live_state='LIVE_ON',authorization_intent=1")
        before = self.ledger.snapshot()
        self.recovery.restart('restart', at=NOW+2)
        after = self.ledger.snapshot()
        self.assertEqual(after['live_state'], 'PAUSED')
        self.assertEqual(after['authorization_intent'], 1)
        self.assertEqual(after['positions'], before['positions'])
        self.assertEqual(after['reservations'], before['reservations'])
        self.assertEqual(after['high'], before['high'])

from solbot.risk.bnb_reserve import BNBReserve
from solbot.risk.apts_compat import resolve_legacy
from solbot.risk.contracts import APTSParameters


class IndependentP4BNBQA(unittest.TestCase):
    def setUp(self):
        IndependentP4RiskQA.setUp(self)
        self.bnb = BNBReserve(self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def ready_bnb(self, quantity='0', price='100', index=0):
        with self.ledger.transaction() as db:
            self.ledger.set_owned(db, 'BNB', 'ROBOT_OWNED', quantity)
            db.execute("UPDATE account_allocations SET live_state='LIVE_ON'")
        self.recon.reconcile('bnb-reconcile-'+str(index), {'USDT':{'free':'2000','locked':'0'},
                             'BNB':{'free':quantity,'locked':'0'}}, NOW, at=NOW)
        self.dd.mark('bnb-mark-'+str(index), {'BNB':price}, NOW, at=NOW)

    def candidate(self, key='bnb', **changes):
        params = dict(price='100', step='.001', min_notional='5', observed_at=NOW, permission=True, at=NOW)
        params.update(changes)
        return self.bnb.candidate(key, **params)

    def test_exact_three_does_not_refill_but_below_three_does(self):
        self.ready_bnb('.03')
        self.assertEqual(self.candidate()['code'], 'DENY_REFILL_NOT_NEEDED')
        self.ready_bnb('.029', index=1)
        result = self.candidate('bnb-next')
        self.assertEqual(result['decision'], 'ALLOW')
        self.assertEqual(Decimal(result['notional']), Decimal(10))
        self.assertFalse(result['live_authorized'])
        self.assertEqual(self.candidate('bnb-next')['code'], 'DENY_DUPLICATE_REFILL')
        self.assertEqual(self.candidate('bnb-other')['code'], 'DENY_REFILL_PENDING')

    def test_bnb_off_and_filter_minimum_cannot_force_refill(self):
        self.assertEqual(self.candidate()['code'], 'DENY_LIVE_STATE')
        self.ready_bnb()
        self.assertEqual(self.candidate('too-large', min_notional='21')['decision'], 'DENY')
        self.assertEqual(self.bnb.fee_rate(discount_confirmed=False), Decimal('.001'))

    def test_unknown_bnb_intent_keeps_cash_committed_and_blocks_entry(self):
        self.ready_bnb()
        self.assertEqual(self.candidate()['decision'], 'ALLOW')
        self.bnb.failed('bnb-unknown', 'bnb', at=NOW)
        self.recon.reconcile('balance-only', {'USDT':{'free':'2000','locked':'0'},
                             'BNB':{'free':'0','locked':'0'}}, NOW, at=NOW)
        result = self.risk.evaluate(intent(), at=NOW, reserve=True)
        self.assertNotEqual(result.decision, 'ALLOW')
        with self.ledger.transaction() as db:
            row = db.execute("SELECT reserve,status FROM bnb_refill_intents WHERE intent_id='bnb'").fetchone()
            self.assertEqual(Decimal(row['reserve']), Decimal('10.01'))
            self.assertEqual(row['status'], 'UNKNOWN')

    def test_legacy_apts_compatibility_does_not_claim_native_protection(self):
        legacy = APTSParameters()
        self.assertEqual(legacy.implementation_status, 'NOT_IMPLEMENTED')
        capability = resolve_legacy(legacy)
        self.assertEqual(capability.mathematics, 'P3_IMPLEMENTED')
        self.assertEqual(capability.native_protection, 'P5_NOT_ACCEPTED')
        self.assertFalse(capability.live_authorized)





class IndependentP4AdminQA(unittest.TestCase):
    def setUp(self):
        IndependentP4RiskQA.setUp(self)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unlock_requires_os_root_and_explicit_authorization_and_preserves_high(self):
        from unittest.mock import patch
        import json
        guard = RecoveryGuard(self.ledger)
        with self.ledger.transaction() as db:
            DrawdownEngine.lock(db, 'qa-lock')
        with patch('os.geteuid', return_value=1000, create=True), self.assertRaises(PermissionError):
            guard.administrator_unlock('unlock', reason='qa', user_authorization='explicit-ref', at=NOW)
        with patch('os.geteuid', return_value=0, create=True):
            with self.assertRaises(ValueError):
                guard.administrator_unlock('unlock', reason='qa', user_authorization='', at=NOW)
            self.assertEqual(guard.request_on('on', actor='qa-admin', at=NOW), 'DENY_HARD_LOCKED')
            guard.administrator_unlock('unlock', reason='qa', user_authorization='explicit-ref', at=NOW)
        state = self.ledger.snapshot()
        self.assertEqual(state['live_state'], 'PAUSED')
        self.assertEqual(Decimal(state['high']), Decimal(2000))
        self.assertTrue(state['recovery_required'])
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM risk_events WHERE event_id='unlock'").fetchone()
            payload = json.loads(row['payload'])
            self.assertEqual(payload['uid'], 0)
            self.assertEqual(payload['user_authorization'], 'explicit-ref')
            self.assertEqual(payload['reason'], 'qa')
            self.assertEqual(row['created_at'], NOW)
            self.assertIn('high', payload['risk'])

    def test_bnb_total_owned_cap_includes_locked_inventory(self):
        bnb = BNBReserve(self.ledger)
        with self.ledger.transaction() as db:
            self.ledger.set_owned(db, 'BNB', 'ROBOT_OWNED', '.19')
            db.execute("UPDATE account_allocations SET live_state='LIVE_ON'")
        self.recon.reconcile('bnb', {'USDT':{'free':'2000','locked':'0'},
                                    'BNB':{'free':'.02','locked':'.17'}}, NOW, at=NOW)
        self.dd.mark('bnb-value', {'BNB':'100'}, NOW, at=NOW)
        result = bnb.candidate('cap', price='100', step='.001', min_notional='5',
                               observed_at=NOW, permission=True, at=NOW)
        self.assertEqual(result['code'], 'DENY_REFILL_CAP_OR_MINIMUM')

    def test_bnb_retry_limit_cannot_be_reset_with_new_intent_id(self):
        bnb = BNBReserve(self.ledger)
        with self.ledger.transaction() as db:
            db.execute("UPDATE account_allocations SET live_state='LIVE_ON'")
        for i in range(3):
            key = 'refill-'+str(i)
            result = bnb.candidate(key, price='100', step='.001', min_notional='5',
                                   observed_at=NOW, permission=True, at=NOW)
            self.assertEqual(result['decision'], 'ALLOW')
            bnb.failed('failed-'+str(i), key, definitely_not_submitted=True, at=NOW)
            self.recon.reconcile('recon-'+str(i), {'USDT':{'free':'2000','locked':'0'}}, NOW, at=NOW)
        result = bnb.candidate('fourth', price='100', step='.001', min_notional='5',
                               observed_at=NOW, permission=True, at=NOW)
        self.assertEqual(result['code'], 'DENY_RETRY_LIMIT')


if __name__ == '__main__':
    unittest.main()

