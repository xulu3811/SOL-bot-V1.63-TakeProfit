"""Tick-aligned limits, a five-minute order life, and a 24-hour hold."""
from decimal import Decimal
import unittest

from solbot.strategy.s1_execution import buy_limit, limit_expires, simulate


def minute(open_time, close, low=None, open_=None):
    low = close if low is None else low
    open_ = close if open_ is None else open_
    return {
        "open_time": open_time,
        "close_time": open_time + 59_999,
        "open": open_,
        "high": close,
        "low": low,
        "close": close,
    }


class S1ExecutionTests(unittest.TestCase):
    def test_limit_is_on_the_cent_grid_and_expires_in_five_minutes(self):
        self.assertEqual(buy_limit("100.019"), Decimal("100.01"))
        self.assertEqual(limit_expires(1_000_000), 1_300_000)

    def test_touch_fills_then_the_hold_ends_at_24h(self):
        signal = 1_000_000
        opportunity = {"close_time": signal, "close": "100.00", "block_id": 0}
        deadline_open = signal + 60_000 + 24 * 60 * 60 * 1000
        bars = [
            minute(signal - 59_999, "100.00"),
            minute(signal + 1, "100.20", low="99.90"),
            minute(deadline_open, "101.00", low="100.50"),
        ]
        # The signal bar's close_time must equal the opportunity clock.
        bars[0]["close_time"] = signal
        result = simulate(bars, [opportunity], "2000")
        self.assertEqual(len(result["fills"]), 1)
        self.assertEqual(result["fills"][0]["entry_price"], Decimal("100.00"))
        self.assertEqual(result["exits"][0]["reason"], "HOLD_24H")
        self.assertEqual(result["exits"][0]["exit_ms"], bars[2]["close_time"])

    def test_a_missed_limit_expires_without_a_fill(self):
        signal = 5_000_000
        bars = [
            minute(signal - 59_999, "100.00"),
            minute(signal + 1, "101.00", low="100.50"),
            minute(signal + 300_000, "101.00", low="100.50"),
        ]
        bars[0]["close_time"] = signal
        result = simulate(bars, [{"close_time": signal, "close": "100.00"}], "2000")
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["orders"][0]["status"], "EXPIRED")

    def test_an_open_slot_skips_the_next_opportunity(self):
        first = 10_000_000
        second = first + 8 * 60 * 60 * 1000
        bars = [
            minute(first - 59_999, "100.00"),
            minute(first + 1, "100.00", low="99.90"),
            minute(second - 59_999, "100.00"),
        ]
        bars[0]["close_time"] = first
        bars[2]["close_time"] = second
        result = simulate(
            bars,
            [{"close_time": first, "close": "100.00"}, {"close_time": second, "close": "100.00"}],
            "2000",
        )
        self.assertEqual(len(result["fills"]), 1)
        self.assertEqual(result["orders"][-1]["status"], "SKIPPED_SLOT")


if __name__ == "__main__":
    unittest.main()
