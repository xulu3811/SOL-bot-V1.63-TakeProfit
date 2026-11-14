"""Typed requests and results shared by isolated execution channels."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re

SYMBOLS = frozenset({"SOLUSDT", "SUIUSDT"})


def amount(value, name="amount", *, zero=False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError(f"{name} must be a decimal string")
    text = str(value)
    if len(text) > 64 or text.strip() != text:
        raise ValueError(f"invalid {name}")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {name}") from exc
    if not result.is_finite() or result < 0 or (not zero and result == 0) or (result and not -24 <= result.adjusted() <= 24):
        raise ValueError(f"invalid {name}")
    return result


def identifier(value, name="identifier") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ValueError(f"invalid {name}")
    return value


def timestamp(value) -> int:
    if type(value) is not int or not 1_000_000_000_000 <= value < 4_102_444_800_000:
        raise ValueError("timestamp must be UTC milliseconds")
    return value


class Mode(StrEnum):
    LIVE = "LIVE"
    SHADOW = "SHADOW"


class LiveState(StrEnum):
    LIVE_OFF = "LIVE_OFF"
    LIVE_PENDING = "LIVE_PENDING"
    LIVE_ON = "LIVE_ON"
    PAUSED = "PAUSED"
    HARD_LOCKED = "HARD_LOCKED"


@dataclass(frozen=True)
class OrderRequest:
    mode: Mode
    account_id: str
    symbol: str
    strategy_id: str
    request_id: str
    side: str
    limit_price: str
    notional: str | None = None
    position_id: str | None = None

    def __post_init__(self):
        if self.mode not in (Mode.LIVE, Mode.SHADOW) or self.symbol not in SYMBOLS:
            raise ValueError("unsupported execution mode or symbol")
        identifier(self.account_id, "account_id")
        identifier(self.request_id, "request_id")
        if self.strategy_id not in ("T1", "R1", "T2") or self.side not in ("BUY", "SELL"):
            raise ValueError("unsupported strategy or side")
        amount(self.limit_price, "limit_price")
        if self.side == "BUY":
            amount(self.notional, "notional")
        elif self.position_id is None:
            raise ValueError("sell requires a position_id")


@dataclass(frozen=True)
class AuthorizationRequest:
    actor: str
    request_id: str
    authenticated: bool

    def __post_init__(self):
        identifier(self.actor, "actor")
        identifier(self.request_id, "request_id")
        if type(self.authenticated) is not bool:
            raise ValueError("authenticated must be boolean")


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    code: str
    reasons: tuple[str, ...]
    state: str | None = None
    order_id: str | None = None

    def asdict(self):
        return asdict(self)
