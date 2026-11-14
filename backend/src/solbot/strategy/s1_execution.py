"""S1 research orders. Touch-fill is an assumption, not a live queue.

A one-minute low at or below the limit fills the whole size at that limit.
The book does not model queue position, partial fills, or latency. Live
routing stays closed: this module never calls an exchange client.
"""

from decimal import Decimal

from solbot.mathematics.decimal_utils import D, floor_step
from solbot.mathematics.protective_stop import initial_stop
from solbot.mathematics.s1_sizing import FEE, forced_exit_ms, size_order

TICK = Decimal("0.01")
QTY_STEP = Decimal("0.001")
LIMIT_MS = 5 * 60 * 1000
LOCK = Decimal("0.95")
ASSUMPTION = (
    "Research fills the full size when a later 1m low trades at or through the limit "
    "before the 5-minute expiry. Live matching can miss that touch."
)


def buy_limit(close) -> Decimal:
    return floor_step(D(close, "close", positive=True), TICK)


def limit_expires(signal_close_ms: int) -> int:
    if type(signal_close_ms) is not int or signal_close_ms < 0:
        raise ValueError("signal close time required")
    return signal_close_ms + LIMIT_MS


def simulate(minutes: list[dict], opportunities: list[dict], equity) -> dict:
    """One slot, 24h maximum hold, 5% peak lock. Opportunities stay counted when skipped."""
    cash = D(equity, "equity", positive=True)
    peak = cash
    locked = False
    position = None
    pending = None
    orders, fills, exits = [], [], []
    pierce = 0
    by_close = {int(row["close_time"]): row for row in opportunities}

    def liquidation(close_px):
        if position is None:
            return cash
        return cash + position["qty"] * close_px * (1 - FEE)

    for bar in minutes:
        close_time = int(bar["close_time"])
        open_time = int(bar["open_time"])
        open_, low, close = (D(bar[name], positive=True) for name in ("open", "low", "close"))
        if position is not None and close_time > position["entry_ms"]:
            if low <= position["stop"]:
                price = open_ if open_ < position["stop"] else position["stop"]
                cash += _exit(position, price, "PROTECTIVE_STOP", close_time, exits)
                position = None
            elif close_time >= position["deadline"]:
                cash += _exit(position, close, "HOLD_24H", close_time, exits)
                position = None
        if pending is not None and close_time > pending["expires"]:
            pending["status"] = "EXPIRED"
            orders.append(pending)
            pending = None
        if (
            pending is not None
            and position is None
            and open_time > pending["signal_ms"]
            and close_time <= pending["expires"]
            and low <= pending["limit_price"]
        ):
            position = _fill(pending, close_time)
            fills.append(position)
            if low <= position["stop"]:
                pierce += 1
            pending["status"] = "FILLED"
            orders.append(pending)
            pending = None
            cash -= position["notional"] + position["entry_fee"]
        equity_now = liquidation(close)
        if equity_now > peak:
            peak = equity_now
        if not locked and equity_now <= peak * LOCK:
            locked = True
            if pending is not None:
                pending["status"] = "CANCELED_LOCK"
                orders.append(pending)
                pending = None
        opportunity = by_close.get(close_time)
        if opportunity is None:
            continue
        limit = buy_limit(opportunity["close"])
        order = {
            "candidate_close_ms": close_time,
            "limit_price": limit,
            "expires": limit_expires(close_time),
            "signal_ms": close_time,
            "status": "WORKING",
        }
        if locked:
            order["status"] = "SKIPPED_LOCK"
            orders.append(order)
            continue
        if position is not None or pending is not None:
            order["status"] = "SKIPPED_SLOT"
            orders.append(order)
            continue
        qty, notional = size_order(equity_now, limit, QTY_STEP)
        if qty <= 0 or notional <= 0:
            order["status"] = "SKIPPED_SIZE"
            orders.append(order)
            continue
        order["qty"] = qty
        order["notional"] = notional
        pending = order

    if pending is not None:
        pending["status"] = "EXPIRED"
        orders.append(pending)
    if position is not None and minutes:
        last = minutes[-1]
        cash += _exit(position, D(last["close"], positive=True), "DATA_END", int(last["close_time"]), exits)
    return {"orders": orders, "fills": fills, "exits": exits, "pierce": pierce, "locked": locked, "final_equity": cash}


def _fill(order, entry_ms):
    fee = order["notional"] * FEE
    stop = initial_stop(order["limit_price"], TICK)
    return {
        "candidate_close_ms": order["candidate_close_ms"],
        "entry_ms": entry_ms,
        "entry_price": order["limit_price"],
        "qty": order["qty"],
        "notional": order["notional"],
        "entry_fee": fee,
        "stop": stop,
        "deadline": forced_exit_ms(entry_ms),
    }


def _exit(position, price, reason, exit_ms, exits):
    proceeds = position["qty"] * price
    fee = proceeds * FEE
    pnl = proceeds - fee - position["notional"] - position["entry_fee"]
    exits.append({
        "candidate_close_ms": position["candidate_close_ms"],
        "exit_ms": exit_ms,
        "exit_price": price,
        "reason": reason,
        "exit_fee": fee,
        "pnl": pnl,
    })
    return proceeds - fee
