"""Versioned, non-configurable production risk bounds (P5 execution remains gated)."""
from dataclasses import dataclass
from decimal import Decimal

VERSION = 'guardian-p4-1'
CAPITAL = Decimal('2000')
MAX_DRAWDOWN = Decimal('.05')
MIN_ORDER = Decimal('300')
MAX_ORDER = Decimal('700')
MAX_PRINCIPAL = Decimal('1400')
MAX_SLOTS = 2
T1_RISK = Decimal('.004')
R1_RISK = Decimal('.0025')
TOTAL_RISK = Decimal('.008')
FEE = Decimal('.001')
SLIPPAGE = Decimal('.001')
STRATEGY_VERSION = 'p2-research-1'

@dataclass(frozen=True)
class Readiness:
    """Evidence model only: booleans can never open the P4 execution gate."""
    account: bool = False
    orders: bool = False
    positions: bool = False
    protection: bool = False
    connection: bool = False
    executor: bool = False

    @property
    def complete(self):
        return all(v is True for v in vars(self).values())

