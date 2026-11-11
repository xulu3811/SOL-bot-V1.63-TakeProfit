"""P2 APTSParameters is a frozen historical contract, not P3 capability metadata."""
from dataclasses import dataclass
from .contracts import APTSParameters
from solbot.mathematics.apts_exit import APTSState, update_protection

@dataclass(frozen=True)
class APTSCapability:
    mathematics: str='P3_IMPLEMENTED'
    native_protection: str='P5_NOT_ACCEPTED'
    adaptive_boundary: str='STRICTLY_GREATER_THAN_0.015'
    live_authorized: bool=False

def resolve_legacy(parameters):
    if not isinstance(parameters,APTSParameters): raise ValueError('P2 contract required')
    return APTSCapability()

