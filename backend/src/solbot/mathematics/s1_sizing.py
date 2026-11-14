"""S1 notional cap. Two worst weeks of full stops stay under the 5% entry lock.

The cap uses the high end of the 7-10 trades/week band and the 0.8% stop plus
the 0.15% round trip. It is not fit to the year sample's profits.
"""

from decimal import Decimal

from solbot.mathematics.decimal_utils import D, floor_step
from solbot.mathematics.execution_cost import FeeSchedule

VERSION = "s1-size-1"
FEE = FeeSchedule(bnb_discount_enabled=True).rate("TAKER")
ROUND_TRIP = FEE + FEE
STOP = Decimal("0.008")
LOCK = Decimal("0.05")
WORST_WEEKS = 2
TRADES_PER_WEEK = 10
WORST_TRADES = WORST_WEEKS * TRADES_PER_WEEK
HOLD_MS = 24 * 60 * 60 * 1000


def loss_fraction() -> Decimal:
    """Price stop plus both sides of the BNB taker fee. No extra slippage knob."""
    return STOP + ROUND_TRIP


def minimum_range() -> Decimal:
    """Observable high-low must cover three round trips before a bar can be a candidate."""
    return ROUND_TRIP * 3


def max_notional(equity) -> Decimal:
    equity = D(equity, "equity", positive=True)
    raw = equity * LOCK / (Decimal(WORST_TRADES) * loss_fraction())
    return floor_step(raw, Decimal("0.01"))


def size_order(equity, limit_price, step="0.001"):
    cap = max_notional(equity)
    price = D(limit_price, "limit_price", positive=True)
    qty = floor_step(cap / price, D(step, "step", positive=True))
    return qty, qty * price


def forced_exit_ms(entry_ms: int) -> int:
    if type(entry_ms) is not int or entry_ms < 0:
        raise ValueError("entry time required")
    return entry_ms + HOLD_MS
