"""Explicit dispatch; shadow rejection can never fall back to live execution."""
from .models import ExecutionResult, Mode, OrderRequest


class ExecutionRouter:
    def __init__(self, shadow, *, live=None):
        self.shadow = shadow
        self.live = live

    def execute(self, request: OrderRequest, *, candidate=None, now_ms):
        if not isinstance(request, OrderRequest):
            raise ValueError("typed order request required")
        if request.mode == Mode.SHADOW:
            if request.account_id != self.shadow.account_id or request.symbol != self.shadow.symbol:
                return ExecutionResult(False, "DENY_CHANNEL_ISOLATION", ("shadow account and symbol must match",))
            if request.side == "BUY":
                if not isinstance(candidate, dict) or request.strategy_id != candidate.get("strategy_id"):
                    return ExecutionResult(False, "DENY_SIGNAL", ("request needs a matching strategy candidate",))
                return self.shadow.submit_candidate(candidate, notional=request.notional, limit_price=request.limit_price, now_ms=now_ms)
            return self.shadow.submit_sell(request.position_id, request.limit_price, now_ms=now_ms, request_id=request.request_id)
        if self.live is None:
            return ExecutionResult(False, "DENY_LIVE_UNAVAILABLE", ("no live executor is configured",))
        return self.live.execute(request)
