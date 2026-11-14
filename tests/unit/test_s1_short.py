"""Frozen S1 gates. Thresholds are not adjusted to the year count."""
from decimal import Decimal
import inspect
import unittest

from solbot.risk import guardian
from solbot.strategy import evaluate
from solbot.strategy.s1_short import (
    MIN_RANGE,
    block_id,
    is_downtrend,
    qualifies,
    select_opportunities,
)


def bar(close_time, open_, high, low, close, blocked=False):
    return {"close_time": close_time, "open": open_, "high": high, "low": low, "close": close, "blocked": blocked}


class S1RuleTests(unittest.TestCase):
    def test_range_floor_is_three_round_trips(self):
        self.assertEqual(MIN_RANGE, Decimal("0.0045"))

    def test_downtrend_uses_the_existing_definition(self):
        self.assertTrue(is_downtrend(1, 2, 1, 2, 25))
        self.assertFalse(is_downtrend(3, 2, 1, 2, 25))
        self.assertFalse(is_downtrend(1, 2, 1, 2, 24))
        self.assertTrue(is_downtrend(None, 2, 1, 2, 25))

    def test_red_or_narrow_or_blocked_bars_are_not_candidates(self):
        self.assertFalse(qualifies("100", "100.50", "100", "99.9", False))
        self.assertFalse(qualifies("100", "100.40", "100", "100.20", False))
        self.assertFalse(qualifies("100", "101", "100", "100.50", True))
        self.assertTrue(qualifies("100", "100.60", "100", "100.20", False))

    def test_only_the_first_bar_in_an_eight_hour_block_is_kept(self):
        start = 8 * 60 * 60 * 1000
        bars = [
            bar(start + 60_000, "100", "100.60", "100", "100.20"),
            bar(start + 120_000, "100", "101", "100", "100.80"),
            bar(start + 8 * 60 * 60 * 1000, "100", "100.70", "100", "100.30"),
        ]
        chosen = select_opportunities(bars)
        self.assertEqual([row["close_time"] for row in chosen], [start + 60_000, start + 8 * 60 * 60 * 1000])
        self.assertEqual(block_id(start + 60_000), block_id(start + 120_000))

    def test_evaluate_and_guardian_do_not_admit_s1(self):
        source = inspect.getsource(evaluate)
        self.assertNotIn("S1", source)
        self.assertNotIn("s1_short", source)
        guardian_source = inspect.getsource(guardian.Guardian.evaluate)
        self.assertIn("intent.strategy_id!='T1'", guardian_source)


if __name__ == "__main__":
    unittest.main()
