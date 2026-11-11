"""Strict decimal inputs, exchange increments, and reproducible audit hashes."""
from dataclasses import fields, is_dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from enum import Enum
from hashlib import sha256
import json
from typing import Mapping

ZERO = Decimal("0")
ONE = Decimal("1")


def D(value, name="value", *, positive=False, nonnegative=False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{name} must be Decimal, decimal string, or integer; floats prohibited")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if not result.is_finite() or (result and not -24 <= result.adjusted() <= 24):
        raise ValueError(f"nonfinite or unbounded {name}")
    if positive and result <= 0 or nonnegative and result < 0:
        raise ValueError(f"invalid sign for {name}")
    return result


def floor_step(value, step) -> Decimal:
    value, step = D(value), D(step, "step", positive=True)
    with localcontext() as context:
        context.prec = 60
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_step(value, step) -> Decimal:
    value, step = D(value), D(step, "step", positive=True)
    with localcontext() as context:
        context.prec = 60
        return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def clip(value, lower=ZERO, upper=ONE) -> Decimal:
    value, lower, upper = D(value), D(lower), D(upper)
    if lower > upper:
        raise ValueError("invalid clipping bounds")
    return max(lower, min(upper, value))


def json_safe(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite decimal cannot be serialized")
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: json_safe(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, float):
        raise ValueError("float inputs prohibited in audited P3 mathematics")
    return value


def canonical_hash(value) -> str:
    return sha256(json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()
