from contextlib import closing
from pathlib import Path
import hashlib,json,sqlite3,tempfile,unittest
from unittest.mock import patch
from test_p3_pricing import features,NOW
from solbot.p3_runtime import plan_signal,scheduler_status
from solbot.mathematics.decimal_utils import json_safe
from solbot.storage.math_audit import MathAudit,normalize_signal

def candidate(symbol='SOLUSDT'):
    return dict(symbol=symbol,strategy_id='T1',strategy_version='p2-research-1',signal_id='test-math',
        accepted=True,signal_time=NOW-1000,expires_at=NOW+60000,entry_high='100.2',score=92.5,
        score_factors={'execution':7.5},indicators={'5m':{'atr14':'.1'}})
ACCOUNT=dict(cash='2000',reserved='0',last_equity='2000',peak_equity='2000',hard_locked=0,positions=[],pending_orders=[])

class RuntimeTests(unittest.TestCase):
    def test_minute_boundary_scheduler_scores_are_display_strings(self):
        report={'samples':{'T1':{'score':75.5,'score_factors':{'x':7.5},'entry_high':'83.01'}}}
        self.assertEqual(json_safe(scheduler_status(report))['samples']['T1']['score'],'75.5')
        self.assertEqual(report['samples']['T1']['score'],75.5)
        with self.assertRaises(ValueError):scheduler_status({'score':float('nan')})
    def test_plan_deterministic_insufficient_evidence_no_network(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            a=plan_signal(candidate(),features(),ACCOUNT,NOW)
            self.assertEqual(a,plan_signal(candidate(),features(),ACCOUNT,NOW))
        self.assertIn('MATH_NOT_APPROVED',a[2]['gate_reasons'])
        self.assertFalse(a[2]['live_authorized'])
        self.assertEqual(a[2]['expected']['status'],'INSUFFICIENT_EVIDENCE')
    def test_original_float_payload_hash_retained(self):
        raw=candidate();value,original=normalize_signal(raw)
        self.assertEqual(value['score'],'92.5')
        self.assertEqual(value['p2_original_payload_hash'],hashlib.sha256(original.encode()).hexdigest())
        self.assertEqual(json.loads(original),raw)
    def test_audit_compressed_dedup_restart_and_foreign_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'audit.sqlite'
            audit=MathAudit(path,'SOLUSDT');s,o,r=plan_signal(candidate(),features(),ACCOUNT,NOW)
            inputs={'signal':s,'original':o,'features':features()}
            audit.record('test-math',NOW,inputs,r);audit.record('test-math',NOW+1,inputs,{'ignored':True})
            reopened=MathAudit(path,'SOLUSDT')
            self.assertEqual(reopened.read('test-math')['result'],r)
            with self.assertRaises(ValueError):MathAudit(path,'SUIUSDT')
            with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM math_records').fetchone()[0],1)
    def test_stale_foreign_expired_and_missing_indicators(self):
        stale=features();stale['exchange_time_ms']=NOW-10000
        self.assertIn('STALE_OR_FUTURE_BOOK',plan_signal(candidate(),stale,ACCOUNT,NOW)[2]['gate_reasons'])
        with self.assertRaises(ValueError):plan_signal(candidate(),features('SUIUSDT'),ACCOUNT,NOW)
        signal=candidate();signal['expires_at']=NOW-1
        self.assertIn('SIGNAL_EXPIRED_OR_FUTURE',plan_signal(signal,features(),ACCOUNT,NOW)[2]['gate_reasons'])
        signal['indicators']={}
        self.assertIn('INDICATORS_UNAVAILABLE',plan_signal(signal,features(),ACCOUNT,NOW)[2]['gate_reasons'])
