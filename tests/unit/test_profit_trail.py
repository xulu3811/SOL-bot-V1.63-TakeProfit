"""A +1.8% high trails at 99.5% of that high. RVOL is not an input."""
from decimal import Decimal
import unittest

from solbot.mathematics.profit_trail import price_exit, trail_line


class ProfitTrailTests(unittest.TestCase):
    def test_eighteen_tenths_trails_at_995_of_the_high(self):
        line = trail_line("100", "101.8")
        self.assertEqual(line, Decimal("101.8") * Decimal("0.995"))
        self.assertEqual(price_exit("100", "101.8", "101.29", "99.2"), "TRAIL")
        self.assertEqual(price_exit("100", "101.8", "101.30", "99.2"), "HOLD")

    def test_plus_one_percent_does_not_sell_before_the_arm(self):
        self.assertIsNone(trail_line("100", "101.4"))
        self.assertEqual(price_exit("100", "101.4", "101", "99.2"), "HOLD")

    def test_initial_stop_wins_over_an_unarmed_trail(self):
        self.assertEqual(price_exit("100", "100.4", "99.2", "99.2"), "STOP")


if __name__ == "__main__":
    unittest.main()
