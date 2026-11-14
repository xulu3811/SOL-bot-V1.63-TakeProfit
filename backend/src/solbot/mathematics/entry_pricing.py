"""Bounded four-mode buy proposals; never chases beyond the supplied ceiling."""
from decimal import Decimal, localcontext

from .contracts import PricingDecision, SymbolRules, bind_context, feature_reasons, validate_related_context
from .decimal_utils import D, ZERO, ONE, floor_step, ceil_step

VERSION = "entry-pricing-research-1"
MODES = ("PASSIVE_LIMIT", "IMPROVED_LIMIT", "MARKETABLE_LIMIT", "REJECT")


def price_entry(context, features, *, signal_max_price, notional, atr,
                expected=None, cost=None, mode="AUTO", max_chase_bps="10",
                stop_distance="0.008", reference_price=None, reference_minutes=None):
    context = bind_context(context, dict(features=features, signal_max_price=signal_max_price,
                           notional=notional, atr=atr, expected=expected, cost=cost, mode=mode,
                           max_chase_bps=max_chase_bps, stop_distance=stop_distance,
                           reference_price=reference_price, reference_minutes=reference_minutes), VERSION)
    if mode not in MODES + ("AUTO",):
        raise ValueError("unsupported pricing mode")
    ceiling, amount, volatility = D(signal_max_price, positive=True), D(notional, positive=True), D(atr, nonnegative=True)
    stop, chase = D(stop_distance, positive=True), D(max_chase_bps, nonnegative=True) / 10_000
    max_stop = Decimal("0.020") if getattr(context, "strategy_id", None) == "T2" else Decimal("0.015")
    if stop < Decimal("0.008") or stop > max_stop or chase > Decimal("0.005"):
        raise ValueError("initial stop cannot widen; chase research cap is 50bps")
    reasons = list(feature_reasons(context, features))
    if not Decimal("300") <= amount <= Decimal("700"):
        reasons.append("NOTIONAL_OUTSIDE_300_700")
    if mode == "REJECT":
        reasons.append("EXPLICIT_REJECT")
    if reasons:
        return PricingDecision(context=context, status="REJECT", parameter_version=VERSION,
                               reasons=tuple(reasons), max_buy_price=ceiling)
    bid, ask = D(features["bid"]), D(features["ask"])
    rules = SymbolRules.from_exchange_info(features["rules"], context.symbol)
    if volatility / ask > stop:
        reasons.append("ATR_EXCEEDS_INITIAL_STOP")
    if expected is None or expected.status != "RESEARCH_POSITIVE":
        reasons.append("EXPECTED_RETURN_INSUFFICIENT")
    if cost is None or not cost.evidence_sufficient:
        reasons.append("EXECUTION_COST_INSUFFICIENT")
    for evidence in (expected, cost):
        if evidence is not None:
            reasons.extend(validate_related_context(context, evidence.context))
    if cost is not None and cost.entry_notional != amount:
        reasons.append("COST_NOTIONAL_MISMATCH")
    if expected is not None and expected.notional != amount:
        reasons.append("EXPECTED_RETURN_NOTIONAL_MISMATCH")
    if expected is not None and cost is not None and expected.cost_input_hash != cost.context.input_hash:
        reasons.append("EXPECTED_RETURN_COST_SOURCE_MISMATCH")
    buy_ratio = features.get("buy_ratio")
    if buy_ratio is None:
        reasons.append("ACTIVE_FLOW_MISSING")
        ratio = ZERO
    else:
        ratio = D(buy_ratio, nonnegative=True)
        if ratio > ONE:
            raise ValueError("buy ratio exceeds one")
    # Recompute microprice from visible amounts, not a potentially stale derived
    # value. It is pressure information, never a promised future price.
    bqty, aqty = D(features["bid_qty"]), D(features["ask_qty"])
    with localcontext() as precision:
        precision.prec = 40
        micro = (ask * bqty + bid * aqty) / (bqty + aqty)
        edge_cap = ask * (ONE + max(ZERO, expected.expected_return_fraction) / 2) if expected and expected.expected_return_fraction is not None else ask
        maximum = floor_step(min(ceiling, ask * (ONE + chase), edge_cap), rules.tick_size)
        selected = mode
        if selected == "AUTO":
            if ratio >= Decimal("0.65") and micro > (bid + ask) / 2 and expected and expected.expected_return_fraction is not None and expected.expected_return_fraction >= Decimal("0.004"):
                selected = "MARKETABLE_LIMIT"
            elif ask - bid >= 3 * rules.tick_size and ratio >= Decimal("0.55") and micro > (bid + ask) / 2:
                selected = "IMPROVED_LIMIT"
            else:
                selected = "PASSIVE_LIMIT"
        if selected == "MARKETABLE_LIMIT":
            price = ceil_step(ask, rules.tick_size)
            if price > maximum:
                reasons.append("ASK_EXCEEDS_MAX_BUY_PRICE")
        elif selected == "IMPROVED_LIMIT":
            price = floor_step(min(bid + rules.tick_size, micro, ask - rules.tick_size, maximum), rules.tick_size)
            if not bid < price < ask:
                reasons.append("NO_IMPROVEMENT_WITHIN_PRICE_CAP")
        else:
            price = floor_step(min(bid, maximum), rules.tick_size)
        if price <= 0 or price > maximum:
            reasons.append("PRICE_PROTECTION")
        qty = floor_step(amount / price, rules.step_size) if price > 0 else ZERO
        actual = qty * price
        if qty <= 0 or actual < 300 or actual > amount:
            reasons.append("ROUNDED_NOTIONAL_OUTSIDE_BUDGET")
        elif price > 0:
            reasons.extend(rules.validate_order(price, qty, "BUY", reference_price=reference_price,
                                                 reference_minutes=reference_minutes))
        insufficient = any("INSUFFICIENT" in reason for reason in reasons)
        return PricingDecision(context=context, status="INSUFFICIENT_EVIDENCE" if insufficient else "REJECT" if reasons else "RESEARCH_CANDIDATE",
                               parameter_version=VERSION, reasons=tuple(sorted(set(reasons))),
                               mode="REJECT" if reasons else selected,
                               limit_price=price if ZERO < price <= maximum else None,
                               max_buy_price=maximum, quantity=ZERO if reasons else qty,
                               notional=ZERO if reasons else actual,
                               fee_estimate=cost.entry_fee if cost else ZERO,
                               risk_estimate=actual * stop + (cost.total_cost if cost else ZERO))
