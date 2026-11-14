"""Normalized reader for the independent 1-second SOLUSDT microstructure status file."""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_MICRO_STATUS_PATH = Path(
    os.environ.get("SOLBOT_MICRO_STATUS_PATH", "/var/lib/sol-bot/sol-micro-status.json")
)


def _str_decimal(value, default: str | None = None) -> str | None:
    if value is None:
        return default
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    if not dec.is_finite():
        return default
    return format(dec, "f")


def read_micro_snapshot(
    path: str | Path | None = None,
    *,
    now_ms: int | None = None,
    max_age_ms: int = 3000,
    symbol: str = "SOLUSDT",
) -> dict | None:
    """Return fresh, JSON-validated microstructure fields or None when absent/stale."""
    target = Path(path) if path is not None else DEFAULT_MICRO_STATUS_PATH
    try:
        raw_text = target.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("symbol") != symbol:
        return None
    if not payload.get("ready"):
        return None
    now = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
    ts = payload.get("feature_generated_time_ms") or payload.get("received_time_ms")
    if ts is None:
        return None
    try:
        age = now - int(ts)
    except (TypeError, ValueError):
        return None
    if age > max_age_ms or age < -2000:
        return None
    bid = _str_decimal(payload.get("bid"))
    ask = _str_decimal(payload.get("ask"))
    if bid is None or Decimal(bid) <= 0:
        return None
    return {
        "symbol": symbol,
        "ready": True,
        "bid": bid,
        "ask": ask,
        "buy_ratio": _str_decimal(payload.get("buy_ratio"), "0.5"),
        "imbalance": _str_decimal(payload.get("imbalance"), "0"),
        "spread_bps": _str_decimal(payload.get("spread_bps")),
        "short_volatility": _str_decimal(payload.get("short_volatility")),
        "exchange_time_ms": int(payload.get("exchange_time_ms") or ts),
        "received_time_ms": int(payload.get("received_time_ms") or ts),
    }
