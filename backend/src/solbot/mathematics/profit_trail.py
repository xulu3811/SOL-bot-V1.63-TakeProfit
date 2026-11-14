"""Research profit trail. It does not replace APTS ``update_protection``.

The trail arms only after the high is more than 1.5% above entry. The resting
line is the higher of entry+1% and 99.5% of the high. RVOL and RSI are not inputs.
"""

from decimal import Decimal

from .decimal_utils import D

VERSION = "profit-trail-research-1"
ARM = Decimal("0.015")
GIVEBACK = Decimal("0.005")
FLOOR = Decimal("0.01")


def trail_line(entry, high):
    """Return the resting profit line, or None while the high is at or below +1.5%."""
    entry, high = D(entry, positive=True), D(high, positive=True)
    if high / entry - 1 <= ARM:
        return None
    return max(entry * (1 + FLOOR), high * (1 - GIVEBACK))


def price_exit(entry, high, bid, stop):
    """High-water trail. +1% is a floor, not an immediate take-profit."""
    entry, high, bid, stop = (D(value, positive=True) for value in (entry, high, bid, stop))
    if bid <= stop:
        return "STOP"
    if high / entry - 1 > ARM and bid <= max(high * (1 - GIVEBACK), entry * (1 + FLOOR)):
        return "TRAIL"
    return "HOLD"


def classify_trail(entry, high, bid, stop):
    """Same decision as ``price_exit``. The backtest fills a trail at ``trail_line``."""
    return price_exit(entry, high, bid, stop)
