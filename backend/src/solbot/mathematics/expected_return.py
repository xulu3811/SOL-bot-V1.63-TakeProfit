"""Conditional return expectation, with mandatory evidence qualification."""
from decimal import localcontext

from .contracts import ExpectedReturnDecision, bind_context, validate_related_context
from .decimal_utils import D, ONE

VERSION = "expected-return-research-1"


def estimate_return(context, *, notional, profit_fraction, loss_fraction,
                    win_probability=None, cost=None, win_evidence=None, execution=None):
    context = bind_context(context, dict(notional=notional, profit_fraction=profit_fraction,
                           loss_fraction=loss_fraction, win_probability=win_probability, cost=cost,
                           win_evidence=win_evidence, execution=execution), VERSION)
    amount, gain, loss = D(notional, positive=True), D(profit_fraction, positive=True), D(loss_fraction, positive=True)
    reasons = []
    if win_probability is None:
        reasons.append("WIN_PROBABILITY_MISSING")
    if cost is None:
        reasons.append("EXECUTION_COST_MISSING")
    if win_evidence is None or not win_evidence.sufficient(context):
        reasons.append("WIN_EVIDENCE_INSUFFICIENT")
    if cost is not None:
        reasons.extend(validate_related_context(context, cost.context))
        if cost.entry_notional != amount or cost.context.valid_until_ms <= context.decision_time_ms:
            reasons.append("EXECUTION_COST_SIZE_OR_VALIDITY_MISMATCH")
        if not cost.evidence_sufficient:
            reasons.append("COST_EVIDENCE_INSUFFICIENT")
    fraction = net = filled = None
    if win_probability is not None and cost is not None:
        probability = D(win_probability, nonnegative=True)
        if probability > 1:
            raise ValueError("win probability exceeds one")
        with localcontext() as precision:
            precision.prec = 40
            fraction = probability * gain - (ONE - probability) * loss - cost.total_cost / amount
            net = fraction * amount
            if execution is not None:
                reasons.extend(validate_related_context(context, execution.context))
                filled = net * execution.expected_fill_fraction
                reasons.append("FILL_MODEL_UNCALIBRATED")
    status = "INSUFFICIENT_EVIDENCE" if reasons else "RESEARCH_POSITIVE" if fraction > 0 else "RESEARCH_NONPOSITIVE"
    return ExpectedReturnDecision(context=context, status=status, parameter_version=VERSION,
                                  reasons=tuple(reasons), expected_return_fraction=fraction,
                                  expected_net_quote=net, fill_adjusted_net_quote=filled,
                                  quantity=cost.quantity if cost else D(0),
                                  fee_estimate=cost.entry_fee + cost.exit_fee if cost else D(0),
                                  risk_estimate=amount * loss,
                                  notional=amount, cost_input_hash=cost.context.input_hash if cost else None,
                                  assumptions=("E=p_win*G-(1-p_win)*L-C conditional on filled exposure",
                                               "Scenario values do not establish confidence or Guardian authorization"))
