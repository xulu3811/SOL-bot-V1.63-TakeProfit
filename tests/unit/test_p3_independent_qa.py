"""Independent P3 arithmetic and boundary checks authored by the QA reviewer."""

from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest
import json

from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.execution.shadow import ShadowExecutor, ShadowRejected
from solbot.market.trade_collector import BookTicker, TradeWindow
from solbot.mathematics.apts_exit import APTSState, update_protection
from solbot.mathematics.contracts import DecisionContext, Evidence, SymbolRules
from solbot.mathematics.decimal_utils import D, ceil_step, floor_step
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.execution_cost import FeeSchedule, estimate_cost
from solbot.mathematics.exit_pricing import price_exit
from solbot.mathematics.expected_return import estimate_return
from solbot.mathematics.fill_probability import estimate_fill
from solbot.mathematics.microstructure import depth_imbalance, microprice, spread_metrics
from solbot.mathematics.market_features import input_hash as micro_input_hash
from solbot.mathematics.orderbook import OrderBook, SequenceGap
from solbot.mathematics.position_sizing import size_position
from solbot.mathematics.protective_stop import initial_stop, profit_floor
from solbot.mathematics.trend_exhaustion import TrendEvidence, classify
from solbot.mathematics.volatility_regime import decimal_atr, trailing_distance
from solbot.p3_runtime import refresh_features, scheduler_status
from solbot.mathematics.decimal_utils import json_safe


NOW = 1_800_000_000_000


def context(offset=0, *, stale=False):
    observed = NOW + offset
    return DecisionContext("SOLUSDT", "SOL-shadow", "T1", "qa-1", "independent-signal",
                           observed, observed - 10_000 if stale else observed,
                           observed + 5000, "a" * 64)


def strong_evidence():
    return TrendEvidence(ready=True, trend_4h=True, trend_1h=True, trend_15m=True,
                         higher_low_1m=True, higher_low_5m=True, rvol="2.5",
                         buy_ratio="0.7", previous_buy_ratio="0.65", atr="0.6")


def exchange_rules():
    return {"symbol": "SOLUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
            "filters": [{"filterType": "PRICE_FILTER", "minPrice": "0.01",
                         "maxPrice": "100000", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "minQty": "0.01",
                         "maxQty": "100000", "stepSize": "0.01"},
                        {"filterType": "MIN_NOTIONAL", "minNotional": "1"}]}


def public_features(offset=0, *, bid="99.9", ask="100", bid_quantity="100", ask_quantity="100"):
    return {"symbol": "SOLUSDT", "ready": True, "synced": True,
            "exchange_time_ms": NOW + offset, "received_time_ms": NOW + offset,
            "update_id": 100 + offset, "bid": bid, "ask": ask,
            "bid_qty": bid_quantity, "ask_qty": ask_quantity,
            "bids": [[bid, bid_quantity]], "asks": [[ask, ask_quantity]],
            "buy_ratio": "0.6", "trade_volume": "100", "short_volatility": "0.001",
            "trade_event_time_ms": NOW + offset, "trade_received_time_ms": NOW + offset,
            "trade_window_complete": True, "trade_window_ms": 60000, "rules": exchange_rules()}


def candidate(signal="qa-entry"):
    return {"symbol": "SOLUSDT", "strategy_id": "T1", "signal_id": signal,
            "signal_time": NOW, "expires_at": NOW + 300000, "accepted": True,
            "reasons": [], "risk_flags": [], "entry_low": "99", "entry_high": "100"}


class IndependentDecimalTests(unittest.TestCase):
    def test_non_power_of_ten_increments_match_integer_reference(self):
        cases = (("101.237", "0.05"), ("0.12345678", "0.00025"),
                 ("999.999999", "5"), ("1.000000000000000001", "0.000000000000000003"))
        with localcontext() as context:
            context.prec = 70
            for text, increment in cases:
                value, step = Decimal(text), Decimal(increment)
                numerator, denominator = value.as_integer_ratio()
                step_numerator, step_denominator = step.as_integer_ratio()
                units_numerator = numerator * step_denominator
                units_denominator = denominator * step_numerator
                lower_units = units_numerator // units_denominator
                upper_units = -(-units_numerator // units_denominator)
                with self.subTest(value=text, step=increment):
                    self.assertEqual(floor_step(value, step), lower_units * step)
                    self.assertEqual(ceil_step(value, step), upper_units * step)
                    self.assertLessEqual(floor_step(value, step), value)
                    self.assertGreaterEqual(ceil_step(value, step), value)

    def test_float_boolean_and_nonfinite_money_inputs_are_rejected(self):
        for value in (0.1, True, False, "NaN", "Infinity", "-Infinity", "1e25"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                D(value)
        self.assertEqual(D("0.10000000000000000001"), Decimal("0.10000000000000000001"))


class IndependentPublicDataTests(unittest.TestCase):
    def test_microprice_spread_and_imbalance_match_hand_calculation(self):
        self.assertEqual(microprice("100", "101", "3", "1"), Decimal("100.75"))
        metrics = spread_metrics("100", "101")
        self.assertEqual(metrics["spread"], Decimal(1))
        self.assertEqual(metrics["midpoint"], Decimal("100.5"))
        self.assertLess(abs(metrics["spread_bps"] - Decimal(20000) / 201), Decimal("1e-25"))
        self.assertEqual(depth_imbalance([("100", "3")], [("101", "1")]), Decimal("0.5"))
        for args in (("100", "101", "0", "0"), ("101", "100", "1", "1"),
                     ("100", "101", "NaN", "1")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                microprice(*args)

    def test_depth_requires_bridge_and_invalidates_on_true_gap(self):
        book = OrderBook("SOLUSDT")
        book.load_snapshot({"lastUpdateId": 100,
                            "bids": [[str(Decimal(100) - Decimal("0.01") * i), "3"] for i in range(20)],
                            "asks": [[str(Decimal(101) + Decimal("0.01") * i), "1"] for i in range(20)]}, NOW)
        self.assertFalse(book.synced)
        event = {"e": "depthUpdate", "s": "SOLUSDT", "U": 99, "u": 101,
                 "E": NOW + 1, "b": [["100", "4"]], "a": []}
        self.assertTrue(book.apply(event, NOW + 2))
        self.assertTrue(book.synced)
        self.assertEqual(book.levels(1)[0][0], (Decimal(100), Decimal(4)))
        self.assertFalse(book.apply(event, NOW + 3))
        self.assertEqual(book.received_time_ms, NOW + 2)
        with self.assertRaises(SequenceGap):
            book.apply({**event, "U": 103, "u": 103, "E": NOW + 4}, NOW + 5)
        self.assertFalse(book.synced)
        self.assertIsNone(book.update_id)

    def test_book_ticker_keeps_missing_exchange_timestamp_unknown(self):
        ticker = BookTicker("SOLUSDT")
        ticker.add({"s": "SOLUSDT", "u": 100, "b": "100", "a": "101",
                    "B": "3", "A": "1"}, NOW)
        self.assertIsNone(ticker.value["exchange_time_ms"])
        self.assertEqual(ticker.value["received_time_ms"], NOW)

    def test_regressed_trade_time_cannot_add_expired_volume(self):
        window = TradeWindow("SOLUSDT", window_ms=1000)
        trade = {"e": "aggTrade", "s": "SOLUSDT", "a": 10, "E": NOW - 900,
                 "T": NOW - 900, "p": "100", "q": "1", "m": False, "f": 1, "l": 1}
        window.add(trade, NOW - 900)
        with self.assertRaises(ValueError):
            window.add({**trade, "a": 11, "E": NOW - 800, "T": NOW - 1500,
                        "q": "1000", "f": 2, "l": 2}, NOW - 800)
        self.assertEqual(Decimal(window.features(NOW)["trade_volume"]), Decimal(1))


class IndependentCostTests(unittest.TestCase):
    def test_fee_and_expected_return_reference_preserve_evidence_qualification(self):
        cost = estimate_cost(context(), entry_price="100", exit_price="110", quantity="3")
        self.assertEqual(cost.entry_fee, Decimal("0.3"))
        self.assertEqual(cost.exit_fee, Decimal("0.33"))
        self.assertEqual(cost.total_cost, Decimal("1.26"))
        result = estimate_return(context(), notional="300", profit_fraction="0.1",
                                 loss_fraction="0.008", win_probability="0.6", cost=cost)
        self.assertEqual(result.expected_return_fraction, Decimal("0.0526"))
        self.assertEqual(result.expected_net_quote, Decimal("15.78"))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result.live_authorized)
        self.assertEqual(FeeSchedule(bnb_discount_enabled=True).rate("MAKER"), Decimal("0.00075"))

    def test_fill_outcomes_sum_to_one_and_touch_never_becomes_certain(self):
        result = estimate_fill(context(), public_features(), limit_price="100", quantity="3", wait_ms=1000)
        outcomes = [getattr(result, name) for name in
                    ("no_fill", "partial_fill", "full_fill", "timeout", "cancelled", "adverse_fill")]
        self.assertTrue(all(0 <= value <= 1 for value in outcomes))
        self.assertEqual(sum(outcomes, Decimal(0)), Decimal(1))
        self.assertLess(result.partial_fill + result.full_fill + result.adverse_fill, Decimal(1))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result.live_authorized)

    def test_dynamic_size_matches_score_risk_and_cash_reference(self):
        common = dict(entry_price="100", equity="2000", high_watermark="2000", cash_balance="2000")
        minimum = size_position(context(), public_features(), score="75", **common)
        self.assertEqual(minimum.notional, Decimal(300))
        middle = size_position(context(), public_features(), score="87.5", **common)
        self.assertEqual(middle.notional, Decimal(500))
        risk_limited = size_position(context(), public_features(), score="100", **common)
        self.assertEqual(risk_limited.notional, Decimal(666))
        self.assertLessEqual(risk_limited.risk_estimate, Decimal(8))
        self.assertFalse(risk_limited.live_authorized)
        r1 = size_position(replace(context(), strategy_id="R1"), public_features(), score="100", **common)
        self.assertEqual(r1.notional, Decimal(416))
        self.assertLessEqual(r1.risk_estimate, Decimal(5))

    def test_size_rejects_exact_drawdown_slots_and_fee_shortfall(self):
        common = dict(score="100", entry_price="100", equity="2000", high_watermark="2000", cash_balance="2000")
        for changes, reason in (({"equity": "1900"}, "HARD_DRAWDOWN_LOCK"),
                                ({"open_positions": 1, "pending_entries": 1}, "TWO_SLOTS_OCCUPIED"),
                                ({"cash_balance": "300"}, "ALLOWED_NOTIONAL_BELOW_300")):
            with self.subTest(changes=changes):
                result = size_position(context(), public_features(), **{**common, **changes})
                self.assertEqual(result.quantity, Decimal(0))
                self.assertIn(reason, result.reasons)

    def test_entry_rounding_never_breaks_price_ceiling_even_with_positive_scenario(self):
        # Synthetic evidence activates the mathematical candidate branch only.
        # It provides no empirical strategy or execution validation.
        proof = Evidence("SOLUSDT", "SOL-shadow", 200, True, "b" * 64, independently_reviewed=True)
        cost = estimate_cost(context(), entry_price="100", exit_price="105", quantity="5", evidence=proof)
        expected = estimate_return(context(), notional="500", profit_fraction="0.05",
                                   loss_fraction="0.008", win_probability="0.7", cost=cost, win_evidence=proof)
        valid = price_entry(context(), public_features(), signal_max_price="100", notional="500",
                            atr="0.2", expected=expected, cost=cost, mode="MARKETABLE_LIMIT")
        self.assertEqual(valid.mode, "MARKETABLE_LIMIT")
        self.assertLessEqual(valid.limit_price, valid.max_buy_price)
        self.assertEqual(valid.limit_price % Decimal("0.01"), Decimal(0))
        rejected = price_entry(context(), public_features(ask="100.005"), signal_max_price="100.005",
                               notional="500", atr="0.2", expected=expected, cost=cost,
                               mode="MARKETABLE_LIMIT")
        self.assertEqual(rejected.mode, "REJECT")
        self.assertEqual(rejected.quantity, Decimal(0))
        self.assertIsNone(rejected.limit_price)
        self.assertIn("ASK_EXCEEDS_MAX_BUY_PRICE", rejected.reasons)
        self.assertFalse(valid.live_authorized)

    def test_exit_preserves_minimum_sell_price_when_bid_is_below_it(self):
        result = price_exit(context(), public_features(), quantity="3", min_sell_price="100",
                            mode="MARKETABLE_LIMIT")
        self.assertEqual(result.mode, "REJECT")
        self.assertIsNone(result.limit_price)
        self.assertEqual(result.quantity, Decimal(0))
        self.assertIn("BID_BELOW_SELL_PROTECTION", result.reasons)


class IndependentAPTSMathTests(unittest.TestCase):
    def state(self):
        return APTSState.create(context(), "100", "3", "0.01")

    def test_decimal_atr_matches_closed_form_wilder_and_distance_bounds(self):
        rows = []
        for index in range(24):
            close = Decimal(100) + Decimal(index) / 10
            rows.append({"close": str(close), "high": str(close + Decimal(3 + index % 3) / 10),
                         "low": str(close - Decimal(2 + index % 5) / 20)})
        ranges = [max(Decimal(current["high"]), Decimal(previous["close"]))
                  - min(Decimal(current["low"]), Decimal(previous["close"]))
                  for previous, current in zip(rows, rows[1:])]
        with localcontext() as precision:
            precision.prec = 60
            weight = Decimal(13) / 14
            expected = sum(ranges[:14], Decimal(0)) / 14 * weight ** (len(ranges) - 14)
            expected += sum((value / 14 * weight ** (len(ranges) - 1 - index)
                             for index, value in enumerate(ranges) if index >= 14), Decimal(0))
        self.assertLess(abs(decimal_atr(rows) - expected), Decimal("1e-25"))
        self.assertEqual(trailing_distance("NORMAL_UPTREND", "1", "100"), Decimal("0.005"))
        self.assertEqual(trailing_distance("STRONG_BREAKOUT", "0.1", "100"), Decimal("0.008"))
        self.assertEqual(trailing_distance("STRONG_BREAKOUT", "2", "100"), Decimal("0.012"))
        self.assertEqual(trailing_distance("WEAKENING", "1", "100"), Decimal("0.004"))
        self.assertEqual(trailing_distance("NORMAL_UPTREND", "0.3", "100", scheme="ATR"), Decimal("0.006"))

    def test_initial_stop_and_cost_floor_are_rounded_toward_protection(self):
        self.assertEqual(initial_stop("101.237", "0.05"), Decimal("100.45"))
        self.assertGreaterEqual(initial_stop("101.237", "0.05"), Decimal("101.237") * Decimal("0.992"))
        with self.assertRaises(ValueError):
            initial_stop("100", "5")
        floor = profit_floor("100", "0.001", "0.001", "0.002", "0.01")
        self.assertEqual(floor, Decimal("100.42"))
        self.assertGreater(floor * Decimal("0.999") * Decimal("0.998"), Decimal("100.1"))

    def test_one_percent_and_strict_one_point_five_percent_stages(self):
        state = self.state()
        ordinary = TrendEvidence(ready=True)
        below = update_protection(state, context(1), "100.999", ordinary, tick_size="0.01")
        self.assertEqual(below.state.stage, "S0_INITIAL_PROTECTION")
        protected = update_protection(below.state, context(2), "101", ordinary, tick_size="0.01")
        self.assertEqual(protected.state.stage, "S1_PROFIT_PROTECTION")
        exact = update_protection(protected.state, context(3), "101.5", ordinary, tick_size="0.01")
        self.assertEqual(exact.state.stage, "S1_PROFIT_PROTECTION")
        over = update_protection(exact.state, context(4), "101.51", ordinary, tick_size="0.01")
        self.assertEqual(over.state.stage, "S2_ADAPTIVE_TRAILING")
        self.assertGreaterEqual(over.stop_price, exact.stop_price)
        self.assertFalse(over.live_authorized)

    def test_strong_breakout_tolerates_point_six_percent_without_lowering_stop(self):
        first = update_protection(self.state(), context(1), "102", strong_evidence(), tick_size="0.01")
        self.assertEqual(first.market_regime, "STRONG_BREAKOUT")
        second = update_protection(first.state, context(2), "101.388", strong_evidence(), tick_size="0.01")
        self.assertFalse(second.exit_required)
        self.assertGreaterEqual(second.stop_price, first.stop_price)
        third = update_protection(second.state, context(3), "104", strong_evidence(), tick_size="0.01")
        self.assertGreaterEqual(third.stop_price, second.stop_price)

    def test_trigger_precedes_widening_and_remains_latched_after_recovery(self):
        ordinary = TrendEvidence(ready=True)
        first = update_protection(self.state(), context(1), "102", ordinary, tick_size="0.01")
        self.assertEqual(first.stop_price, Decimal("101.49"))
        stopped = update_protection(first.state, context(2), "101.388", strong_evidence(), tick_size="0.01")
        self.assertTrue(stopped.exit_required)
        recovered = update_protection(stopped.state, context(3), "104", strong_evidence(), tick_size="0.01")
        self.assertTrue(recovered.exit_required)
        self.assertEqual(recovered.stop_price, first.stop_price)
        restored = APTSState.from_dict(recovered.state.asdict())
        self.assertTrue(restored.trigger_latched)
        self.assertEqual(restored.stop_price, first.stop_price)

    def test_stale_data_preserves_stop_and_fresh_stop_works_without_trend(self):
        state = self.state()
        stale = update_protection(state, context(1, stale=True), "90", TrendEvidence(), tick_size="0.01")
        self.assertEqual(stale.status, "DATA_UNAVAILABLE")
        self.assertEqual(stale.stop_price, state.stop_price)
        self.assertFalse(stale.exit_required)
        fresh = update_protection(stale.state, context(2), "90", TrendEvidence(), tick_size="0.01")
        self.assertTrue(fresh.exit_required)
        self.assertEqual(fresh.stop_price, state.stop_price)
        self.assertGreater(fresh.limit_price, Decimal(90))

    def test_volume_alone_cannot_assert_strong_trend_and_inputs_change_audit_hash(self):
        self.assertNotEqual(classify(TrendEvidence(ready=True, rvol="100")), "STRONG_BREAKOUT")
        first = update_protection(self.state(), context(1), "101", TrendEvidence(ready=True), tick_size="0.01")
        changed = update_protection(self.state(), context(1), "101.1", TrendEvidence(ready=True), tick_size="0.01")
        self.assertNotEqual(first.context.input_hash, changed.context.input_hash)


class IndependentRuntimeTests(unittest.TestCase):
    def source(self):
        return dict(public_features(), book_ticker_received_time_ms=NOW,
                    book_ticker_exchange_time_ms=None, reasons=[])

    def test_consumer_expiration_cannot_trust_persisted_ready(self):
        source = self.source()
        self.assertTrue(refresh_features(source, NOW)["ready"])
        expired = refresh_features(source, NOW + 2001)
        self.assertFalse(expired["ready"])
        self.assertTrue(expired["synced"])
        self.assertTrue(source["ready"])
        self.assertEqual(source["reasons"], [])
        self.assertIn("CONSUMER_STALE_EXCHANGE_TIME_MS", expired["reasons"])

    def test_consumer_rechecks_flow_and_ticker_separately_without_faking_event_time(self):
        source = self.source()
        for field, age in (("trade_event_time_ms", 15001), ("trade_received_time_ms", 15001),
                           ("book_ticker_received_time_ms", 5001)):
            with self.subTest(field=field):
                value = refresh_features(dict(source, **{field: NOW - age}), NOW)
                self.assertFalse(value["ready"])
                self.assertTrue(value["synced"])
        self.assertIsNone(refresh_features(source, NOW)["book_ticker_exchange_time_ms"])
        self.assertFalse(refresh_features(dict(source, book_ticker_exchange_time_ms=NOW + 1), NOW)["ready"])
        self.assertFalse(refresh_features(dict(source, received_time_ms=NOW + 1), NOW)["ready"])

    def test_consumer_preserves_hash_verifiable_original_source(self):
        source = self.source()
        source["input_hash"] = micro_input_hash(source)
        expired = refresh_features(source, NOW + 2001)
        self.assertFalse(expired["ready"])
        original = expired["source_snapshot"]
        self.assertEqual(original, source)
        self.assertTrue(original["ready"])
        self.assertEqual(micro_input_hash(original), source["input_hash"])
        self.assertIsNone(original["book_ticker_exchange_time_ms"])

    def test_real_scheduler_minute_report_can_be_written_without_changing_original(self):
        from test_p2_strategy import AS_OF
        from test_p3_real_replay import market_fixture
        from solbot.strategy.scheduler import Scheduler, StrategyStore
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            market_fixture(root / "market.sqlite", "SOLUSDT")
            store = StrategyStore(root / "strategy.sqlite")
            try:
                report = Scheduler(root / "market.sqlite", store, "SOLUSDT").tick(now_ms=AS_OF + 1)
                self.assertGreater(report["processed"], 0)
                original = json.dumps(report, sort_keys=True, allow_nan=False)
                status = {"scheduler": scheduler_status(report), "cash": "2000.00"}
                encoded = json.dumps(json_safe(status), allow_nan=False)
                self.assertEqual(json.loads(encoded)["cash"], "2000.00")
                self.assertEqual(json.dumps(report, sort_keys=True, allow_nan=False), original)
                self.assertTrue(any(isinstance(value.get("score"), float)
                                    for value in report["samples"].values()))
            finally:
                store.close()


class IndependentShadowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "shadow.sqlite"
        self.rules = SymbolRules.from_exchange_info(exchange_rules(), "SOLUSDT")

    def ledger(self):
        return P3ShadowExecutor(self.path, rules=self.rules, slippage_rate="0")

    def test_partial_protective_exit_preserves_quantity_and_cash_without_duplicate_fill(self):
        ledger = self.ledger()
        ledger.submit_candidate(candidate(), now_ms=NOW)
        ordinary = TrendEvidence(ready=True)
        ledger.process_market(public_features(1), ordinary, now_ms=NOW + 1)
        ledger.process_market(public_features(2, bid="102", ask="102.1"), ordinary, now_ms=NOW + 2)
        ledger.process_market(public_features(3, bid="101.4", ask="101.5"), ordinary, now_ms=NOW + 3)
        protection = ledger.snapshot()["pending_orders"][0]
        self.assertEqual(protection["side"], "SELL")
        with self.assertRaises(ShadowRejected):
            ledger.cancel(protection["order_id"])
        partial_quote = public_features(4, bid="101.3", ask="101.4", bid_quantity="5")
        result = ledger.process_market(partial_quote, ordinary, now_ms=NOW + 4)
        self.assertEqual(Decimal(result["fills"][0]["quantity"]), Decimal("0.5"))
        snapshot = ledger.snapshot()
        self.assertEqual(Decimal(snapshot["positions"][0]["quantity"]), Decimal("2.5"))
        self.assertEqual(Decimal(snapshot["pending_orders"][0]["executed_quantity"]), Decimal("0.5"))
        self.assertFalse(ledger.process_market(partial_quote, ordinary, now_ms=NOW + 4)["processed"])
        self.assertEqual(ledger.snapshot()["cash"], snapshot["cash"])
        final = ledger.process_market(public_features(5, bid="101.3", ask="101.4", bid_quantity="25"),
                                      ordinary, now_ms=NOW + 5)
        self.assertEqual(Decimal(final["fills"][0]["quantity"]), Decimal("2.5"))
        self.assertEqual(ledger.snapshot()["positions"], [])
        self.assertEqual(ledger.snapshot()["pending_orders"], [])
        self.assertEqual(Decimal(ledger.snapshot()["cash"]), Decimal("2003.2961"))
        self.assertEqual(Decimal(ledger.positions()[0]["realized_pnl"]), Decimal("3.2961"))

    def test_hard_lock_alone_cancels_buys_and_retains_existing_position(self):
        ledger = self.ledger()
        ordinary = TrendEvidence(ready=True)
        ledger.submit_candidate(candidate(), now_ms=NOW)
        ledger.process_market(public_features(1), ordinary, now_ms=NOW + 1)
        ledger.submit_candidate(candidate("qa-second"), now_ms=NOW + 2)
        # Isolate the account lock from a price-stop trigger using a persisted
        # prior equity peak, as can occur after earlier account activity.
        with ledger._transaction() as connection:
            connection.execute("UPDATE shadow_account SET peak_equity='2200'")
        ledger.process_market(public_features(3), ordinary, now_ms=NOW + 3)
        snapshot = ledger.snapshot()
        self.assertTrue(snapshot["hard_locked"])
        self.assertEqual(len(snapshot["positions"]), 1)
        self.assertEqual(Decimal(snapshot["positions"][0]["quantity"]), Decimal(3))
        self.assertEqual(snapshot["pending_orders"], [])
        self.assertEqual(Decimal(snapshot["reserved"]), Decimal(0))
        self.assertTrue(all(order["side"] == "BUY" for order in ledger.orders()))
        self.assertEqual(snapshot["risk_policy"], "BLOCK_ENTRIES_KEEP_PROTECTION_V1")
        self.assertTrue(self.ledger().snapshot()["hard_locked"])

    def test_entry_rejects_off_tick_limit_without_reserving_cash(self):
        ledger = self.ledger()
        invalid = {**candidate(), "entry_high": "100.005"}
        with self.assertRaises((ValueError, ShadowRejected)):
            ledger.submit_candidate(invalid, notional="700", now_ms=NOW)
        self.assertEqual(ledger.orders(), [])
        self.assertEqual(Decimal(ledger.snapshot()["cash"]), Decimal(2000))
        self.assertEqual(Decimal(ledger.snapshot()["reserved"]), Decimal(0))

    def test_migration_uses_actual_historical_fill_notional(self):
        old = ShadowExecutor(self.path)
        old.submit_candidate(candidate(), notional="700", now_ms=NOW)
        old.process_quote("SOLUSDT", "98.9", "99", NOW + 1, now_ms=NOW + 1)
        cash = old.snapshot()["cash"]
        upgraded = self.ledger()
        order = upgraded.orders()[0]
        self.assertEqual(Decimal(order["executed_notional"]), Decimal(693))
        self.assertEqual(Decimal(order["executed_fee"]), Decimal("0.693"))
        self.assertEqual(upgraded.snapshot()["cash"], cash)
        self.assertEqual(Decimal(upgraded.snapshot()["positions"][0]["quantity"]), Decimal(7))
        with self.assertRaises(ValueError):
            ShadowExecutor(self.path)

    def test_fresh_depth_keeps_protection_during_flow_warmup(self):
        ledger = self.ledger()
        ordinary = TrendEvidence(ready=True)
        ledger.submit_candidate(candidate(), now_ms=NOW)
        ledger.process_market(public_features(1), ordinary, now_ms=NOW + 1)
        ledger.submit_candidate(candidate("qa-blocked-during-warmup"), now_ms=NOW + 2)
        warmup = dict(public_features(3, bid="99.1", ask="99.2"), ready=False,
                      trade_window_complete=False, short_volatility=None,
                      reasons=["TRADE_WINDOW_INCOMPLETE"])
        trigger = ledger.process_market(warmup, TrendEvidence(), now_ms=NOW + 3)
        self.assertTrue(trigger["processed"])
        self.assertEqual(trigger["fills"], [])
        self.assertTrue(ledger.snapshot()["protection"][0]["state"]["trigger_latched"])
        next_depth = dict(warmup, exchange_time_ms=NOW + 4, received_time_ms=NOW + 4,
                          update_id=104)
        exited = ledger.process_market(next_depth, TrendEvidence(), now_ms=NOW + 4)
        self.assertEqual([row["side"] for row in exited["fills"]], ["SELL"])
        self.assertEqual(ledger.snapshot()["positions"], [])
        self.assertEqual(len(ledger.snapshot()["pending_orders"]), 1)
        self.assertEqual(ledger.snapshot()["pending_orders"][0]["side"], "BUY")

    def test_explicit_sell_atomically_cancels_partial_entry_remainder(self):
        ledger = self.ledger()
        ledger.submit_candidate(candidate(), now_ms=NOW)
        ledger.process_market(public_features(1, ask_quantity="10"), TrendEvidence(ready=True), now_ms=NOW + 1)
        before = ledger.snapshot()
        self.assertEqual(Decimal(before["positions"][0]["quantity"]), Decimal(1))
        order = ledger.submit_sell(before["positions"][0]["position_id"], "99.9",
                                   now_ms=NOW + 2, request_id="qa-manual-exit")
        after = ledger.snapshot()
        self.assertEqual(Decimal(order["quantity"]), Decimal(1))
        self.assertEqual(Decimal(after["reserved"]), Decimal(0))
        self.assertEqual([row["side"] for row in after["pending_orders"]], ["SELL"])
        ledger.process_market(public_features(3), TrendEvidence(ready=True), now_ms=NOW + 3)
        self.assertEqual(ledger.snapshot()["positions"], [])
        self.assertEqual(ledger.snapshot()["fill_count"], 2)

    def test_already_open_legacy_writer_cannot_modify_migrated_ledger(self):
        legacy = ShadowExecutor(self.path)
        upgraded = self.ledger()
        before = upgraded.snapshot()
        with self.assertRaises(ValueError):
            legacy.submit_candidate(candidate(), now_ms=NOW)
        self.assertEqual(upgraded.snapshot(), before)

    def test_displayed_depth_is_shared_across_orders_and_limit_is_binding(self):
        ledger = P3ShadowExecutor(self.path, rules=self.rules)
        ledger.submit_candidate(candidate("qa-shared-a"), now_ms=NOW)
        ledger.submit_candidate(candidate("qa-shared-b"), now_ms=NOW)
        # An ask touch is insufficient once the modeled slip would cross limit.
        denied = ledger.process_market(public_features(1, ask_quantity="30"), TrendEvidence(ready=True), now_ms=NOW + 1)
        self.assertEqual(denied["fills"], [])
        self.assertEqual(ledger.snapshot()["positions"], [])
        # 30 displayed units give 3 units total, shared by both orders.
        result = ledger.process_market(public_features(2, ask="99.95", ask_quantity="30"), TrendEvidence(ready=True), now_ms=NOW + 2)
        self.assertEqual(sum((Decimal(r["quantity"]) for r in result["fills"]), Decimal(0)), Decimal(3))
        self.assertTrue(all(Decimal(r["price"]) <= Decimal(100) for r in result["fills"]))
        self.assertEqual(len(ledger.snapshot()["pending_orders"]), 1)
        self.assertEqual(Decimal(ledger.snapshot()["reserved"]), Decimal("300.3"))

    def test_migration_failure_rolls_back_extension_schema_and_owner(self):
        legacy = ShadowExecutor(self.path)
        legacy.submit_candidate(candidate(), now_ms=NOW)
        legacy.process_quote("SOLUSDT", "99.9", "100", NOW + 1, now_ms=NOW + 1)
        # Inject damaged historical data to force failure after ALTER TABLE.
        with legacy._transaction() as db:
            db.execute("UPDATE shadow_orders SET fill_price='invalid' WHERE status='FILLED'")
        before = legacy.snapshot()
        with self.assertRaises(ValueError):
            self.ledger()
        with legacy._transaction() as db:
            self.assertEqual(db.execute("SELECT kind FROM execution_owner").fetchone()[0], "SHADOW")
            self.assertNotIn("executed_quantity", [row[1] for row in db.execute("PRAGMA table_info(shadow_orders)")])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='shadow_protection'").fetchone())
        self.assertEqual(legacy.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
