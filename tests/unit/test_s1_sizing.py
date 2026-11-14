"""Two worst weeks of full stops stay strictly under the 5% lock."""
from decimal import Decimal
import unittest

from solbot.mathematics.s1_sizing import LOCK, STOP, WORST_TRADES, loss_fraction, max_notional, minimum_range, size_order


class S1SizingTests(unittest.TestCase):
    def test_minimum_range_covers_three_round_trips(self):
        self.assertEqual(minimum_range(), Decimal("0.0045"))
        self.assertGreater(minimum_range(), loss_fraction() - STOP)

    def test_two_worst_weeks_stay_under_the_lock(self):
        equity = Decimal("2000")
        notional = max_notional(equity)
        damage = WORST_TRADES * notional * loss_fraction()
        self.assertLess(damage, equity * LOCK)
        self.assertGreater(notional, Decimal("500"))
        self.assertLess(notional, Decimal("530"))

    def test_quantity_uses_the_sol_step(self):
        qty, actual = size_order("2000", "100.01")
        self.assertEqual(qty, qty.quantize(Decimal("0.001")))
        self.assertLessEqual(actual, max_notional("2000"))

    def test_float_equity_is_rejected(self):
        with self.assertRaises(ValueError):
            max_notional(2000.0)


if __name__ == "__main__":
    unittest.main()
