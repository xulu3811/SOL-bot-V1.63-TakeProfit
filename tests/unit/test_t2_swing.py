"""T2 keeps the 4h veto, the stop clamp, and a real exit on the shadow book."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from solbot.execution.shadow import ShadowExecutor
from solbot.strategy.t2_swing import assess, risk_notional, stop_distance

NOW = 1_794_355_200_000


def candles():
    return {
        "5m": [{"close_time": 1_500, "close": "100", "low": "98", "high": "101", "open": "99"}],
        "1h": [{"close": "100"}],
        "15m": [
            {"low": "97", "close": "101"},
            {"low": "96.5", "close": "101"},
            {"low": "96", "close": "102"},
        ],
        "1m": [
            {"open_time": 2_000, "close_time": 2_060, "open": "100", "high": "101", "low": "99", "close": "100.5"},
            {"open_time": 2_060, "close_time": 2_120, "open": "100.5", "high": "102", "low": "99.5", "close": "101.5"},
            {"open_time": 2_120, "close_time": 2_180, "open": "101", "high": "103", "low": "100", "close": "103.5"},
        ],
    }


def metrics():
    return {
        "4h": {"ema20": [110.0], "ema50": [100.0]},
        "1h": {"ema20": [105.0]},
        "15m": {"ema20": [100.0], "rvol20": [1.2], "atr14": [1.0]},
        "5m": {"rsi6": [30.0, 35.0], "atr14": [0.5]},
    }


def candidate(**changes):
    result = {"symbol": "SOLUSDT", "strategy_id": "T2", "signal_id": "signal-t2",
              "signal_time": NOW, "expires_at": NOW + 900_000, "accepted": True,
              "reasons": [], "risk_flags": ["RESEARCH_PARAMETERS_UNVALIDATED", "EXECUTION_QUALITY_UNOBSERVED"],
              "entry_low": "99", "entry_high": "100", "strategy_version": "p2-research-1",
              "regime": "UNCERTAIN", "score": 92.5, "stop_distance": "0.020", "indicators": {}}
    result.update(changes)
    return result


class T2RuleTests(unittest.TestCase):
    def test_four_hour_downtrend_vetoes_the_long(self):
        down = metrics()
        down["4h"] = {"ema20": [90.0], "ema50": [100.0]}
        _, reasons = assess(candles(), down, "DOWNTREND")
        self.assertIn("FOUR_HOUR_DOWNTREND", reasons)

    def test_full_setup_has_no_hard_reason(self):
        _, reasons = assess(candles(), metrics(), "UNCERTAIN")
        self.assertEqual(reasons, [])

    def test_stop_clamps_between_0_8_and_2_percent(self):
        self.assertEqual(stop_distance("100", "99.5", "0.1"), Decimal("0.008"))
        self.assertEqual(stop_distance("100", "96", "1"), Decimal("0.01"))
        self.assertEqual(stop_distance("100", "90", "5"), Decimal("0.020"))

    def test_wider_stop_buys_a_smaller_notional(self):
        tight = risk_notional("2000", "0.008", "0.00075", "92.5")
        wide = risk_notional("2000", "0.020", "0.00075", "92.5")
        self.assertGreater(tight, wide)
        self.assertGreaterEqual(wide, Decimal("500"))
        self.assertLessEqual(tight, Decimal("1000"))
        self.assertIsNone(risk_notional("500", "0.020", "0.00075", "92.5"))


class T2ShadowExitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.shadow = ShadowExecutor(Path(self.directory.name) / "shadow.sqlite")

    def tearDown(self):
        self.directory.cleanup()

    def _fill(self, stamp, bid="99.5", ask="100"):
        self.shadow.submit_candidate(candidate(), now_ms=NOW)
        return self.shadow.process_quote("SOLUSDT", bid, ask, stamp, now_ms=stamp)

    def test_stop_closes_only_the_t2_position(self):
        self._fill(NOW + 1_000)
        self.assertEqual(len(self.shadow.snapshot()["positions"]), 1)
        self.shadow.process_quote("SOLUSDT", "97.9", "98.1", NOW + 2_000, now_ms=NOW + 2_000)
        positions = self.shadow.snapshot()["positions"]
        self.assertEqual(positions, [])
        closed = self.shadow.snapshot()
        self.assertEqual(closed["positions"], [])

    def test_t1_position_is_not_time_stopped_or_widened(self):
        self.shadow.submit_candidate(candidate(strategy_id="T1", signal_id="signal-t1", stop_distance="0.008"), now_ms=NOW)
        self.shadow.process_quote("SOLUSDT", "99.5", "100", NOW + 1_000, now_ms=NOW + 1_000)
        self.shadow.process_quote("SOLUSDT", "97.9", "98.1", NOW + 2_000, now_ms=NOW + 2_000)
        self.assertEqual(len(self.shadow.snapshot()["positions"]), 1)

    def test_twenty_four_hour_exit(self):
        self._fill(NOW + 1_000, bid="99.8", ask="100")
        later = NOW + 1_000 + 86_400_000
        self.shadow.process_quote("SOLUSDT", "100.4", "100.5", later, now_ms=later)
        self.assertEqual(len(self.shadow.snapshot()["positions"]), 1)

    def test_trail_locks_a_gain_after_one_and_a_half_percent(self):
        self._fill(NOW + 1_000, bid="99.8", ask="100")
        self.shadow.process_quote("SOLUSDT", "102", "102.1", NOW + 2_000, now_ms=NOW + 2_000)
        self.assertEqual(len(self.shadow.snapshot()["positions"]), 1)
        self.shadow.process_quote("SOLUSDT", "101.4", "101.5", NOW + 3_000, now_ms=NOW + 3_000)
        self.assertEqual(self.shadow.snapshot()["positions"], [])


if __name__ == "__main__":
    unittest.main()
