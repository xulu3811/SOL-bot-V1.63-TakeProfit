"""Replaying an S1 candidate does not create a second fill."""
from pathlib import Path
import tempfile
import unittest

from solbot.storage.s1_store import S1Store


class S1StoreTests(unittest.TestCase):
    def test_replay_keeps_a_single_fill(self):
        with tempfile.TemporaryDirectory() as folder:
            store = S1Store(Path(folder) / "s1.sqlite")
            opportunity = {"close_time": 1000, "block_id": 1, "open": "1", "high": "2", "low": "1", "close": "2"}
            order = {"candidate_close_ms": 1000, "status": "FILLED", "limit_price": "1.00", "qty": "1", "expires": 2000}
            fill = {"candidate_close_ms": 1000, "entry_ms": 1500, "entry_price": "1.00", "qty": "1", "notional": "1", "entry_fee": "0.00075"}
            exit_row = {"candidate_close_ms": 1000, "exit_ms": 1800, "exit_price": "1.01", "reason": "HOLD_24H", "exit_fee": "0.00075", "pnl": "0.01"}
            store.record([opportunity], [order], [fill], [exit_row])
            store.record([opportunity], [order], [fill], [exit_row])
            self.assertEqual(store.counts(), {"opportunities": 1, "orders": 1, "fills": 1, "exits": 1})
            store.close()


if __name__ == "__main__":
    unittest.main()
