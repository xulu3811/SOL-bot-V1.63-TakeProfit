"""Independent formulas and explicit positive/negative research fixtures."""

from copy import deepcopy
from decimal import Decimal
import math
import unittest

from solbot.storage.models import INTERVAL_MS
from solbot.strategy import evaluate, indicators, prepare
from solbot.strategy.signal_scoring import ScoreConfig, score

BOUNDARY = 1_800_000_000_000 // INTERVAL_MS["4h"] * INTERVAL_MS["4h"]
AS_OF = BOUNDARY + 120_000 - 1


def bars(interval, closes, symbol="SOLUSDT", as_of=AS_OF):
    step = INTERVAL_MS[interval]
    end = ((as_of + 1) // step) * step
    rows = []
    for i, value in enumerate(closes):
        opening = end - (len(closes) - i) * step
        price = Decimal(str(value))
        rows.append({"symbol": symbol, "interval": interval, "open_time": opening,
                     "close_time": opening + step - 1, "open": str(price - Decimal("0.03")),
                     "close": str(price), "low": str(price - Decimal("0.1")),
                     "high": str(price + Decimal("0.1")), "volume": "100",
                     "quote_volume": str(price * 100), "trade_count": 100,
                     "taker_buy_base_volume": "50", "taker_buy_quote_volume": str(price * 50),
                     "is_closed": True, "source": "rest", "ingested_at": opening + step + 20,
                     "event_time": opening + step + 10})
    return rows


def positive_fixture(strategy="T1", symbol="SOLUSDT"):
    """Source OHLC fixtures; no strategy/indicator function is patched."""
    trend = [100 + i * 0.2 for i in range(120)]
    sideways = [100 + (0.5 if i % 2 else -0.5) for i in range(120)]
    data = {interval: bars(interval, trend if strategy == "T1" else sideways, symbol)
            for interval in INTERVAL_MS}
    if strategy == "T1":
        close = [80 + i * 0.15 for i in range(116)]
        peak = close[-1]
        close += [peak - .6, peak - 1.2, peak - 1.6, peak - .8]
    else:
        close = [100 + (.1 if i % 2 else -.1) for i in range(114)]
        close += [99.5, 98.9, 98.3, 97.7, 97.1, 98.0]
    data["5m"] = bars("5m", close, symbol)
    last = data["5m"][-1]
    last["volume"] = "200"
    last["quote_volume"] = str(Decimal(last["close"]) * 200)
    last["taker_buy_base_volume"] = "140"
    last["taker_buy_quote_volume"] = str(Decimal(last["quote_volume"]) * Decimal("0.7"))
    price = close[-1]
    data["1m"] = bars("1m", [price - .5] * 117 + [price - .3, price - .15, price + .1], symbol)
    return data


def reference_ema(values, n):
    """Closed-form finite geometric weights, independent of iterative EMA."""
    seed = sum(values[:n]) / n
    alpha = 2 / (n + 1)
    k = len(values) - n
    return seed * (1 - alpha) ** k + sum(alpha * (1 - alpha) ** (len(values) - 1 - i) * values[i]
                                                         for i in range(n, len(values)))


def reference_wilder(values, n):
    seed = sum(values[:n]) / n
    weight = (n - 1) / n
    return seed * weight ** (len(values) - n) + sum(values[i] / n * weight ** (len(values) - 1 - i)
                                                           for i in range(n, len(values)))


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.closes = [100 + math.sin(i * .7) * 3 + i * .04 for i in range(120)]
        self.rows = bars("5m", self.closes)

    def test_ema_matches_closed_form(self):
        for period in (20, 50):
            self.assertAlmostEqual(indicators.ema(self.closes, period)[-1], reference_ema(self.closes, period), 10)
            self.assertIsNone(indicators.ema(self.closes[:period - 1], period)[-1])

    def test_rsi_matches_independent_geometric_wilder(self):
        changes = [self.closes[i] - self.closes[i - 1] for i in range(1, len(self.closes))]
        gain = reference_wilder([max(0, value) for value in changes], 6)
        loss = reference_wilder([max(0, -value) for value in changes], 6)
        expected = 100 - 100 / (1 + gain / loss)
        self.assertAlmostEqual(indicators.rsi(self.closes)[-1], expected, 10)
        self.assertEqual(indicators.rsi([3.] * 20)[-1], 50)
        self.assertEqual(indicators.rsi(list(range(20)))[-1], 100)
        self.assertEqual(indicators.rsi(list(range(20, 0, -1)))[-1], 0)

    def test_atr_and_adx_independent_reference(self):
        tr, plus, minus = [], [], []
        for previous, current in zip(self.rows, self.rows[1:]):
            high, low = float(current["high"]), float(current["low"])
            previous_close = float(previous["close"])
            tr.append(max(high, previous_close) - min(low, previous_close))
            up, down = high - float(previous["high"]), float(previous["low"]) - low
            plus.append(max(up, 0) if up > down else 0)
            minus.append(max(down, 0) if down > up else 0)
        self.assertAlmostEqual(indicators.atr(self.rows)[-1], reference_wilder(tr, 14), 10)
        dx = []
        for length in range(14, len(tr) + 1):
            pos = reference_wilder(plus[:length], 14)
            neg = reference_wilder(minus[:length], 14)
            dx.append(100 * abs(pos - neg) / (pos + neg) if pos + neg else 0)
        self.assertAlmostEqual(indicators.adx(self.rows)[-1], reference_wilder(dx, 14), 10)
        flat = bars("5m", [100] * 60)
        self.assertEqual(indicators.adx(flat)[-1], 0)
        self.assertAlmostEqual(indicators.atr(flat)[-1], .2)

    def test_rvol_excludes_current_and_vwap_uses_quote_volume(self):
        self.rows[-1]["volume"] = "1000"
        self.rows[-1]["quote_volume"] = "123456"
        self.assertEqual(indicators.rvol(self.rows)[-1], 10)
        expected = sum(Decimal(row["quote_volume"]) for row in self.rows[-20:]) / sum(Decimal(row["volume"]) for row in self.rows[-20:])
        self.assertAlmostEqual(indicators.vwap(self.rows)[-1], float(expected), 10)
        for row in self.rows:
            row["volume"] = "0"
            row["quote_volume"] = "0"
        self.assertIsNone(indicators.rvol(self.rows)[-1])
        self.assertIsNone(indicators.vwap(self.rows)[-1])


class StrategyTests(unittest.TestCase):
    def test_t1_positive_and_false_breakout(self):
        data = positive_fixture()
        result = evaluate("SOLUSDT", data, AS_OF)[0]
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["regime"], "UPTREND")
        self.assertEqual(result["score"], 92.5)
        self.assertEqual(result["score_factors"]["execution"], 7.5)
        data["1m"][-1]["close"] = data["1m"][-2]["close"]
        data["1m"][-1]["low"] = str(Decimal(data["1m"][-1]["close"]) - Decimal("0.1"))
        result = evaluate("SOLUSDT", data, AS_OF)[0]
        self.assertFalse(result["accepted"])
        self.assertIn("ONE_MINUTE_BREAKOUT_UNCONFIRMED", result["reasons"])

    def test_r1_positive_and_continuing_decline(self):
        data = positive_fixture("R1")
        result = evaluate("SOLUSDT", data, AS_OF)[1]
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["regime"], "RANGE")
        self.assertIn("R1_LIVE_PERMANENTLY_DISABLED", result["risk_flags"])
        data["5m"][-1].update(open="96.6", close="96.5", low="96.4", high="96.7")
        result = evaluate("SOLUSDT", data, AS_OF)[1]
        self.assertFalse(result["accepted"])
        self.assertIn("RSI_OVERSOLD_NOT_RECOVERED", result["reasons"])
        self.assertIn("FIVE_MINUTE_FLOOR_UNCONFIRMED", result["reasons"])

    def test_r1_volume_or_rsi_alone_never_passes(self):
        data = positive_fixture("R1")
        data["5m"][-1]["taker_buy_quote_volume"] = "1"
        result = evaluate("SOLUSDT", data, AS_OF)[1]
        self.assertFalse(result["accepted"])
        self.assertIn("TAKER_BUY_NOT_RECOVERED", result["reasons"])

    def test_no_future_or_open_candle_can_change_decision(self):
        data = positive_fixture()
        expected = evaluate("SOLUSDT", data, AS_OF)
        for interval, step in INTERVAL_MS.items():
            future = dict(data[interval][-1])
            future.update(open_time=future["open_time"] + step, close_time=future["close_time"] + step,
                          close="100000")
            data[interval].append(future)
            opened = dict(future, close_time=AS_OF, is_closed=False)
            data[interval].append(opened)
        self.assertEqual(evaluate("SOLUSDT", data, AS_OF), expected)

    def test_gap_duplicate_stale_and_warmup_fail_closed(self):
        for modification in ("gap", "duplicate", "stale", "warmup"):
            with self.subTest(modification=modification):
                data = positive_fixture()
                if modification == "gap":
                    del data["4h"][-10]
                elif modification == "duplicate":
                    data["4h"].append(data["4h"][-1])
                elif modification == "stale":
                    data["4h"].pop()
                else:
                    data["4h"] = data["4h"][-49:]
                self.assertTrue(all(r["regime"] == "DATA_NOT_READY" and not r["accepted"]
                                    for r in evaluate("SOLUSDT", data, AS_OF)))

    def test_symbols_strictly_isolated_and_ids_deterministic(self):
        sol = evaluate("SOLUSDT", positive_fixture(), AS_OF)
        sui = evaluate("SUIUSDT", positive_fixture(symbol="SUIUSDT"), AS_OF)
        self.assertTrue(sui[0]["accepted"])
        self.assertNotEqual(sol[0]["signal_id"], sui[0]["signal_id"])
        self.assertEqual(sol, evaluate("SOLUSDT", positive_fixture(), AS_OF))
        mixed = positive_fixture()
        mixed["1h"][-1]["symbol"] = "SUIUSDT"
        self.assertFalse(evaluate("SOLUSDT", mixed, AS_OF)[0]["accepted"])

    def test_confirmation_must_follow_five_minute_close_and_alignment(self):
        data = positive_fixture()
        before = BOUNDARY - 1
        result = evaluate("SOLUSDT", data, before)[0]
        self.assertFalse(result["accepted"])
        self.assertIn("ONE_MINUTE_BREAKOUT_UNCONFIRMED", result["reasons"])
        selected, _ = prepare("SOLUSDT", data, AS_OF)
        for interval, step in INTERVAL_MS.items():
            self.assertEqual(selected[interval][-1]["close_time"], ((AS_OF + 1) // step) * step - 1)

    def test_score_config_and_hard_gate(self):
        factors = dict.fromkeys(("trend", "momentum", "participation", "structure", "execution"), 1)
        self.assertEqual(score(factors)["score"], 100)
        self.assertEqual(score(dict.fromkeys(factors, .75))["priority"], "STANDARD")
        with self.assertRaises(ValueError):
            ScoreConfig(trend=21)
        data = positive_fixture()
        data["1m"][-2]["low"] = data["1m"][-3]["low"]
        result = evaluate("SOLUSDT", data, AS_OF)[0]
        self.assertGreaterEqual(result["score"], 75)
        self.assertFalse(result["accepted"])


if __name__ == "__main__":
    unittest.main()
