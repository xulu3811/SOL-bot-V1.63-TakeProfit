"""JSON-safe public features with explicit freshness, coverage and source hash."""
from collections import deque
from decimal import Decimal, localcontext
import hashlib
import json

from .microstructure import depth_imbalance, microprice, spread_metrics
from .orderbook import SequenceGap


def input_hash(snapshot):
    payload = {key: value for key, value in snapshot.items() if key != "input_hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class MarketFeatures:
    def __init__(self, book, trades, ticker, *, depth_levels=20):
        if len({book.symbol, trades.symbol, ticker.symbol}) != 1:
            raise ValueError("feature components must belong to one symbol")
        self.book, self.trades, self.ticker = book, trades, ticker
        self.depth_levels = depth_levels
        self.quote_times = deque(maxlen=20000)
        self.quote_incomplete_until_ms = 0

    def quote_updated(self, received_ms):
        if len(self.quote_times) == self.quote_times.maxlen:
            self.quote_incomplete_until_ms = max(self.quote_incomplete_until_ms, self.quote_times[0] + 60000)
        self.quote_times.append(received_ms)

    def snapshot(self, now_ms, *, connected, rules, clock_offset_ms=0, extra_reasons=()):
        reasons = list(extra_reasons)
        exchange_now = now_ms + clock_offset_ms
        result = {"symbol": self.book.symbol, "ready": False, "synced": self.book.synced,
                  "reasons": reasons, "exchange_time_ms": self.book.exchange_time_ms,
                  "received_time_ms": self.book.received_time_ms, "feature_generated_time_ms": now_ms,
                  "update_id": self.book.update_id, "bid": None, "ask": None, "bid_qty": None,
                  "ask_qty": None, "bids": [], "asks": [], "microprice": None, "spread": None,
                  "spread_bps": None, "imbalance": None, "rules": rules,
                  "book_ticker_exchange_time_ms": None, "book_ticker_received_time_ms": None,
                  "feature_version": "p3-public-v1", "clock_offset_ms": clock_offset_ms}
        if not connected:
            reasons.append("PUBLIC_STREAM_DISCONNECTED")
        if not self.book.synced:
            reasons.append("ORDER_BOOK_UNSYNCHRONIZED")
        try:
            bids, asks = self.book.levels(self.depth_levels)
            if len(bids) < self.depth_levels or len(asks) < self.depth_levels:
                reasons.append("INSUFFICIENT_DEPTH_COVERAGE")
            if bids and asks:
                result.update(bids=[[str(p), str(q)] for p, q in bids], asks=[[str(p), str(q)] for p, q in asks],
                              bid=str(bids[0][0]), ask=str(asks[0][0]), bid_qty=str(bids[0][1]), ask_qty=str(asks[0][1]),
                              microprice=str(microprice(bids[0][0], asks[0][0], bids[0][1], asks[0][1])),
                              imbalance=str(depth_imbalance(bids, asks)))
                result.update({key: str(value) for key, value in spread_metrics(bids[0][0], asks[0][0]).items()})
        except SequenceGap:
            reasons.append("DEPTH_COVERAGE_LOST")
            result["synced"] = False
        for label, value, clock, max_age in (("DEPTH_RECEIPT", self.book.received_time_ms, now_ms, 5000),
                                            ("DEPTH_EVENT", self.book.exchange_time_ms, exchange_now, 5000)):
            if value is None or not -2000 <= clock - value <= max_age:
                reasons.append(label + "_STALE")
        result.update(self.trades.features(exchange_now))
        if not result["trade_window_complete"]:
            reasons.append("TRADE_WINDOW_INCOMPLETE")
        if result["short_volatility"] is None:
            reasons.append("SHORT_VOLATILITY_UNAVAILABLE")
        if result["trade_received_time_ms"] is None or now_ms - result["trade_received_time_ms"] > 15000:
            reasons.append("TRADE_RECEIPT_STALE")
        if result["trade_event_time_ms"] is None or not -2000 <= exchange_now - result["trade_event_time_ms"] <= 15000:
            reasons.append("TRADE_EVENT_STALE")
        ticker = self.ticker.value
        if ticker is not None:
            result.update(book_ticker_exchange_time_ms=ticker["exchange_time_ms"], book_ticker_received_time_ms=ticker["received_time_ms"], book_ticker=ticker)
        if ticker is None or now_ms - ticker["received_time_ms"] > 5000:
            reasons.append("BOOK_TICKER_STALE")
        if ticker is not None and ticker["exchange_time_ms"] is not None and not -2000 <= exchange_now - ticker["exchange_time_ms"] <= 5000:
            reasons.append("BOOK_TICKER_EVENT_STALE")
        if not isinstance(rules, dict) or rules.get("symbol") != self.book.symbol or rules.get("status") != "TRADING" or rules.get("isSpotTradingAllowed") is not True:
            reasons.append("SPOT_RULES_UNAVAILABLE")
        while self.quote_times and self.quote_times[0] <= now_ms - 60000:
            self.quote_times.popleft()
        with localcontext() as context:
            context.prec = 40
            result["quote_updates_per_sec"] = str(Decimal(len(self.quote_times)) / Decimal(60))
        result["quote_window_complete"] = now_ms >= self.quote_incomplete_until_ms
        if not result["quote_window_complete"]:
            reasons.append("QUOTE_WINDOW_CAPACITY_LOSS")
        result["ready"] = not reasons and self.book.synced
        result["input_hash"] = input_hash(result)
        return result
