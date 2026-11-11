from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskContract:
    initial_capital: Decimal = Decimal("2000")
    minimum_notional: Decimal = Decimal("300")
    maximum_notional: Decimal = Decimal("700")
    maximum_slots: int = 2
    maximum_drawdown: Decimal = Decimal("0.05")
    initial_stop_loss: Decimal = Decimal("0.008")
    profit_protection_trigger: Decimal = Decimal("0.01")
    apts_trigger: Decimal = Decimal("0.015")
    bnb_refill_below_usdt: Decimal = Decimal("3")
    bnb_refill_target_usdt: Decimal = Decimal("10")
    bnb_refill_cap_usdt: Decimal = Decimal("20")

    def __post_init__(self):
        expected = ("2000", "300", "700", 2, "0.05", "0.008", "0.01", "0.015", "3", "10", "20")
        for (name, field), value in zip(self.__dataclass_fields__.items(), expected):
            required = value if isinstance(value, int) else Decimal(value)
            if getattr(self, name) != required:
                raise ValueError(f"P2 risk contract cannot override {name}")


@dataclass(frozen=True)
class APTSParameters:
    """Inputs reserved for P3/P4; no fabricated adaptive stop implementation."""
    initial_stop_loss: Decimal = Decimal("0.008")
    profit_protection_trigger: Decimal = Decimal("0.01")
    adaptive_trigger: Decimal = Decimal("0.015")
    implementation_status: str = "NOT_IMPLEMENTED"

    def __post_init__(self):
        if (self.initial_stop_loss, self.profit_protection_trigger, self.adaptive_trigger,
                self.implementation_status) != (Decimal("0.008"), Decimal("0.01"), Decimal("0.015"), "NOT_IMPLEMENTED"):
            raise ValueError("APTS thresholds are fixed; its mathematics is not implemented in P2")


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    code: str
    reasons: tuple[str, ...]


class Guardian:
    """Fail-closed contract, never an ALLOW placeholder."""
    def authorize_live(self, request) -> RiskDecision:
        if getattr(request, "strategy_id", None) == "R1":
            return RiskDecision(False, "DENY_R1_NOT_APPROVED", ("R1 has no independent live approval",))
        return RiskDecision(False, "DENY_NOT_IMPLEMENTED", (
            "Guardian, private account adapter, real order state machine and protective orders are not accepted",
        ))
