"""Bounded Spot diff-depth book; snapshot alone never establishes synchronization."""
from decimal import Decimal, InvalidOperation

from solbot.storage.models import timestamp_ms, validate_symbol


class SequenceGap(ValueError):
    """Discard the book and obtain a new stream-buffered REST snapshot."""


def decimal_value(value, *, zero=False):
    if not isinstance(value, (str, Decimal)) or len(str(value)) > 64:
        raise ValueError("market price/quantity must be a bounded decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid market decimal") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not zero) or (number and not -24 <= number.adjusted() <= 24):
        raise ValueError("nonfinite, nonpositive or excessive market decimal")
    return number


def update_id(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("invalid update identifier")
    return value


def parse_levels(rows, *, max_levels=5000):
    if not isinstance(rows, list) or len(rows) > max_levels:
        raise ValueError("unbounded/invalid price levels")
    result = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("price level requires price and quantity")
        price, quantity = decimal_value(row[0]), decimal_value(row[1], zero=True)
        if price in result:
            raise ValueError("duplicate price within a depth payload")
        result[price] = quantity
    return result


class OrderBook:
    def __init__(self, symbol, *, max_levels=2048):
        self.symbol = validate_symbol(symbol)
        if not 20 <= max_levels <= 10000:
            raise ValueError("max_levels must be 20..10000 per side")
        self.max_levels = max_levels
        self.reset()

    def reset(self):
        self.bids, self.asks = {}, {}
        self.update_id = None
        self.synced = False
        self.exchange_time_ms = None
        self.received_time_ms = None
        self.bid_coverage_floor = None
        self.ask_coverage_ceiling = None

    def load_snapshot(self, payload, received_ms):
        if not isinstance(payload, dict) or payload.get("s", self.symbol) != self.symbol:
            raise ValueError("snapshot symbol mismatch")
        last = update_id(payload.get("lastUpdateId"))
        received_ms = timestamp_ms(received_ms)
        bids = {p: q for p, q in parse_levels(payload.get("bids"), max_levels=self.max_levels).items() if q}
        asks = {p: q for p, q in parse_levels(payload.get("asks"), max_levels=self.max_levels).items() if q}
        if not bids or not asks or max(bids) >= min(asks):
            raise ValueError("empty or crossed depth snapshot")
        self.reset()
        self.bids, self.asks = bids, asks
        self.update_id, self.received_time_ms = last, received_ms
        self.bid_coverage_floor, self.ask_coverage_ceiling = min(bids), max(asks)

    def apply(self, event, received_ms):
        if not isinstance(event, dict) or event.get("s") != self.symbol or event.get("e") != "depthUpdate":
            raise ValueError("unexpected depth event/symbol")
        first, last = update_id(event.get("U")), update_id(event.get("u"))
        received_ms, event_ms = timestamp_ms(received_ms), timestamp_ms(event.get("E"))
        if first > last:
            raise ValueError("depth event update range is reversed")
        if self.update_id is None:
            raise SequenceGap("snapshot required before applying buffered depth")
        if last <= self.update_id:
            return False
        if first > self.update_id + 1:
            self.reset()
            raise SequenceGap("missing depth updates; restart snapshot synchronization")
        if self.exchange_time_ms is not None and event_ms < self.exchange_time_ms:
            self.reset()
            raise SequenceGap("depth exchange time regressed")
        bids, asks = dict(self.bids), dict(self.asks)
        try:
            for target, changes in ((bids, event.get("b")), (asks, event.get("a"))):
                for price, quantity in parse_levels(changes, max_levels=self.max_levels).items():
                    if quantity:
                        target[price] = quantity
                    else:
                        target.pop(price, None)
            if len(bids) > self.max_levels or len(asks) > self.max_levels:
                raise SequenceGap("depth memory bound reached; resnapshot instead of silently dropping levels")
            if not bids or not asks or max(bids) >= min(asks):
                raise SequenceGap("empty or crossed incremental depth")
        except ValueError:
            self.reset()
            raise
        self.bids, self.asks = bids, asks
        self.update_id, self.exchange_time_ms, self.received_time_ms = last, event_ms, received_ms
        self.synced = True
        return True

    def levels(self, n=20):
        if type(n) is not int or not 1 <= n <= 100:
            raise ValueError("export depth must be 1..100")
        bids = sorted(self.bids.items(), reverse=True)[:n]
        asks = sorted(self.asks.items())[:n]
        if self.synced and (len(bids) < n or len(asks) < n or bids[-1][0] < self.bid_coverage_floor or asks[-1][0] > self.ask_coverage_ceiling):
            self.reset()
            raise SequenceGap("top depth left the known snapshot coverage; resnapshot required")
        return bids, asks
