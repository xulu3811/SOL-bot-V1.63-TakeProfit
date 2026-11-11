"""Uncalibrated conservative queue-hazard model and observable calibration API."""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import islice

from .contracts import ExecutionEstimate, bind_context, feature_reasons
from .decimal_utils import D, ZERO, ONE, clip, canonical_hash

VERSION = "queue-hazard-assumption-1"
OUTCOMES = ("NO_FILL", "PARTIAL_FILL", "FULL_FILL", "TIMEOUT", "CANCELLED", "ADVERSE_FILL")


def estimate_fill(context, features, *, limit_price, quantity, wait_ms, side="BUY",
                  window_ms=60_000, cancellation_probability="0.02"):
    inputs = dict(features=features, limit_price=limit_price, quantity=quantity,
                  wait_ms=wait_ms, side=side, window_ms=window_ms,
                  cancellation_probability=cancellation_probability)
    context = bind_context(context, inputs, VERSION)
    reasons = list(feature_reasons(context, features))
    price, qty = D(limit_price, positive=True), D(quantity, positive=True)
    if side not in ("BUY", "SELL") or type(wait_ms) is not int or not 1 <= wait_ms <= 300_000:
        raise ValueError("invalid fill horizon or side")
    if type(window_ms) is not int or window_ms <= 0:
        raise ValueError("invalid trade observation window")
    for name in ("trade_event_time_ms", "trade_received_time_ms"):
        value = features.get(name)
        if type(value) is not int or not 0 <= context.decision_time_ms - value <= window_ms:
            reasons.append("TRADE_WINDOW_MISSING_STALE_OR_FUTURE")
    cancel = D(cancellation_probability, nonnegative=True)
    if cancel > Decimal("0.1"):
        raise ValueError("cancellation assumption exceeds bound")
    if reasons:
        return ExecutionEstimate(context=context, status="REJECT", parameter_version=VERSION,
                                 reasons=tuple(reasons), no_fill=ONE, limit_price=price, quantity=qty)
    bid, ask = D(features["bid"]), D(features["ask"])
    volume = D(features.get("trade_volume", "0"), nonnegative=True)
    buy_ratio = features.get("buy_ratio")
    if buy_ratio is None:
        return ExecutionEstimate(context=context, status="INSUFFICIENT_EVIDENCE", parameter_version=VERSION,
                                 reasons=("ACTIVE_FLOW_MISSING",), no_fill=ONE, limit_price=price, quantity=qty)
    ratio = D(buy_ratio, nonnegative=True)
    if ratio > ONE:
        raise ValueError("buy ratio exceeds one")
    marketable = price >= ask if side == "BUY" else price <= bid
    opposing = ONE - ratio if side == "BUY" else ratio
    displayed = D(features["bid_qty"] if side == "BUY" else features["ask_qty"], positive=True)
    distance = max(ZERO, (bid - price) / bid if side == "BUY" else (price - ask) / ask)
    volatility = D(features.get("short_volatility", "0"), nonnegative=True)
    with localcontext() as precision:
        precision.prec = 40
        # Twice visible depth is a conservative queue proxy, not known priority.
        flow = volume * D(wait_ms) / D(window_ms) * opposing
        hazard = flow / (displayed * 2 + qty) / (ONE + distance / max(volatility, Decimal("0.0001")))
        passive_probability = ONE - (-hazard).exp()
        probability = min(Decimal("0.70"), passive_probability)
        if marketable:
            # Displayed depth can disappear; even a crossed price is not 100%.
            visible = D(features["ask_qty"] if side == "BUY" else features["bid_qty"], positive=True)
            probability = min(Decimal("0.90"), Decimal("0.5") + Decimal("0.4") * min(ONE, visible / qty))
        fill = (ONE - cancel) * probability
        adverse_share = clip(Decimal("0.10") + opposing * Decimal("0.15"), "0.10", "0.25")
        adverse = fill * adverse_share
        partial = fill * Decimal("0.35")
        full = fill - partial - adverse
        unfilled = ONE - cancel - fill
        timeout = unfilled * Decimal("0.8")
        no_fill = unfilled - timeout
        return ExecutionEstimate(context=context, status="INSUFFICIENT_EVIDENCE", parameter_version=VERSION,
                                 reasons=("UNOBSERVED_QUEUE_POSITION", "UNCALIBRATED_FILL_MODEL"),
                                 no_fill=no_fill, partial_fill=partial, full_fill=full,
                                 timeout=timeout, cancelled=cancel, adverse_fill=adverse,
                                 expected_fill_fraction=partial / 2 + full + adverse,
                                 limit_price=price, quantity=qty, risk_estimate=price * qty * adverse,
                                 assumptions=("Mutually exclusive terminal outcomes; adverse fills classified separately",
                                              "Partial outcome assumes half quantity; adverse outcome assumes full quantity",
                                              "Touch/crossing is not fill certainty; no actual queue data",
                                              "No external/private order data or automatic parameter training"))


@dataclass(frozen=True)
class FillObservation:
    predicted_any_fill: Decimal
    outcome: str
    filled_fraction: Decimal
    source_hash: str

    def __post_init__(self):
        p, fraction = D(self.predicted_any_fill, nonnegative=True), D(self.filled_fraction, nonnegative=True)
        if p > ONE or fraction > ONE or self.outcome not in OUTCOMES:
            raise ValueError("invalid calibration observation")
        if (self.outcome in ("NO_FILL", "TIMEOUT", "CANCELLED") and fraction != 0
                or self.outcome == "FULL_FILL" and fraction != 1
                or self.outcome == "PARTIAL_FILL" and not 0 < fraction < 1
                or self.outcome == "ADVERSE_FILL" and fraction <= 0):
            raise ValueError("outcome and fill fraction disagree")
        if len(self.source_hash) != 64 or any(c not in "0123456789abcdef" for c in self.source_hash):
            raise ValueError("observation source hash required")
        object.__setattr__(self, "predicted_any_fill", p)
        object.__setattr__(self, "filled_fraction", fraction)


def calibration_report(observations, *, out_of_sample=False, minimum_samples=100):
    rows = tuple(islice(observations, 100_001))
    if len(rows) > 100_000 or type(minimum_samples) is not int or minimum_samples < 100:
        raise ValueError("bounded calibration and minimum 100 observations required")
    if len({row.source_hash for row in rows}) != len(rows):
        raise ValueError("duplicate calibration evidence")
    count = len(rows)
    brier = sum(((row.predicted_any_fill - D(int(row.filled_fraction > 0))) ** 2 for row in rows), ZERO) / count if count else None
    actual = sum((D(int(row.filled_fraction > 0)) for row in rows), ZERO) / count if count else None
    return {"status": "DIAGNOSTIC_ONLY" if count >= minimum_samples and out_of_sample is True else "INSUFFICIENT_EVIDENCE",
            "sample_count": count, "out_of_sample": out_of_sample, "brier_score": brier,
            "observed_any_fill": actual, "input_hash": canonical_hash(rows), "parameter_version": VERSION,
            "automatically_applied": False, "live_authorized": False,
            "limitations": "Calibration diagnostics do not certify reliable probabilities or update model parameters"}
