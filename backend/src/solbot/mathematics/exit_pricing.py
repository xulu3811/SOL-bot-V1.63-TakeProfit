"""Protective sell limit proposals with explicit non-fill risk."""
from decimal import Decimal

from .contracts import ExitDecision, SymbolRules, bind_context, feature_reasons
from .decimal_utils import D, ZERO, ONE, floor_step, ceil_step
from .execution_cost import FeeSchedule

VERSION = "exit-pricing-research-1"


def price_exit(context, features, *, quantity, min_sell_price, mode="MARKETABLE_LIMIT",
               max_slippage_bps="20", fees=FeeSchedule(), reference_price=None, reference_minutes=None):
    context = bind_context(context, dict(features=features, quantity=quantity,
                           min_sell_price=min_sell_price, mode=mode, max_slippage_bps=max_slippage_bps,
                           fees=fees, reference_price=reference_price, reference_minutes=reference_minutes), VERSION)
    if mode not in ("PASSIVE_LIMIT", "IMPROVED_LIMIT", "MARKETABLE_LIMIT", "REJECT"):
        raise ValueError("invalid sell mode")
    qty, minimum = D(quantity, positive=True), D(min_sell_price, positive=True)
    slip = D(max_slippage_bps, nonnegative=True) / 10_000
    if slip > Decimal("0.05"):
        raise ValueError("sell slippage research bound exceeded")
    reasons = list(feature_reasons(context, features))
    if mode == "REJECT":
        reasons.append("EXPLICIT_REJECT")
    if reasons:
        return ExitDecision(context=context, status="REJECT", parameter_version=VERSION,
                            reasons=tuple(reasons), min_sell_price=minimum)
    rules = SymbolRules.from_exchange_info(features["rules"], context.symbol)
    bid, ask = D(features["bid"]), D(features["ask"])
    minimum = ceil_step(max(minimum, bid * (ONE - slip)), rules.tick_size)
    if mode == "MARKETABLE_LIMIT":
        price = floor_step(bid, rules.tick_size)
        if price < minimum:
            reasons.append("BID_BELOW_SELL_PROTECTION")
    elif mode == "PASSIVE_LIMIT":
        price = ceil_step(max(ask, minimum), rules.tick_size)
    else:
        price = ceil_step(max(ask - rules.tick_size, bid + rules.tick_size, minimum), rules.tick_size)
        if not bid < price < ask:
            reasons.append("NO_INTERIOR_SELL_TICK")
    rounded = floor_step(qty, rules.step_size)
    if rounded <= 0:
        reasons.append("DUST_QUANTITY")
    else:
        reasons.extend(rules.validate_order(price, rounded, "SELL", reference_price=reference_price,
                                             reference_minutes=reference_minutes))
    return ExitDecision(context=context, status="REJECT" if reasons else "RESEARCH_CANDIDATE",
                        parameter_version=VERSION, reasons=tuple(sorted(set(reasons))), mode="REJECT" if reasons else mode,
                        limit_price=price if price >= minimum else None,
                        min_sell_price=minimum, quantity=ZERO if reasons else rounded,
                        fee_estimate=price * rounded * fees.rate("TAKER" if mode == "MARKETABLE_LIMIT" else "MAKER"),
                        risk_estimate=max(ZERO, (ask - price) * rounded))
