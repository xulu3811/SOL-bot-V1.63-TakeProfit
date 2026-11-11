from dataclasses import asdict, replace
import unittest

from solbot.storage.models import INTERVAL_MS, Kline


START = 1_767_225_600_000  # 2026-01-01T00:00:00Z, aligned for all five intervals.


def candle(interval="1m", index=0, **changes):
    step = INTERVAL_MS[interval]
    start = START + index * step
    values = dict(
        symbol="SOLUSDT", interval=interval, open_time=start,
        close_time=start + step - 1, open="100.00", high="102.00",
        low="99.00", close="101.00", volume="10.00", quote_volume="1000.00",
        trade_count=20, taker_buy_base_volume="4.00", taker_buy_quote_volume="400.00",
        is_closed=True, source="rest", ingested_at=start + step + 1000,
    )
    values.update(changes)
    return Kline(**values)


def rest_row(interval="1m"):
    item = candle(interval)
    return [item.open_time, item.open, item.high, item.low, item.close, item.volume,
            item.close_time, item.quote_volume, item.trade_count,
            item.taker_buy_base_volume, item.taker_buy_quote_volume, "0"]


def ws_payload(is_closed=True):
    item = candle(source="ws", is_closed=is_closed)
    return {"e": "kline", "E": item.close_time + 1 if is_closed else item.open_time + 1000,
            "s": "SOLUSDT", "k": {
                "s": "SOLUSDT", "t": item.open_time, "T": item.close_time,
                "i": item.interval, "o": item.open, "h": item.high,
                "l": item.low, "c": item.close, "v": item.volume,
                "q": item.quote_volume, "n": item.trade_count,
                "V": item.taker_buy_base_volume, "Q": item.taker_buy_quote_volume,
                "x": item.is_closed,
            }}


class KlineTests(unittest.TestCase):
    def test_all_five_intervals_validate_without_precision_loss(self):
        for interval in INTERVAL_MS:
            with self.subTest(interval=interval):
                item = candle(interval, open="100.12345678901234567890123456789")
                self.assertIs(item.validate(), item)
                self.assertEqual(asdict(item)["open"], item.open)

    def test_rest_uses_server_clock_to_exclude_unfinished_candle(self):
        row = rest_row()
        self.assertFalse(Kline.from_rest(row, "1m", row[6]).is_closed)
        item = Kline.from_rest(row, "1m", row[6] + 1)
        self.assertTrue(item.is_closed)
        self.assertEqual(item.source, "rest")
        self.assertEqual(item.event_time, row[6] + 1)
        self.assertIsInstance(item.ingested_at, int)

    def test_ws_raw_and_combined_normalize(self):
        for wrapped in (False, True):
            payload = ws_payload()
            item = Kline.from_ws({"stream": "solusdt@kline_1m", "data": payload} if wrapped else payload)
            self.assertTrue(item.is_closed)
            self.assertEqual(item.source, "ws")
            self.assertEqual(item.event_time, payload["E"])
        self.assertFalse(Kline.from_ws(ws_payload(False)).is_closed)

    def test_millisecond_units_alignment_and_exact_close_time(self):
        for fields in (
            {"open_time": START // 1000}, {"open_time": START * 1000},
            {"open_time": float(START)}, {"open_time": True},
            {"open_time": START + 1}, {"close_time": START + 60_000},
            {"ingested_at": START // 1000}, {"event_time": START - 1},
            {"event_time": START + 59_999}, {"ingested_at": START},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(candle(), **fields).validate()

    def test_nonfinite_negative_inconsistent_and_nonstring_values(self):
        for fields in (
            {"high": "NaN"}, {"low": "Infinity"}, {"open": "-1"},
            {"open": "0"}, {"open": 100.0}, {"volume": "-1"},
            {"low": "100.50"}, {"high": "100.50"},
            {"taker_buy_base_volume": "10.01"}, {"taker_buy_quote_volume": "1000.01"},
            {"volume": " 10"}, {"volume": "1e999999999"},
            {"trade_count": True}, {"trade_count": -1}, {"trade_count": 2**63},
            {"is_closed": 1}, {"source": "other"}, {"symbol": "BTCUSDT"}, {"interval": "1M"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(candle(), **fields).validate()

    def test_malformed_exchange_messages_are_rejected(self):
        for row in ([], "bad", [1] * 11):
            with self.subTest(row=row), self.assertRaises(ValueError):
                Kline.from_rest(row, "1m", START)
        for payload in ({}, {"e": "bookTicker"}, {"e": "kline", "k": {}}, []):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                Kline.from_ws(payload)
        payload = ws_payload()
        payload["s"] = "BTCUSDT"
        with self.assertRaises(ValueError):
            Kline.from_ws(payload)


if __name__ == "__main__":
    unittest.main()
