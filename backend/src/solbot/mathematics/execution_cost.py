"""Configurable research fees/slippage; never reads private commission rates."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .contracts import DecisionBase, Evidence, bind_context
from .decimal_utils import D, ZERO, ONE

VERSION = "execution-cost-research-1"


@dataclass(frozen=True)
class FeeSchedule:
    maker: Decimal = Decimal("0.001")
    taker: Decimal = Decimal("0.001")
    bnb_discount: Decimal = Decimal("0.25")
    bnb_discount_enabled: bool = False
    version: str = "public-research-fees-1"

    def __post_init__(self):
        for name in ("maker", "taker", "bnb_discount"):
            value = D(getattr(self, name), name, nonnegative=True)
            if value > (ONE if name == "bnb_discount" else Decimal("0.02")):
                raise ValueError("research fee configuration outside bounds")
            object.__setattr__(self, name, value)
        if type(self.bnb_discount_enabled) is not bool or not self.version:
            raise ValueError("explicit fee assumption/version required")

    def rate(self, liquidity):
        if liquidity not in ("MAKER", "TAKER"):
            raise ValueError("liquidity must be MAKER or TAKER")
        base = self.maker if liquidity == "MAKER" else self.taker
        return base * (ONE - self.bnb_discount if self.bnb_discount_enabled else ONE)


@dataclass(frozen=True, kw_only=True)
class CostEstimate(DecisionBase):
    entry_notional: Decimal = ZERO
    exit_notional: Decimal = ZERO
    quantity: Decimal = ZERO
    entry_fee: Decimal = ZERO
    exit_fee: Decimal = ZERO
    entry_slippage: Decimal = ZERO
    exit_slippage: Decimal = ZERO
    uncertainty_cost: Decimal = ZERO
    total_cost: Decimal = ZERO
    cost_fraction: Decimal = ZERO
    evidence_sufficient: bool = False
    assumptions: tuple[str, ...] = ()


def estimate_cost(context, *, entry_price, exit_price, quantity,
                  entry_liquidity="TAKER", exit_liquidity="TAKER",
                  entry_slippage_bps="5", exit_slippage_bps="10", uncertainty_bps="5",
                  fees=FeeSchedule(), evidence: Evidence | None = None):
    inputs = dict(entry_price=entry_price, exit_price=exit_price, quantity=quantity,
                  entry_liquidity=entry_liquidity, exit_liquidity=exit_liquidity,
                  entry_slippage_bps=entry_slippage_bps, exit_slippage_bps=exit_slippage_bps,
                  uncertainty_bps=uncertainty_bps, fees=fees, evidence=evidence)
    context = bind_context(context, inputs, VERSION)
    entry, exit_, qty = D(entry_price, positive=True), D(exit_price, positive=True), D(quantity, positive=True)
    slips = [D(value, nonnegative=True) / 10_000 for value in
             (entry_slippage_bps, exit_slippage_bps, uncertainty_bps)]
    if any(value > Decimal("0.1") for value in slips):
        raise ValueError("slippage input exceeds research bound")
    with localcontext() as precision:
        precision.prec = 40
        en, ex = entry * qty, exit_ * qty
        entry_fee, exit_fee = en * fees.rate(entry_liquidity), ex * fees.rate(exit_liquidity)
        entry_slip, exit_slip, uncertainty = en * slips[0], ex * slips[1], en * slips[2]
        total = entry_fee + exit_fee + entry_slip + exit_slip + uncertainty
        sufficient = evidence is not None and evidence.sufficient(context)
        return CostEstimate(context=context, status="RESEARCH_ESTIMATE" if sufficient else "INSUFFICIENT_EVIDENCE",
                            parameter_version=VERSION, reasons=() if sufficient else ("UNVALIDATED_EXECUTION_COST",),
                            entry_notional=en, exit_notional=ex, quantity=qty, entry_fee=entry_fee, exit_fee=exit_fee,
                            entry_slippage=entry_slip, exit_slippage=exit_slip, uncertainty_cost=uncertainty,
                            total_cost=total, cost_fraction=total / en, evidence_sufficient=sufficient,
                            assumptions=("Fees are research inputs, not authenticated account commissions",
                                         "BNB discount is an explicit scenario, not a balance or replenishment claim",
                                         "Slippage and uncertainty are not observed execution guarantees"))
