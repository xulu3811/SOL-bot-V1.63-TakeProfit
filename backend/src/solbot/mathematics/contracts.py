"""Immutable P3 mathematical proposals. These are never Guardian permissions."""
from dataclasses import dataclass, field, replace
from decimal import Decimal
import re
from typing import Mapping

from solbot.storage.models import timestamp_ms as timestamp
from .decimal_utils import D, ZERO, canonical_hash, json_safe

ACCOUNTS = {"SOLUSDT": "SOL-shadow", "SUIUSDT": "SUI-live"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ValueError("invalid decision identifier")
    return value


@dataclass(frozen=True)
class DecisionContext:
    symbol: str
    account_id: str
    strategy_id: str
    strategy_version: str
    signal_id: str
    decision_time_ms: int
    book_time_ms: int | None
    valid_until_ms: int
    input_hash: str

    def __post_init__(self):
        if ACCOUNTS.get(self.symbol) != self.account_id:
            raise ValueError("symbol/account isolation violation")
        if self.strategy_id not in ("T1", "R1"):
            raise ValueError("unsupported strategy")
        identifier(self.strategy_version)
        identifier(self.signal_id)
        timestamp(self.decision_time_ms)
        timestamp(self.valid_until_ms)
        if self.book_time_ms is not None:
            timestamp(self.book_time_ms)
        if self.valid_until_ms <= self.decision_time_ms:
            raise ValueError("decision validity must be positive")
        if len(self.input_hash) != 64 or any(c not in "0123456789abcdef" for c in self.input_hash):
            raise ValueError("invalid input hash")


def make_context(*, symbol, account_id, strategy_id, strategy_version, signal_id,
                 decision_time_ms, features, inputs, parameter_version, ttl_ms=5000,
                 signal_expires_at=None) -> DecisionContext:
    if type(ttl_ms) is not int or not 1 <= ttl_ms <= 60_000:
        raise ValueError("TTL must be 1..60000ms")
    if features.get("symbol") != symbol:
        raise ValueError("feature symbol mismatch")
    expiry = decision_time_ms + ttl_ms
    if signal_expires_at is not None:
        expiry = min(expiry, timestamp(signal_expires_at))
    payload = {"symbol": symbol, "account_id": account_id, "strategy_id": strategy_id,
               "strategy_version": strategy_version, "signal_id": signal_id,
               "decision_time_ms": decision_time_ms, "features": features,
               "inputs": inputs, "parameter_version": parameter_version, "valid_until_ms": expiry}
    return DecisionContext(symbol, account_id, strategy_id, strategy_version, signal_id,
                           decision_time_ms, features.get("exchange_time_ms"), expiry, canonical_hash(payload))


def bind_context(context, inputs, parameter_version):
    """Each computation binds its complete inputs, not merely a caller's hash."""
    return replace(context, input_hash=canonical_hash({"parent_context": context,
                   "inputs": inputs, "parameter_version": parameter_version}))


@dataclass(frozen=True)
class Evidence:
    symbol: str
    account_id: str
    sample_count: int
    out_of_sample: bool
    source_hash: str
    independently_reviewed: bool = False

    def sufficient(self, context, minimum=100):
        return (self.symbol == context.symbol and self.account_id == context.account_id
                and type(self.sample_count) is int and self.sample_count >= minimum
                and self.out_of_sample is True and self.independently_reviewed is True
                and len(self.source_hash) == 64
                and all(c in "0123456789abcdef" for c in self.source_hash))


@dataclass(frozen=True, kw_only=True)
class DecisionBase:
    context: DecisionContext
    status: str
    parameter_version: str
    reasons: tuple[str, ...] = ()
    live_authorized: bool = field(default=False, init=False)

    def asdict(self):
        return json_safe(self)


@dataclass(frozen=True, kw_only=True)
class PricingDecision(DecisionBase):
    mode: str = "REJECT"
    limit_price: Decimal | None = None
    max_buy_price: Decimal | None = None
    quantity: Decimal = ZERO
    notional: Decimal = ZERO
    fee_estimate: Decimal = ZERO
    risk_estimate: Decimal = ZERO


@dataclass(frozen=True, kw_only=True)
class ExitDecision(DecisionBase):
    mode: str = "REJECT"
    limit_price: Decimal | None = None
    min_sell_price: Decimal | None = None
    quantity: Decimal = ZERO
    fee_estimate: Decimal = ZERO
    risk_estimate: Decimal = ZERO


@dataclass(frozen=True, kw_only=True)
class ExecutionEstimate(DecisionBase):
    no_fill: Decimal = ZERO
    partial_fill: Decimal = ZERO
    full_fill: Decimal = ZERO
    timeout: Decimal = ZERO
    cancelled: Decimal = ZERO
    adverse_fill: Decimal = ZERO
    expected_fill_fraction: Decimal = ZERO
    limit_price: Decimal | None = None
    quantity: Decimal = ZERO
    fee_estimate: Decimal = ZERO
    risk_estimate: Decimal = ZERO
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PositionSizingDecision(DecisionBase):
    limit_price: Decimal | None = None
    quantity: Decimal = ZERO
    notional: Decimal = ZERO
    fee_estimate: Decimal = ZERO
    risk_estimate: Decimal = ZERO
    score_notional: Decimal = ZERO
    risk_notional: Decimal = ZERO
    available_notional: Decimal = ZERO


@dataclass(frozen=True, kw_only=True)
class ExpectedReturnDecision(DecisionBase):
    expected_return_fraction: Decimal | None = None
    expected_net_quote: Decimal | None = None
    fill_adjusted_net_quote: Decimal | None = None
    quantity: Decimal = ZERO
    fee_estimate: Decimal = ZERO
    risk_estimate: Decimal = ZERO
    assumptions: tuple[str, ...] = ()
    notional: Decimal = ZERO
    cost_input_hash: str | None = None


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_price: Decimal
    max_price: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    max_notional: Decimal = ZERO
    percent_filters: tuple[tuple[str, str, str, str, str, int], ...] = ()

    def __post_init__(self):
        if self.symbol not in ACCOUNTS:
            raise ValueError("unsupported rules symbol")
        for name in ("tick_size", "step_size", "min_price", "max_price", "min_qty", "max_qty", "min_notional", "max_notional"):
            positive = name in ("tick_size", "step_size", "min_qty", "max_qty", "min_notional")
            object.__setattr__(self, name, D(getattr(self, name), name, positive=positive, nonnegative=True))
        if self.max_price and self.max_price < self.min_price or self.max_qty < self.min_qty or self.max_notional and self.max_notional < self.min_notional:
            raise ValueError("inverted exchange filter bounds")
        for kind, bid_low, bid_high, ask_low, ask_high, minutes in self.percent_filters:
            if kind not in ("BOTH", "SIDE") or type(minutes) is not int or minutes < 0:
                raise ValueError("invalid percent filter")
            if not D(bid_low, positive=True) <= D(bid_high, positive=True) or not D(ask_low, positive=True) <= D(ask_high, positive=True):
                raise ValueError("inverted percent filter bounds")

    @classmethod
    def from_exchange_info(cls, raw, symbol=None):
        if "symbols" in raw:
            items = [row for row in raw["symbols"] if row["symbol"] == symbol]
            if len(items) != 1:
                raise ValueError("symbol absent or duplicated in exchangeInfo")
            raw = items[0]
        actual_symbol = raw.get("symbol")
        if actual_symbol not in ACCOUNTS or symbol is not None and actual_symbol != symbol:
            raise ValueError("exchange rules symbol mismatch")
        if raw.get("status") != "TRADING" or raw.get("isSpotTradingAllowed") is not True:
            raise ValueError("symbol not enabled for Spot trading")
        filters = {item["filterType"]: item for item in raw.get("filters", [])}
        try:
            price, lot = filters["PRICE_FILTER"], filters["LOT_SIZE"]
            minimums = [D(filters[key]["minNotional"], positive=True)
                        for key in ("MIN_NOTIONAL", "NOTIONAL") if key in filters]
            if not minimums:
                raise ValueError("notional rules missing")
            percentages = []
            if "PERCENT_PRICE" in filters:
                f = filters["PERCENT_PRICE"]
                percentages.append(("BOTH", f["multiplierDown"], f["multiplierUp"],
                                    f["multiplierDown"], f["multiplierUp"], int(f["avgPriceMins"])))
            if "PERCENT_PRICE_BY_SIDE" in filters:
                f = filters["PERCENT_PRICE_BY_SIDE"]
                percentages.append(("SIDE", f["bidMultiplierDown"], f["bidMultiplierUp"],
                                    f["askMultiplierDown"], f["askMultiplierUp"], int(f["avgPriceMins"])))
            return cls(actual_symbol, D(price["tickSize"], positive=True), D(lot["stepSize"], positive=True),
                       D(price["minPrice"], nonnegative=True), D(price["maxPrice"], nonnegative=True),
                       D(lot["minQty"], positive=True), D(lot["maxQty"], positive=True), max(minimums),
                       D(filters.get("NOTIONAL", {}).get("maxNotional", "0"), nonnegative=True), tuple(percentages))
        except (KeyError, TypeError) as exc:
            raise ValueError("incomplete exchange filters") from exc

    def validate_order(self, price, quantity, side="BUY", *, reference_price=None,
                       reference_minutes=None) -> tuple[str, ...]:
        price, quantity = D(price, positive=True), D(quantity, positive=True)
        reasons = []
        if side not in ("BUY", "SELL"):
            raise ValueError("invalid side")
        if price % self.tick_size or self.min_price and price < self.min_price or self.max_price and price > self.max_price:
            reasons.append("PRICE_FILTER")
        if quantity % self.step_size or not self.min_qty <= quantity <= self.max_qty:
            reasons.append("LOT_SIZE")
        notional = price * quantity
        if notional < self.min_notional or self.max_notional and notional > self.max_notional:
            reasons.append("NOTIONAL_FILTER")
        for _, bid_low, bid_high, ask_low, ask_high, minutes in self.percent_filters:
            if reference_price is None or reference_minutes != minutes:
                reasons.append("PERCENT_PRICE_REFERENCE_MISSING")
                continue
            reference = D(reference_price, positive=True)
            low, high = (bid_low, bid_high) if side == "BUY" else (ask_low, ask_high)
            if not reference * D(low, positive=True) <= price <= reference * D(high, positive=True):
                reasons.append("PERCENT_PRICE_FILTER")
        return tuple(sorted(set(reasons)))


def feature_reasons(context, features, max_age_ms=2000) -> tuple[str, ...]:
    reasons = []
    if features.get("symbol") != context.symbol:
        reasons.append("SYMBOL_MISMATCH")
    if features.get("ready") is not True or features.get("synced") is not True:
        reasons.append("BOOK_NOT_READY")
    for name in ("exchange_time_ms", "received_time_ms"):
        value = features.get(name)
        if type(value) is not int or not 0 <= context.decision_time_ms - value <= max_age_ms:
            reasons.append("STALE_OR_FUTURE_BOOK")
    if context.book_time_ms != features.get("exchange_time_ms"):
        reasons.append("CONTEXT_BOOK_TIME_MISMATCH")
    try:
        bid, ask = D(features["bid"], positive=True), D(features["ask"], positive=True)
        D(features["bid_qty"], positive=True)
        D(features["ask_qty"], positive=True)
        if bid >= ask:
            reasons.append("CROSSED_BOOK")
    except (ValueError, KeyError):
        reasons.append("INVALID_BOOK")
    return tuple(sorted(set(reasons)))


def validate_related_context(context, other):
    for name in ("symbol", "account_id", "strategy_id", "strategy_version", "signal_id"):
        if getattr(context, name) != getattr(other, name):
            raise ValueError("evidence context belongs to another decision")
    if other.decision_time_ms > context.decision_time_ms:
        return ("EVIDENCE_FROM_FUTURE",)
    if other.valid_until_ms <= context.decision_time_ms:
        return ("EVIDENCE_EXPIRED",)
    return ()
