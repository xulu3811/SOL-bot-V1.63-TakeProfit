"""P3-B independent Decimal references; supplied evidence is synthetic test data."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
import json
import unittest

from solbot.mathematics.contracts import DecisionContext, Evidence, SymbolRules, make_context
from solbot.mathematics.decimal_utils import D, floor_step, ceil_step, canonical_hash
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.exit_pricing import price_exit
from solbot.mathematics.execution_cost import FeeSchedule, estimate_cost
from solbot.mathematics.expected_return import estimate_return
from solbot.mathematics.fill_probability import estimate_fill, FillObservation, calibration_report
from solbot.mathematics.position_sizing import size_position

NOW = 1_800_000_000_000


def features(symbol="SOLUSDT"):
    return {"symbol": symbol, "ready": True, "synced": True, "reasons": [],
            "exchange_time_ms": NOW - 20, "received_time_ms": NOW - 10, "update_id": 42,
            "bid": "100", "ask": "100.04", "bid_qty": "30", "ask_qty": "10",
            "bids": [["100", "30"]], "asks": [["100.04", "10"]], "microprice": "100.03",
            "spread": "0.04", "spread_bps": "3.9992", "imbalance": "0.5",
            "buy_ratio": "0.7", "trade_volume": "100", "trade_count": 50,
            "trade_event_time_ms": NOW - 30, "trade_received_time_ms": NOW - 10,
            "quote_updates_per_sec": "2", "short_volatility": "0.001",
            "book_ticker_exchange_time_ms": None, "book_ticker_received_time_ms": NOW - 10,
            "rules": {"symbol": symbol, "status": "TRADING", "isSpotTradingAllowed": True,
                      "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "100000"},
                                  {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100000"},
                                  {"filterType": "MIN_NOTIONAL", "minNotional": "5"}]},
            "input_hash": "a" * 64}


def context(data=None, strategy="T1"):
    data = features() if data is None else data
    return make_context(symbol=data["symbol"], account_id="SOL-shadow" if data["symbol"] == "SOLUSDT" else "SUI-live",
                        strategy_id=strategy, strategy_version="p2-research-1", signal_id="fixture-signal-1",
                        decision_time_ms=NOW, features=data, inputs={}, parameter_version="test-1")


def evidence(ctx):
    return Evidence(ctx.symbol, ctx.account_id, 200, True, "b" * 64, independently_reviewed=True)


def scenario(ctx, amount="500", reliable=True):
    sample = evidence(ctx) if reliable else None
    cost = estimate_cost(ctx, entry_price="100", exit_price="102", quantity=D(amount) / 100,
                         evidence=sample)
    expectation = estimate_return(ctx, notional=amount, profit_fraction="0.02", loss_fraction="0.008",
                                  win_probability="0.7", cost=cost, win_evidence=sample)
    return cost, expectation


class DecimalContractTests(unittest.TestCase):
    def test_increment_rounding_independent_integer_reference(self):
        value, step = Decimal("12.34567"), Decimal("0.005")
        self.assertEqual(floor_step(value, step), Decimal(2469) * step)
        self.assertEqual(ceil_step(value, step), Decimal(2470) * step)
        for invalid in (float("nan"), 0.1, "Infinity", "NaN", True):
            with self.assertRaises(ValueError):
                D(invalid)
        with self.assertRaises(ValueError):
            floor_step("1", "0")

    def test_context_identity_hash_and_live_denial_immutable(self):
        data = features()
        a, b = context(data), context(deepcopy(data))
        self.assertEqual(a, b)
        data["bid_qty"] = "29"
        self.assertNotEqual(a.input_hash, context(data).input_hash)
        with self.assertRaises(ValueError):
            replace(a, account_id="SUI-live")
        cost, expected = scenario(a)
        self.assertFalse(cost.live_authorized)
        with self.assertRaises(FrozenInstanceError):
            cost.live_authorized = True
        json.dumps(cost.asdict(), allow_nan=False)
        self.assertNotEqual(cost.context.input_hash, a.input_hash)

    def test_exchange_static_filters_and_dynamic_reference_fail_closed(self):
        data = features()
        rules = SymbolRules.from_exchange_info(data["rules"])
        self.assertEqual(rules.validate_order("100.00", "3.000"), ())
        self.assertIn("PRICE_FILTER", rules.validate_order("100.001", "3"))
        self.assertIn("LOT_SIZE", rules.validate_order("100", "3.0001"))
        self.assertIn("NOTIONAL_FILTER", rules.validate_order("1", "1"))
        data["rules"]["filters"].append({"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierDown": "0.9",
              "bidMultiplierUp": "1.1", "askMultiplierDown": "0.8", "askMultiplierUp": "1.2", "avgPriceMins": 5})
        rules = SymbolRules.from_exchange_info(data["rules"])
        self.assertIn("PERCENT_PRICE_REFERENCE_MISSING", rules.validate_order("100", "3"))
        self.assertEqual(rules.validate_order("100", "3", reference_price="100", reference_minutes=5), ())
        self.assertIn("PERCENT_PRICE_FILTER", rules.validate_order("111", "3", reference_price="100", reference_minutes=5))
        self.assertEqual(rules.validate_order("111", "3", "SELL", reference_price="100", reference_minutes=5), ())


class CostAndReturnTests(unittest.TestCase):
    def test_fee_slippage_decimal_reference_with_bnb_scenario(self):
        ctx = context()
        fees = FeeSchedule(maker=Decimal("0.0008"), taker=Decimal("0.0012"), bnb_discount_enabled=True)
        result = estimate_cost(ctx, entry_price="100", exit_price="110", quantity="4",
                               entry_liquidity="MAKER", exit_liquidity="TAKER", fees=fees,
                               entry_slippage_bps="2", exit_slippage_bps="4", uncertainty_bps="3")
        reference = Decimal("400") * Decimal("0.0008") * Decimal("0.75") + Decimal("440") * Decimal("0.0012") * Decimal("0.75")
        reference += Decimal("400") * Decimal("0.0002") + Decimal("440") * Decimal("0.0004") + Decimal("400") * Decimal("0.0003")
        self.assertEqual(result.total_cost, reference)
        self.assertEqual(result.cost_fraction, reference / Decimal("400"))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result.evidence_sufficient)

    def test_expected_net_matches_independent_decimal_formula(self):
        ctx = context()
        cost, result = scenario(ctx)
        reference = Decimal("0.7") * Decimal("0.02") - Decimal("0.3") * Decimal("0.008") - cost.total_cost / Decimal("500")
        self.assertEqual(result.expected_return_fraction, reference)
        self.assertEqual(result.expected_net_quote, reference * Decimal("500"))
        self.assertEqual(result.status, "RESEARCH_POSITIVE")
        self.assertFalse(result.live_authorized)

    def test_missing_or_small_or_cross_symbol_evidence_is_insufficient(self):
        ctx = context()
        cost, result = scenario(ctx, reliable=False)
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertIsNotNone(result.expected_net_quote)  # explicit scenario only
        missing = estimate_return(ctx, notional="500", profit_fraction="0.02", loss_fraction="0.008")
        self.assertIsNone(missing.expected_net_quote)
        self.assertEqual(missing.status, "INSUFFICIENT_EVIDENCE")
        wrong = Evidence("SUIUSDT", "SUI-live", 1000, True, "b" * 64)
        self.assertFalse(wrong.sufficient(ctx))
        self.assertFalse(replace(evidence(ctx), sample_count=99).sufficient(ctx))
        self.assertFalse(replace(evidence(ctx), out_of_sample=False).sufficient(ctx))
        self.assertFalse(replace(evidence(ctx), independently_reviewed=False).sufficient(ctx))

    def test_cost_size_and_cross_account_cannot_silently_transfer(self):
        ctx = context()
        cost, _ = scenario(ctx)
        result = estimate_return(ctx, notional="600", profit_fraction="0.02", loss_fraction="0.008",
                                 win_probability="0.7", cost=cost, win_evidence=evidence(ctx))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        with self.assertRaises(ValueError):
            estimate_return(context(features("SUIUSDT")), notional="500", profit_fraction="0.02",
                            loss_fraction="0.008", win_probability="0.7", cost=cost)


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.data = features()
        self.ctx = context(self.data)
        self.cost, self.expected = scenario(self.ctx)

    def entry(self, **kwargs):
        args = dict(signal_max_price="100.10", notional="500", atr="0.2", cost=self.cost, expected=self.expected)
        args.update(kwargs)
        return price_entry(self.ctx, self.data, **args)

    def test_three_limits_and_reject_are_distinct(self):
        for mode, expected_price in (("PASSIVE_LIMIT", "100"), ("IMPROVED_LIMIT", "100.01"), ("MARKETABLE_LIMIT", "100.04")):
            result = self.entry(mode=mode)
            self.assertEqual(result.mode, mode, result)
            self.assertEqual(result.limit_price, Decimal(expected_price))
            self.assertLessEqual(result.limit_price, result.max_buy_price)
            self.assertEqual(result.quantity % Decimal("0.001"), 0)
            self.assertFalse(result.live_authorized)
        self.assertEqual(self.entry(mode="REJECT").mode, "REJECT")

    def test_marketability_never_overrides_maximum(self):
        result = self.entry(mode="MARKETABLE_LIMIT", signal_max_price="100.025")
        self.assertEqual(result.mode, "REJECT")
        self.assertEqual(result.max_buy_price, Decimal("100.02"))
        self.assertIsNone(result.limit_price)
        self.assertEqual(result.quantity, 0)
        self.assertIn("ASK_EXCEEDS_MAX_BUY_PRICE", result.reasons)

    def test_missing_execution_evidence_never_outputs_candidate(self):
        cost, expected = scenario(self.ctx, reliable=False)
        result = self.entry(cost=cost, expected=expected)
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result.mode, "REJECT")
        self.assertEqual(result.quantity, 0)

    def test_future_evidence_and_other_cost_provenance_rejected(self):
        future = replace(self.cost, context=replace(self.cost.context, decision_time_ms=NOW + 1))
        self.assertIn("EVIDENCE_FROM_FUTURE", self.entry(cost=future).reasons)
        changed = estimate_cost(self.ctx, entry_price="100", exit_price="102", quantity="5",
                                exit_slippage_bps="50", evidence=evidence(self.ctx))
        self.assertIn("EXPECTED_RETURN_COST_SOURCE_MISMATCH", self.entry(cost=changed).reasons)

    def test_stale_unsynced_zero_qty_and_volatile_rejected(self):
        for field, value in (("synced", False), ("received_time_ms", NOW - 2001), ("exchange_time_ms", NOW + 1), ("bid_qty", "0")):
            with self.subTest(field=field):
                changed = deepcopy(self.data)
                changed[field] = value
                result = price_entry(context(changed), changed, signal_max_price="101", notional="500", atr="0.2")
                self.assertEqual(result.mode, "REJECT")
        self.assertIn("ATR_EXCEEDS_INITIAL_STOP", self.entry(atr="2").reasons)
        with self.assertRaises(ValueError):
            self.entry(stop_distance="0.02")

    def test_exit_tick_floor_gap_and_remaining_quantity(self):
        result = price_exit(self.ctx, self.data, quantity="3.1234", min_sell_price="99.8")
        self.assertEqual(result.limit_price, Decimal("100"))
        self.assertEqual(result.quantity, Decimal("3.123"))
        self.assertEqual(result.mode, "MARKETABLE_LIMIT")
        gap = price_exit(self.ctx, self.data, quantity="3", min_sell_price="100.01")
        self.assertEqual(gap.mode, "REJECT")
        self.assertIsNone(gap.limit_price)
        self.assertIn("BID_BELOW_SELL_PROTECTION", gap.reasons)
        self.assertEqual(price_exit(self.ctx, self.data, quantity="0.0001", min_sell_price="99").mode, "REJECT")


class FillProbabilityTests(unittest.TestCase):
    def test_probabilities_sum_one_and_touch_is_not_certainty(self):
        data = features()
        for price in ("99", "100", "100.04"):
            result = estimate_fill(context(data), data, limit_price=price, quantity="3", wait_ms=5000)
            total = sum(getattr(result, name) for name in ("no_fill", "partial_fill", "full_fill", "timeout", "cancelled", "adverse_fill"))
            self.assertAlmostEqual(total, Decimal(1), places=26)
            self.assertLess(result.full_fill + result.partial_fill + result.adverse_fill, Decimal(1))
            self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
            self.assertGreater(result.adverse_fill, 0)
            self.assertGreater(result.partial_fill, 0)

    def test_passive_hazard_matches_independent_decimal_reference(self):
        data = features()
        result = estimate_fill(context(data), data, limit_price="100", quantity="3", wait_ms=5000)
        with localcontext() as p:
            p.prec = 40
            flow = Decimal(100) * Decimal(5000) / Decimal(60000) * Decimal("0.3")
            probability = 1 - (-(flow / Decimal(63))).exp()
            expected_any = Decimal("0.98") * probability
        actual_any = result.partial_fill + result.full_fill + result.adverse_fill
        self.assertAlmostEqual(actual_any, expected_any, places=27)

    def test_more_wait_increases_passive_fill_and_missing_flow_fails(self):
        data = features()
        first = estimate_fill(context(data), data, limit_price="100", quantity="3", wait_ms=1000)
        later = estimate_fill(context(data), data, limit_price="100", quantity="3", wait_ms=5000)
        self.assertLess(first.expected_fill_fraction, later.expected_fill_fraction)
        data["buy_ratio"] = None
        self.assertIn("ACTIVE_FLOW_MISSING", estimate_fill(context(data), data, limit_price="100", quantity="3", wait_ms=1000).reasons)

    def test_stale_trade_flow_cannot_feed_probability(self):
        data = features()
        data["trade_event_time_ms"] = NOW - 60_001
        result = estimate_fill(context(data), data, limit_price="100", quantity="3", wait_ms=1000)
        self.assertEqual(result.status, "REJECT")
        self.assertEqual(result.no_fill, 1)

    def test_calibration_outcomes_brier_and_no_auto_training(self):
        rows = [FillObservation(Decimal("0.8"), "FULL_FILL", Decimal(1), "1" * 64),
                FillObservation(Decimal("0.4"), "TIMEOUT", Decimal(0), "2" * 64)]
        result = calibration_report(rows)
        self.assertEqual(result["brier_score"], (Decimal("0.2") ** 2 + Decimal("0.4") ** 2) / 2)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["automatically_applied"])
        for outcome, fraction in (("NO_FILL", "0"), ("PARTIAL_FILL", "0.4"), ("FULL_FILL", "1"),
                                  ("TIMEOUT", "0"), ("CANCELLED", "0"), ("ADVERSE_FILL", "0.6")):
            FillObservation(Decimal("0.5"), outcome, Decimal(fraction), "a" * 64)
        with self.assertRaises(ValueError):
            FillObservation(Decimal("0.5"), "PARTIAL_FILL", Decimal(1), "a" * 64)
        with self.assertRaises(ValueError):
            calibration_report(rows + rows)


class SizingTests(unittest.TestCase):
    def size(self, data=None, strategy="T1", **kwargs):
        data = features() if data is None else data
        args = dict(score="100", entry_price="100", equity="2000", high_watermark="2000", cash_balance="2000")
        args.update(kwargs)
        return size_position(context(data, strategy), data, **args)

    def test_score_boundaries_and_independent_risk_formula(self):
        self.assertEqual(self.size(score="75").notional, Decimal(300))
        self.assertEqual(self.size(score="87.5").score_notional, Decimal(500))
        result = self.size()
        with localcontext() as p:
            p.prec = 40
            cap = Decimal("2000") * Decimal("0.004") / (Decimal("0.008") + Decimal("0.001") * 2 + Decimal("0.002"))
        self.assertEqual(result.score_notional, Decimal(700))
        self.assertEqual(result.risk_notional, cap)
        self.assertEqual(result.notional, Decimal("666.6"))
        self.assertLessEqual(result.risk_estimate, Decimal(8))
        self.assertLess(self.size(strategy="R1").risk_notional, result.risk_notional)

    def test_fee_inclusive_reservations_cap_available_cash(self):
        result = self.size(cash_balance="900", reserved_entry_notional="500", reserved_entry_fees="0.5")
        self.assertLessEqual(result.notional + result.fee_estimate, Decimal("399.5"))
        self.assertEqual(self.size(cash_balance="300").status, "REJECT")
        self.assertIn("ALLOWED_NOTIONAL_BELOW_300", self.size(cash_balance="300").reasons)

    def test_two_slots_pending_and_five_percent_are_hard_gates(self):
        for inputs in (dict(open_positions=2), dict(open_positions=1, pending_entries=1), dict(pending_entries=2),
                       dict(hard_locked=True), dict(equity="1900"), dict(equity="1899")):
            result = self.size(**inputs)
            self.assertEqual(result.status, "REJECT", inputs)
            self.assertEqual(result.quantity, 0)
        self.assertEqual(self.size(equity="1900.01").status, "RESEARCH_SIZED")
        self.assertEqual(self.size(hard_locked=True, equity="2100").status, "REJECT")

    def test_symbol_risk_versions_distinct_and_sui_never_live_allowed(self):
        sol, sui = self.size(), self.size(features("SUIUSDT"))
        self.assertNotEqual(sol.parameter_version, sui.parameter_version)
        self.assertEqual(sui.context.account_id, "SUI-live")
        self.assertFalse(sui.live_authorized)
        self.assertFalse(self.size(features("SUIUSDT"), strategy="R1").live_authorized)
        self.assertEqual(self.size(score="74.99").status, "REJECT")


if __name__ == "__main__":
    unittest.main()
