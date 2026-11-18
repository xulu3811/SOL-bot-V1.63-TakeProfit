"""Canonical live-aligned entry pricing, risk sizing, and strategy gating policy.

Both SOL-bot Live (``AccountVault``) and Shadow-bot executors (P3 & R2) import
from this module so live and shadow execution share identical formulas,
thresholds, slippage assumptions, and stop-distance bounds.
"""

from __future__ import annotations

from decimal import Decimal
import os
from typing import Any

from solbot.mathematics.decimal_utils import D, ceil_step, floor_step
from solbot.strategy import t2_swing


def _dec(value: Decimal | str | int | float, name: str = "value") -> Decimal:
    """Parse numeric policy input strictly into Decimal without float drift."""
    if isinstance(value, float):
        return D(str(value), name)
    return D(value, name)

DEFAULT_FEE_RATE = Decimal("0.00075")
DEFAULT_SLIPPAGE_RATE = Decimal("0.00075")
CROSS_STRATEGY_COOLDOWN_MS = 900_000
LIVE_MIN_ORDER_USDT = Decimal("500")
LIVE_MAX_ORDER_USDT = Decimal("1000")
LIVE_MIN_STOP_DISTANCE = Decimal("0.005")
LIVE_MAX_STOP_DISTANCE = Decimal("0.050")
DEFAULT_APTS_STOP_DISTANCE = Decimal("0.010")
R2_RISK_BUDGET_RATIO = Decimal("0.010")
R2_EXTRA_SLIPPAGE_BUFFER = Decimal("0.002")
R2_CUSTOM_EQUITY_MULTIPLIER = Decimal("2.5")
T2_CUSTOM_MIN_EQUITY = Decimal("5000")
BREAKOUT_TAKER_BUFFER_RATE = Decimal("0.0005")
BREAKOUT_TAKER_MIN_TICKS = Decimal("2")
BNB_MIN_RESERVE_USDT = Decimal("5.00")
BNB_REFILL_TARGET_USDT = Decimal("5.05")
BNB_MAX_HOLDING_USDT = Decimal("20.00")
BNB_REFILL_COOLDOWN_MS = 60_000


def parse_allowed_strategies(
    raw: str | None = None,
    default: tuple[str, ...] = ("T1",),
) -> tuple[str, ...]:
    """Parse a comma-separated strategy allowlist (e.g. ``T1,T2,R2``)."""
    source = os.getenv("SOLBOT_LIVE_STRATEGIES") if raw is None else raw
    parsed = tuple(
        item.strip().upper()
        for item in str(source or "").split(",")
        if item.strip()
    )
    return parsed or default


def is_custom_trading_config(
    cfg: dict[str, Any] | None,
    min_order_usdt: Decimal | str | int | float,
    max_order_usdt: Decimal | str | int | float,
) -> bool:
    """Return True when user customized sizing beyond the $500-$1000 baseline."""
    if not isinstance(cfg, dict) or not cfg.get("custom"):
        return False
    return (
        _dec(min_order_usdt) != LIVE_MIN_ORDER_USDT
        or _dec(max_order_usdt) != LIVE_MAX_ORDER_USDT
    )


def strategy_entry_allowed(
    strategy_id: str,
    regime: str | None = None,
    allowed: tuple[str, ...] | None = None,
) -> tuple[bool, str | None]:
    """Enforce live strategy allowlist and regime filters."""
    sid = str(strategy_id or "T1").upper()
    allow_set = allowed if allowed is not None else parse_allowed_strategies()
    if sid not in allow_set:
        return False, "STRATEGY_NOT_ALLOWED"
    if sid == "T1" and str(regime or "").upper() == "DOWNTREND":
        return False, "T1_BLOCKED_IN_DOWNTREND"
    return True, None


def compute_entry_limit_price(
    strategy_id: str,
    entry_low: Decimal | str | float | None,
    entry_high: Decimal | str | float | None,
    bid: Decimal | str | float | None,
    ask: Decimal | str | float | None,
    tick_size: Decimal | str | float,
) -> tuple[Decimal | None, str | None]:
    """Compute the live canonical limit buy price, rejecting chased T2/R2 entries."""
    sid = str(strategy_id or "T1").upper()
    tick = _dec(tick_size)
    low_dec = _dec(entry_low) if entry_low not in (None, "") else Decimal("0")
    high_dec = _dec(entry_high) if entry_high not in (None, "") else Decimal("0")
    bid_dec = _dec(bid) if bid not in (None, "") else Decimal("0")
    ask_dec = _dec(ask) if ask not in (None, "") else Decimal("0")

    if sid in {"T2", "R2"}:
        if high_dec > 0 and bid_dec > high_dec:
            return None, "CHASE_EXCEEDED"
        if ask_dec > 0:
            buf = max(
                ask_dec * BREAKOUT_TAKER_BUFFER_RATE,
                tick * BREAKOUT_TAKER_MIN_TICKS if tick > 0 else Decimal("0"),
            )
            buffered_ask = ceil_step(ask_dec + buf, tick) if tick > 0 else (ask_dec + buf)
            raw_price = min(high_dec, buffered_ask) if high_dec > 0 else buffered_ask
        else:
            raw_price = high_dec
    else:
        limit_ref = low_dec if low_dec > 0 else high_dec
        if limit_ref > 0 and bid_dec > 0:
            raw_price = min(limit_ref, bid_dec)
        elif limit_ref > 0:
            raw_price = limit_ref
        else:
            raw_price = bid_dec

    if raw_price <= 0:
        return None, "INVALID_PRICE"
    price = floor_step(raw_price, tick) if tick > 0 else raw_price
    if price <= 0:
        return None, "INVALID_PRICE"
    return price, None


def resolve_stop_distance(
    payload: dict[str, Any],
    entry_ref: Decimal | str | float | None = None,
) -> Decimal | None:
    """Resolve fractional stop distance from ``stop_distance`` or ``stop_price``."""
    raw_dist = payload.get("stop_distance")
    if raw_dist not in (None, "", "None"):
        dist = _dec(raw_dist)
        if dist > 0:
            return dist
    raw_stop = payload.get("stop_price")
    ref_raw = entry_ref if entry_ref not in (None, "") else payload.get("entry_low")
    if raw_stop not in (None, "", "None") and ref_raw not in (None, "", "None"):
        ref_dec = _dec(ref_raw)
        stop_dec = _dec(raw_stop)
        if ref_dec > 0 and Decimal("0") < stop_dec < ref_dec:
            return (ref_dec - stop_dec) / ref_dec
    return None


def score_span(score: Decimal | str | float | int | None) -> Decimal:
    """Normalize strategy score in [75, 100] onto [0, 1]."""
    score_dec = _dec(score if score not in (None, "") else "75")
    return min(Decimal("1"), max(Decimal("0"), (score_dec - Decimal("75")) / Decimal("25")))


def r2_risk_notional(
    equity: Decimal | str | float | int,
    stop_distance: Decimal | str | float | int | None,
    fee_rate: Decimal | str | float = DEFAULT_FEE_RATE,
    score: Decimal | str | float | int | None = "75",
    *,
    min_order_usdt: Decimal | str | float | int = LIVE_MIN_ORDER_USDT,
    max_order_usdt: Decimal | str | float | int = LIVE_MAX_ORDER_USDT,
    is_custom_cfg: bool = False,
) -> Decimal | None:
    """Canonical R2 risk-budgeted order notional matching ``AccountVault._r2_notional``."""
    if stop_distance in (None, "", "None"):
        return None
    eq = _dec(equity)
    min_u = _dec(min_order_usdt)
    max_u = _dec(max_order_usdt)
    eff_eq = max(eq, max_u * R2_CUSTOM_EQUITY_MULTIPLIER) if is_custom_cfg else eq
    stop = _dec(stop_distance)
    fee = _dec(fee_rate)
    if (
        eff_eq <= 0
        or stop < LIVE_MIN_STOP_DISTANCE
        or stop > LIVE_MAX_STOP_DISTANCE
        or fee < 0
        or fee > Decimal("0.01")
    ):
        return None
    loss = stop + Decimal("2") * fee + R2_EXTRA_SLIPPAGE_BUFFER
    score_cap = min_u + score_span(score) * (max_u - min_u)
    risk_cap = eff_eq * R2_RISK_BUDGET_RATIO / loss
    if risk_cap < min_u:
        return None
    return min(max_u, score_cap, risk_cap)


def t2_risk_notional(
    equity: Decimal | str | float | int,
    stop_distance: Decimal | str | float | int | None,
    fee_rate: Decimal | str | float = DEFAULT_FEE_RATE,
    score: Decimal | str | float | int | None = "75",
    *,
    min_order_usdt: Decimal | str | float | int = LIVE_MIN_ORDER_USDT,
    max_order_usdt: Decimal | str | float | int = LIVE_MAX_ORDER_USDT,
    is_custom_cfg: bool = False,
) -> Decimal | None:
    """Canonical T2 risk-budgeted order notional matching ``AccountVault.consider_entry``."""
    if stop_distance in (None, "", "None"):
        return None
    eq = _dec(equity)
    min_u = _dec(min_order_usdt)
    max_u = _dec(max_order_usdt)
    eff_eq = max(eq, T2_CUSTOM_MIN_EQUITY) if is_custom_cfg else eq
    r_notional = t2_swing.risk_notional(eff_eq, stop_distance, fee_rate, score)
    if r_notional is None:
        return None
    if is_custom_cfg:
        return min_u + score_span(score) * (max_u - min_u)
    return r_notional


def t1_target_notional(
    score: Decimal | str | float | int | None = "75",
    *,
    min_order_usdt: Decimal | str | float | int = LIVE_MIN_ORDER_USDT,
    max_order_usdt: Decimal | str | float | int = LIVE_MAX_ORDER_USDT,
) -> Decimal:
    """Canonical T1 linear score-scaled target notional."""
    min_u = _dec(min_order_usdt)
    max_u = _dec(max_order_usdt)
    return min_u + score_span(score) * (max_u - min_u)


def compute_live_order_size(
    strategy_id: str,
    price: Decimal | str | float,
    free_quote: Decimal | str | float,
    equity: Decimal | str | float,
    stop_distance: Decimal | str | float | None,
    *,
    fee_rate: Decimal | str | float = DEFAULT_FEE_RATE,
    score: Decimal | str | float | int | None = "75",
    min_order_usdt: Decimal | str | float | int = LIVE_MIN_ORDER_USDT,
    max_order_usdt: Decimal | str | float | int = LIVE_MAX_ORDER_USDT,
    is_custom_cfg: bool = False,
    step_size: Decimal | str | float = "0.001",
    min_qty: Decimal | str | float = "0",
    rules_min_notional: Decimal | str | float = "5",
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str | None]:
    """Compute final (quantity, order_notional, budget_notional, reject_reason)."""
    sid = str(strategy_id or "T1").upper()
    min_u = _dec(min_order_usdt)
    max_u = _dec(max_order_usdt)
    free = _dec(free_quote)
    price_dec = _dec(price)
    eff_min_u = (
        min(min_u, free)
        if (min_u > LIVE_MIN_ORDER_USDT and free >= LIVE_MIN_ORDER_USDT and free >= min_u * Decimal("0.90"))
        else min_u
    )

    if sid == "T2":
        target = t2_risk_notional(
            equity,
            stop_distance,
            fee_rate,
            score,
            min_order_usdt=eff_min_u,
            max_order_usdt=max_u,
            is_custom_cfg=is_custom_cfg,
        )
        if target is None:
            return None, None, None, "T2_SIZE"
    elif sid == "R2":
        target = r2_risk_notional(
            equity,
            stop_distance,
            fee_rate,
            score,
            min_order_usdt=eff_min_u,
            max_order_usdt=max_u,
            is_custom_cfg=is_custom_cfg,
        )
        if target is None:
            return None, None, None, "R2_SIZE"
    else:
        target = t1_target_notional(
            score,
            min_order_usdt=eff_min_u,
            max_order_usdt=max_u,
        )

    notional = min(max_u, max(eff_min_u, target), free)
    if notional < eff_min_u:
        return (
            None,
            None,
            None,
            "USDT_BELOW_500" if min_u == LIVE_MIN_ORDER_USDT else "USDT_BELOW_MIN",
        )

    step = _dec(step_size)
    min_qty_dec = _dec(min_qty or "0")
    qty = floor_step(notional / price_dec, step)
    order_notional = qty * price_dec
    min_notional = max(eff_min_u - price_dec * step, _dec(rules_min_notional or "5"))
    if qty <= 0 or qty < min_qty_dec or order_notional < min_notional:
        return None, None, None, "BELOW_MIN_NOTIONAL"
    return qty, order_notional, notional, None


def resolve_live_apts_stop_distance(
    stop_distance: Decimal | str | float | None,
) -> Decimal:
    """Resolve canonical APTS initial stop distance bounded to [0.005, 0.050]."""
    if stop_distance in (None, "", "None"):
        return DEFAULT_APTS_STOP_DISTANCE
    dist = _dec(stop_distance)
    if dist < LIVE_MIN_STOP_DISTANCE or dist > LIVE_MAX_STOP_DISTANCE:
        return DEFAULT_APTS_STOP_DISTANCE
    return dist


def compute_bnb_refill_order(
    bnb_total: Decimal | str | float,
    ask_price: Decimal | str | float,
    free_usdt: Decimal | str | float,
    *,
    reserved_usdt: Decimal | str | float = "0",
    step_size: Decimal | str | float = "0.001",
    min_qty: Decimal | str | float = "0.001",
    tick_size: Decimal | str | float = "0.01",
    rules_min_notional: Decimal | str | float = "5",
    min_threshold_usdt: Decimal | str | float = BNB_MIN_RESERVE_USDT,
    target_refill_usdt: Decimal | str | float = BNB_REFILL_TARGET_USDT,
    max_holding_usdt: Decimal | str | float = BNB_MAX_HOLDING_USDT,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str | None]:
    """Compute (qty, limit_price, est_notional, skip_reason) for BNB fee top-up.

    Triggers only when total BNB spot value is strictly below ``min_threshold_usdt``
    (5 USDT), sizes the order to clear ``rules_min_notional`` (~5.05 USDT), and
    strictly enforces ``bnb_value + est_notional <= max_holding_usdt`` (20 USDT)
    without encroaching on ``reserved_usdt`` needed for the primary SOL order.
    """
    bnb_dec = max(Decimal("0"), _dec(bnb_total, "bnb_total"))
    ask = _dec(ask_price, "ask_price")
    free_u = max(Decimal("0"), _dec(free_usdt, "free_usdt"))
    reserved_u = max(Decimal("0"), _dec(reserved_usdt or "0", "reserved_usdt"))
    step = _dec(step_size, "step_size")
    min_q = max(Decimal("0"), _dec(min_qty or "0", "min_qty"))
    tick = _dec(tick_size, "tick_size")
    min_notional = max(Decimal("0"), _dec(rules_min_notional or "0", "rules_min_notional"))
    min_thresh = _dec(min_threshold_usdt, "min_threshold_usdt")
    target_u = _dec(target_refill_usdt, "target_refill_usdt")
    max_hold = _dec(max_holding_usdt, "max_holding_usdt")

    if ask <= 0 or step <= 0 or tick <= 0:
        return None, None, None, "BNB_INVALID_MARKET"

    bnb_value = bnb_dec * ask
    if bnb_value >= min_thresh:
        return None, None, None, "BNB_SUFFICIENT"

    limit_price = ceil_step(ask + tick, tick)
    effective_target = max(target_u, min_notional + Decimal("0.05") if min_notional > 0 else target_u)
    qty = max(min_q, ceil_step(effective_target / ask, step))
    est_notional = qty * limit_price

    if bnb_value + est_notional > max_hold:
        headroom = max_hold - bnb_value
        capped_qty = floor_step(headroom / limit_price, step)
        if capped_qty < min_q or capped_qty <= 0 or capped_qty * ask < min_notional:
            return None, None, None, "BNB_MAX_CAP_EXCEEDED"
        qty = capped_qty
        est_notional = qty * limit_price

    if qty <= 0 or qty < min_q or qty * ask < min_notional:
        return None, None, None, "BNB_BELOW_MIN_NOTIONAL"

    available = free_u - reserved_u
    if available < est_notional:
        return None, None, None, "USDT_INSUFFICIENT_FOR_BNB"
    return qty, limit_price, est_notional, None
