"""Bounded aggTrade and bookTicker normalization; no invented exchange timestamp."""
from collections import deque
from decimal import Decimal, localcontext

from solbot.mathematics.orderbook import decimal_value, update_id
from solbot.storage.models import timestamp_ms, validate_symbol


class TradeWindow:
    def __init__(self, symbol, *, window_ms=60_000, max_trades=20_000):
        self.symbol = validate_symbol(symbol)
        if not 1000 <= window_ms <= 300_000 or not 2 <= max_trades <= 50_000:
            raise ValueError("invalid bounded trade window")
        self.window_ms, self.max_trades = window_ms, max_trades
        self.reset()

    def reset(self):
        self.trades = deque(maxlen=self.max_trades)
        self.last_id = None
        self.last_event_ms = self.last_received_ms = None
        self.last_trade_ms = None
        self.complete_after_ms = None
        self.gaps = 0

    def add(self, payload, received_ms):
        if not isinstance(payload, dict) or payload.get("e") != "aggTrade" or payload.get("s") != self.symbol:
            raise ValueError("unexpected aggregate trade symbol/type")
        trade_id = update_id(payload.get("a"))
        event_ms, trade_ms, received_ms = timestamp_ms(payload.get("E")), timestamp_ms(payload.get("T")), timestamp_ms(received_ms)
        if type(payload.get("m")) is not bool or trade_ms > event_ms:
            raise ValueError("invalid taker direction or trade event time")
        price, quantity = decimal_value(payload.get("p")), decimal_value(payload.get("q"))
        first_raw, last_raw = update_id(payload.get("f")), update_id(payload.get("l"))
        if first_raw > last_raw:
            raise ValueError("reversed constituent trade identifiers")
        if self.last_id is not None and trade_id <= self.last_id:
            return False
        if self.last_event_ms is not None and event_ms < self.last_event_ms:
            raise ValueError("trade event time regressed")
        if self.last_trade_ms is not None and trade_ms < self.last_trade_ms:
            raise ValueError("trade execution time regressed")
        if self.last_id is not None and trade_id != self.last_id + 1:
            self.trades.clear()
            self.complete_after_ms = event_ms + self.window_ms
            self.gaps += 1
        if self.complete_after_ms is None:
            self.complete_after_ms = event_ms + self.window_ms
        while self.trades and self.trades[0][0] <= trade_ms - self.window_ms:
            self.trades.popleft()
        if len(self.trades) == self.max_trades:
            self.complete_after_ms = max(self.complete_after_ms, self.trades[0][0] + self.window_ms)
        self.trades.append((trade_ms, price, quantity, not payload["m"], last_raw - first_raw + 1))
        self.last_id, self.last_event_ms, self.last_received_ms = trade_id, event_ms, received_ms
        self.last_trade_ms = trade_ms
        return True

    def features(self, now_ms):
        now_ms = timestamp_ms(now_ms)
        while self.trades and self.trades[0][0] <= now_ms - self.window_ms:
            self.trades.popleft()
        rows = [row for row in self.trades if now_ms - self.window_ms < row[0] <= now_ms]
        with localcontext() as context:
            context.prec = 40
            volume = sum((row[2] for row in rows), Decimal(0))
            quote_volume = sum((row[1] * row[2] for row in rows), Decimal(0))
            active_buy = sum((row[1] * row[2] for row in rows if row[3]), Decimal(0))
            returns = [current[1] / previous[1] - 1 for previous, current in zip(rows, rows[1:])]
            mean = sum(returns, Decimal(0)) / len(returns) if returns else Decimal(0)
            volatility = (sum(((value - mean) ** 2 for value in returns), Decimal(0)) / len(returns)).sqrt() if returns else None
            return {"buy_ratio": str(active_buy / quote_volume) if quote_volume else None,
                    "trade_volume": str(volume), "trade_quote_volume": str(quote_volume),
                    "trade_count": len(rows), "raw_trade_count": sum(row[4] for row in rows),
                    "short_volatility": str(volatility) if volatility is not None else None,
                    "trade_event_time_ms": self.last_event_ms, "trade_received_time_ms": self.last_received_ms,
                    "trade_window_complete": self.complete_after_ms is not None and now_ms >= self.complete_after_ms,
                    "trade_gap_count": self.gaps, "trade_window_ms": self.window_ms}


class BookTicker:
    def __init__(self, symbol):
        self.symbol = validate_symbol(symbol)
        self.value = None

    def add(self, payload, received_ms):
        if not isinstance(payload, dict) or payload.get("s") != self.symbol:
            raise ValueError("bookTicker symbol mismatch")
        uid = update_id(payload.get("u"))
        received_ms = timestamp_ms(received_ms)
        event_ms = timestamp_ms(payload["E"]) if "E" in payload else None
        bid, ask = decimal_value(payload.get("b")), decimal_value(payload.get("a"))
        bid_qty, ask_qty = decimal_value(payload.get("B")), decimal_value(payload.get("A"))
        if bid >= ask:
            raise ValueError("crossed bookTicker")
        if self.value is not None and uid <= self.value["update_id"]:
            return False
        self.value = {"update_id": uid, "bid": str(bid), "ask": str(ask), "bid_qty": str(bid_qty), "ask_qty": str(ask_qty),
                      "exchange_time_ms": event_ms, "received_time_ms": received_ms}
        return True
