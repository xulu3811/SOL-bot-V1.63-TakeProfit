import json
import tempfile
import unittest
from pathlib import Path
from solbot.app_api.data import DataReader

class ShadowHealthTests(unittest.TestCase):
    def test_health_requires_fresh_ready_engine_and_quote(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            reader = DataReader({'shadow_status': path}, clock=lambda: 100000)
            self.assertFalse(reader._shadow_health()['synced'])
            def check(value):
                path.write_text(json.dumps(value), encoding='utf-8')
                return reader._shadow_health()
            ready = {'symbol': 'SOLUSDT', 'status': 'ready', 'updated_at': 99000,
                     'shadow': {'last_quote_ms': 99000}}
            self.assertTrue(check(ready)['synced'])
            self.assertEqual(check(dict(ready, updated_at=80000))['reason'], 'ENGINE_HEARTBEAT_STALE')
            self.assertEqual(check(dict(ready, error='readonly database'))['reason'], 'ENGINE_ERROR')
            self.assertEqual(check(dict(ready, shadow={'last_quote_ms': 70000}))['reason'], 'SHADOW_QUOTE_STALE')
            self.assertFalse(check(dict(ready, status='protection_only'))['synced'])
            self.assertFalse(check(dict(ready, updated_at=101000))['synced'])
            self.assertFalse(check(dict(ready, symbol='SUIUSDT'))['synced'])
            self.assertFalse(check([])['synced'])

if __name__ == '__main__':
    unittest.main()
