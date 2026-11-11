"""P4 integration fixtures are synthetic; never call exchange endpoints."""
import unittest
import tempfile
import sqlite3
from pathlib import Path
from decimal import Decimal as D
from dataclasses import replace
from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.accounts.custody_permissions import CustodyPermissions
from solbot.risk.guardian import Guardian
from solbot.risk.permission_engine import TradeIntent
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.exposure_manager import ExposureManager
from solbot.execution.live import LiveControl, LiveExecutor
from solbot.p4_runtime import tick

class P4Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.l=AllocationLedger(self.root/'risk.sqlite')
        self.now=__import__('time').time_ns()//1000000
        self.intent=TradeIntent('one','signal','SUI-live','SUIUSDT','T1','p2-research-1','1','700','.992',self.now,self.now+60000,self.now,self.now)
        self.ready('0','2000')
    def ready(self,sui,cash):
        BalanceReconciliation(self.l).reconcile('reconcile:'+sui+':'+cash,{'SUI':{'free':sui,'locked':'0'},'USDT':{'free':cash,'locked':'0'}},self.now,at=self.now)
        DrawdownEngine(self.l).mark('mark:'+sui+':'+cash,{'SUI':'1'},self.now,at=self.now)
    def test_live_off_never_constructs_private_transport(self):
        calls=[]
        control=LiveControl(self.root/'live.sqlite')
        executor=LiveExecutor(control,guardian=Guardian(self.l),secret_loader=lambda:calls.append('secret'),private_client_factory=lambda:calls.append('private'))
        self.assertFalse(executor.execute_intent(self.intent).accepted)
        self.assertEqual(calls,[])
    def test_unavailable_guardian_fails_closed(self):
        control=LiveControl(self.root/'live.sqlite'); executor=LiveExecutor(control,guardian=Guardian(self.l))
        with self.l.transaction() as db: db.execute('DROP TABLE risk_high_watermarks')
        self.assertEqual(executor.execute_intent(self.intent).code,'PAUSE_GUARDIAN_UNAVAILABLE')
    def test_audit_update_and_delete_prohibited(self):
        for sql in ('UPDATE risk_events SET kind=\'forged\'','DELETE FROM risk_events'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.l.transaction() as db: db.execute(sql)
    def test_cancel_ack_releases_only_remaining_not_position(self):
        Guardian(self.l).evaluate(self.intent,at=self.now,reserve=True)
        e=ExposureManager(self.l); e.fill('fill','one','200','1','.2',at=self.now)
        e.cancel_request('cancel','one',at=self.now)
        self.assertEqual(D(self.l.snapshot()['reservations'][0]['remaining']),500)
        with self.assertRaises(ValueError): e.cancel_ack('bad','one',executed_quantity='0',at=self.now)
        e.cancel_ack('ack','one',executed_quantity='200',at=self.now)
        s=self.l.snapshot(); self.assertEqual(D(s['reservations'][0]['remaining']),0); self.assertEqual(D(s['positions'][0]['quantity']),200)
    def test_partial_exit_conserves_cash_and_keeps_protection(self):
        Guardian(self.l).evaluate(self.intent,at=self.now,reserve=True)
        ExposureManager(self.l).fill('buy','one','700','1','.7',at=self.now)
        self.ready('700','1299.3'); c=CustodyPermissions(self.l)
        c.reserve_sell('exit','SUI','100',at=self.now)
        c.sell_fill('sell','exit','40','1.1','.044',position_id='one',at=self.now)
        c.sell_fill('sell','exit','40','1.1','.044',position_id='one',at=self.now)
        s=self.l.snapshot(); self.assertEqual(D(s['cash']),D('1343.256')); self.assertEqual(D(s['positions'][0]['quantity']),660)
        self.assertIn('KEEP_PROTECTION',s['positions'][0]['protection'])
        with self.assertRaises(ValueError): c.reserve_sell('exit','SUI','100',at=self.now)
    def test_timeout_requires_cancel_ack(self):
        Guardian(self.l).evaluate(self.intent,at=self.now,reserve=True)
        with self.l.transaction() as db: db.execute('UPDATE fund_reservations SET expires_at=1')
        tick(self.l)
        s=self.l.snapshot(); self.assertEqual(s['reservations'][0]['status'],'CANCEL_PENDING'); self.assertEqual(D(s['reservations'][0]['remaining']),700)
    def test_reopen_does_not_reset_high_watermark(self):
        with self.l.transaction() as db: db.execute("UPDATE account_allocations SET cash='2200'")
        DrawdownEngine(self.l).mark('peak',{},self.now,at=self.now)
        self.assertEqual(D(AllocationLedger(self.l.path).snapshot()['high']),D('2200'))
    def test_partial_fill_does_not_resolve_unknown_remainder(self):
        Guardian(self.l).evaluate(self.intent,at=self.now,reserve=True)
        e=ExposureManager(self.l); e.unknown('unknown','one',at=self.now)
        e.fill('partial','one','100','1','.1',at=self.now)
        self.ready('100','1899.9')
        self.assertEqual(self.l.snapshot()['reservations'][0]['status'],'UNKNOWN')
        self.assertNotEqual(Guardian(self.l).evaluate(replace(self.intent,intent_id='two',signal_id='second'),at=self.now).decision,'ALLOW')

if __name__=='__main__': unittest.main()
