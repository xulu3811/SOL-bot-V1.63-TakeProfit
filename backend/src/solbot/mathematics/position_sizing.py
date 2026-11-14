"""Score/risk/balance constrained research sizing; live approval is impossible."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .contracts import PositionSizingDecision, SymbolRules, bind_context, feature_reasons
from .decimal_utils import D, ZERO, ONE, clip, floor_step


@dataclass(frozen=True)
class ResearchRiskParameters:
    symbol: str
    version: str
    t1_budget: Decimal = Decimal("0.004")
    r1_budget: Decimal = Decimal("0.0025")
    min_notional: Decimal = Decimal("300")
    max_notional: Decimal = Decimal("700")
    allow_cash_fallback: bool = False


PARAMETERS = {symbol: ResearchRiskParameters(symbol, symbol.lower() + "-risk-research-1")
              for symbol in ("SOLUSDT", "SUIUSDT")}


def size_position(context, features, *, score, entry_price, equity, high_watermark,
                  cash_balance, reserved_entry_notional="0", reserved_entry_fees="0",
                  open_positions=0, pending_entries=0, hard_locked=False,
                  entry_fee_rate="0.001", exit_fee_rate="0.001", slippage_fraction="0.002",
                  stop_distance="0.008", reference_price=None, reference_minutes=None,
                  parameters=None):
    parameters = parameters if parameters is not None else PARAMETERS[context.symbol]
    inputs = dict(features=features, score=score, entry_price=entry_price, equity=equity,
                  high_watermark=high_watermark, cash_balance=cash_balance,
                  reserved_entry_notional=reserved_entry_notional, reserved_entry_fees=reserved_entry_fees,
                  open_positions=open_positions, pending_entries=pending_entries, hard_locked=hard_locked,
                  entry_fee_rate=entry_fee_rate, exit_fee_rate=exit_fee_rate, slippage_fraction=slippage_fraction,
                  stop_distance=stop_distance, reference_price=reference_price, reference_minutes=reference_minutes,
                  parameters=parameters)
    context = bind_context(context, inputs, parameters.version)
    score, price = D(score), D(entry_price, positive=True)
    net, high, cash = D(equity, positive=True), D(high_watermark, positive=True), D(cash_balance, nonnegative=True)
    reserved = D(reserved_entry_notional, nonnegative=True) + D(reserved_entry_fees, nonnegative=True)
    entry_fee, exit_fee, slip, stop = (D(value, nonnegative=True) for value in
                                      (entry_fee_rate, exit_fee_rate, slippage_fraction, stop_distance))
    if context.strategy_id == "T2":
        if stop < Decimal("0.008") or stop > Decimal("0.020"):
            raise ValueError("T2 stop distance must stay between 0.8 and 2.0 percent")
        risk_budget = parameters.t1_budget
    else:
        if stop < Decimal("0.008") or stop > Decimal("0.015"):
            raise ValueError("initial stop distance must stay between 0.8 and 1.5 percent")
        risk_budget = parameters.t1_budget if context.strategy_id == "T1" else parameters.r1_budget
    if entry_fee > Decimal("0.02") or exit_fee > Decimal("0.02") or slip > Decimal("0.1"):
        raise ValueError("fee/slippage input outside research bounds")
    if any(type(value) is not int or value < 0 for value in (open_positions, pending_entries)) or type(hard_locked) is not bool:
        raise ValueError("invalid account slots or lock state")
    reasons = list(feature_reasons(context, features))
    if score < 75 or score > 100:
        reasons.append("SCORE_OUTSIDE_ENTRY_RANGE")
    if hard_locked or (high - net) / high >= Decimal("0.05"):
        reasons.append("HARD_DRAWDOWN_LOCK")
    if open_positions + pending_entries >= 2:
        reasons.append("TWO_SLOTS_OCCUPIED")
    if reserved > cash:
        reasons.append("RESERVATIONS_EXCEED_CASH")
    with localcontext() as precision:
        precision.prec = 40
        min_n = parameters.min_notional
        max_n = parameters.max_notional
        n_score = min_n + (max_n - min_n) * clip((score - 75) / 25)
        loss_fraction = stop + entry_fee + exit_fee + slip
        n_risk = net * risk_budget / loss_fraction
        n_available = max(ZERO, cash - reserved) / (ONE + entry_fee)
        if parameters.allow_cash_fallback and min_n <= n_available < max_n:
            permitted = min(max_n, n_risk, floor_step(n_available, Decimal("0.01")))
        else:
            permitted = min(n_score, max_n, n_risk, n_available)
        if permitted < min_n:
            reasons.append(f"ALLOWED_NOTIONAL_BELOW_{int(min_n)}")
        rules = SymbolRules.from_exchange_info(features["rules"], context.symbol)
        qty = floor_step(permitted / price, rules.step_size)
        actual = qty * price
        if parameters.allow_cash_fallback and actual < min_n and (qty + rules.step_size) * price <= min(max_n, n_available):
            qty = qty + rules.step_size
            actual = qty * price
        min_rounded = (
            max(rules.min_notional, min_n - price * rules.step_size)
            if parameters.allow_cash_fallback and permitted >= min_n
            else min_n
        )
        if actual < min_rounded:
            reasons.append(f"ROUNDED_NOTIONAL_BELOW_{int(min_n)}")
        if qty > 0:
            reasons.extend(rules.validate_order(price, qty, reference_price=reference_price,
                                                 reference_minutes=reference_minutes))
        return PositionSizingDecision(context=context, status="REJECT" if reasons else "RESEARCH_SIZED",
                                      parameter_version=parameters.version, reasons=tuple(sorted(set(reasons))),
                                      limit_price=price, quantity=ZERO if reasons else qty,
                                      notional=ZERO if reasons else actual, fee_estimate=actual * entry_fee,
                                      risk_estimate=actual * loss_fraction, score_notional=n_score,
                                      risk_notional=n_risk, available_notional=n_available)
