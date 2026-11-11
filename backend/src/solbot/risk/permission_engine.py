"""Complete immutable intent; internal planning ALLOW is never an execution token."""
from dataclasses import dataclass
from solbot.mathematics.decimal_utils import D
from solbot.execution.models import identifier

@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    signal_id: str
    account_id: str
    symbol: str
    strategy_id: str
    strategy_version: str
    price: str
    quantity: str
    stop_price: str
    created_at: int
    expires_at: int
    market_at: int
    math_at: int
    score: str='100'
    side: str='BUY'

    def __post_init__(self):
        for name in ('intent_id','signal_id','account_id','symbol','strategy_id','strategy_version'): identifier(getattr(self,name))
        for name in ('price','quantity','stop_price'): D(getattr(self,name),positive=True)
        D(self.score)
        if any(type(getattr(self,n)) is not int for n in ('created_at','expires_at','market_at','math_at')): raise ValueError('integer timestamps required')

@dataclass(frozen=True)
class Permission:
    decision: str
    code: str
    intent_hash: str
    policy_version: str
    expires_at: int
    live_authorized: bool=False

