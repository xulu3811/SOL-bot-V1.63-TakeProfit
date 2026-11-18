"""Persistent four-stage research protection with irreversible trigger/stop latches."""
from dataclasses import dataclass,replace,field
from decimal import Decimal
from .contracts import DecisionContext,DecisionBase,bind_context
from .decimal_utils import D,ceil_step,json_safe
from .protective_stop import initial_stop,profit_floor,stop_limit_price
from .trend_exhaustion import TrendEvidence,classify
from .volatility_regime import trailing_distance

S1_PROFIT_PROTECTION_TRIGGER = Decimal('.008')
S1_DYNAMIC_LOCK_TRIGGER = Decimal('.011')
S2_ADAPTIVE_TRAILING_TRIGGER = Decimal('.012')
S2_EXHAUSTION_TIGHTEN_TRIGGER = Decimal('.020')


def _overbought_exhaustion(evidence: TrendEvidence) -> bool:
    """Return True when RSI reached >= 80 and flow/momentum visibly rolls over."""
    peak_rsi = max(evidence.rsi, evidence.previous_rsi)
    if peak_rsi < Decimal('80'):
        return False
    if evidence.buy_ratio < Decimal('0.42'):
        return True
    if evidence.rsi <= evidence.previous_rsi - Decimal('3') and evidence.buy_ratio < Decimal('0.50'):
        return True
    return evidence.rsi < evidence.previous_rsi and evidence.buy_ratio < Decimal('0.48')


def _is_exhaustion_or_weakening(regime: str, evidence: TrendEvidence) -> bool:
    """Return True when structure, flow, or overbought momentum shows top exhaustion."""
    return (
        regime in ('WEAKENING', 'CONFIRMED_REVERSAL', 'SEVERE_ADVERSE_MOVE')
        or evidence.failed_new_high
        or (evidence.adverse_move and evidence.buy_ratio < Decimal('0.48'))
        or _overbought_exhaustion(evidence)
    )


@dataclass(frozen=True)
class APTSState:
    context: DecisionContext
    entry_price: Decimal
    quantity: Decimal
    high_price: Decimal
    stop_price: Decimal
    floor_price: Decimal
    entry_fee_rate: Decimal
    exit_fee_rate: Decimal
    slippage_rate: Decimal
    stage: str='S0_INITIAL_PROTECTION'
    trigger_latched: bool=False
    trigger_reason: str=''
    last_decision_time_ms: int=0
    scheme: str='APTS'
    entry_time_ms: int=0
    reversal_hits: int=0
    reversal_confirm_ticks: int=1
    exhaustion_hits: int=0
    exhaustion_last_bar_ms: int=0
    exhaustion_confirm_bars: int=2
    last_normal_distance: Decimal=Decimal('.005')

    @classmethod
    def create(cls,context,entry_price,quantity,tick_size,entry_fee_rate='0.001',exit_fee_rate='0.001',slippage_rate='0.001',scheme='APTS',stop_distance=None,entry_time_ms=0,reversal_confirm_ticks=1,exhaustion_confirm_bars=2):
        entry,qty=D(entry_price,positive=True),D(quantity,positive=True)
        fees=[D(x,nonnegative=True) for x in (entry_fee_rate,exit_fee_rate,slippage_rate)]
        if max(fees)>=Decimal('.1'):raise ValueError('invalid cost assumptions')
        if scheme not in ('APTS','FIXED_0_5','FIXED_1_0','ATR'):raise ValueError('unknown scheme')
        stop=initial_stop(entry,tick_size) if stop_distance is None else initial_stop(entry,tick_size,stop_distance=stop_distance)
        exhaust_bars=2 if exhaustion_confirm_bars is None else max(1,int(exhaustion_confirm_bars))
        return cls(
            context,entry,qty,entry,stop,stop,*fees,
            last_decision_time_ms=context.decision_time_ms,scheme=scheme,
            entry_time_ms=int(entry_time_ms or 0),reversal_hits=0,
            reversal_confirm_ticks=max(1,int(reversal_confirm_ticks or 1)),
            exhaustion_hits=0,exhaustion_last_bar_ms=0,
            exhaustion_confirm_bars=exhaust_bars,
            last_normal_distance=Decimal('.005'),
        )

    def asdict(self):return json_safe(self)

    @classmethod
    def from_dict(cls,value):
        data=dict(value)
        data['context']=DecisionContext(**data['context'])
        for key in ('entry_price','quantity','high_price','stop_price','floor_price','entry_fee_rate','exit_fee_rate','slippage_rate'):
            data[key]=D(data[key],nonnegative=True)
        data['exhaustion_hits']=int(data.get('exhaustion_hits') or 0)
        data['exhaustion_last_bar_ms']=int(data.get('exhaustion_last_bar_ms') or 0)
        data['exhaustion_confirm_bars']=max(1,int(data.get('exhaustion_confirm_bars') or 2))
        data['last_normal_distance']=D(data.get('last_normal_distance','0.005'),nonnegative=True)
        return cls(**data)


@dataclass(frozen=True,kw_only=True)
class ProtectionDecision(DecisionBase):
    state: APTSState
    stop_price: Decimal
    limit_price: Decimal
    quantity: Decimal
    fee_estimate: Decimal
    risk_estimate: Decimal
    exit_required: bool
    protection_status: str
    market_regime: str
    trailing_distance: Decimal


def update_protection(state,context,bid,evidence,*,tick_size,quantity=None,native_supported=False):
    bid,tick=D(bid,positive=True),D(tick_size,positive=True)
    qty=state.quantity if quantity is None else D(quantity,nonnegative=True)
    identity=('symbol','account_id','strategy_id','signal_id')
    if any(getattr(state.context,k)!=getattr(context,k) for k in identity):raise ValueError('protection context ownership mismatch')
    version=context.symbol+'-apts-research-1'
    context=bind_context(context,{'state':state,'bid':bid,'evidence':evidence,'tick_size':tick,
                                 'remaining_quantity':qty,'native_supported':native_supported},version)
    regime=classify(evidence)
    distance=trailing_distance(regime,evidence.atr,bid,scheme=state.scheme)
    current=replace(state,quantity=qty)
    reasons=[]
    if not native_supported:reasons.append('NATIVE_ORDER_UNSUPPORTED_LOCAL_SIMULATION_ONLY')
    status='PROTECTION_MATH_ONLY'
    protection='SIMULATED_LOCAL_STOP' if not native_supported else 'NATIVE_INTENT_NOT_ACKNOWLEDGED'
    fresh=(context.book_time_ms is not None and 0<=context.decision_time_ms-context.book_time_ms<=3000
           and context.decision_time_ms<context.valid_until_ms)
    if qty==0:
        status,protection='CLOSED','NO_REMAINING_QUANTITY'
    elif context.decision_time_ms<=state.last_decision_time_ms:
        status='DUPLICATE_OR_OLD_EVENT'
    elif not fresh:
        status='DATA_UNAVAILABLE'
        reasons.append('KEEP_LAST_PROTECTION_NO_FABRICATED_FILL')
    elif state.trigger_latched:
        status='EXIT_LATCHED'
    elif bid<=state.stop_price:
        # Test the already-effective stop BEFORE any attempt to widen a band.
        current=replace(current,trigger_latched=True,trigger_reason='PROTECTIVE_STOP_TOUCHED')
        status='EXIT_REQUIRED'
    elif not evidence.ready:
        status='DATA_UNAVAILABLE'
        reasons.append('KEEP_LAST_STOP_TREND_INPUTS_UNAVAILABLE')
    else:
        high=max(state.high_price,bid)
        gross=bid/state.entry_price-1
        peak_gross=high/state.entry_price-1
        stage=state.stage
        if gross>S2_ADAPTIVE_TRAILING_TRIGGER and stage!='S3_TREND_EXHAUSTION':stage='S2_ADAPTIVE_TRAILING'
        elif gross>=S1_PROFIT_PROTECTION_TRIGGER and stage=='S0_INITIAL_PROTECTION':stage='S1_PROFIT_PROTECTION'
        floor=state.floor_price
        candidate=state.stop_price
        exhausted=_is_exhaustion_or_weakening(regime,evidence)
        effective_distance=distance
        if state.scheme=='APTS' and regime=='NORMAL_UPTREND' and evidence.higher_low_5m and not evidence.adverse_move and evidence.buy_ratio>=Decimal('0.50') and evidence.atr>0:
            atr_band=min(Decimal('0.0095'),max(Decimal('0.005'),(Decimal('1.5')*evidence.atr)/bid))
            effective_distance=max(effective_distance,atr_band)
        ex_s1=(stage=='S1_PROFIT_PROTECTION' and state.scheme=='APTS' and peak_gross>=S1_DYNAMIC_LOCK_TRIGGER and exhausted)
        ex_s2=(stage in ('S2_ADAPTIVE_TRAILING','S3_TREND_EXHAUSTION') and state.scheme=='APTS' and peak_gross>=S2_EXHAUSTION_TIGHTEN_TRIGGER and exhausted)
        wants_weakening_shrink=(stage in ('S2_ADAPTIVE_TRAILING','S3_TREND_EXHAUSTION') and state.scheme=='APTS' and regime=='WEAKENING')
        wants_tighten=ex_s1 or ex_s2 or wants_weakening_shrink
        cur_bar_id=evidence.bar_time_ms if evidence.bar_time_ms>0 else ((context.decision_time_ms//60_000)*60_000-1)
        exhaustion_hits=state.exhaustion_hits
        exhaustion_last_bar=state.exhaustion_last_bar_ms
        last_normal_distance=state.last_normal_distance
        if wants_tighten:
            if cur_bar_id!=exhaustion_last_bar:
                exhaustion_hits+=1
                exhaustion_last_bar=cur_bar_id
        else:
            exhaustion_hits=0
            exhaustion_last_bar=0
            if state.scheme=='APTS' and regime!='WEAKENING':
                last_normal_distance=max(effective_distance,Decimal('0.005'))
        tighten_confirmed=wants_tighten and exhaustion_hits>=max(1,state.exhaustion_confirm_bars)
        if stage!='S0_INITIAL_PROTECTION':
            floor=max(floor,profit_floor(state.entry_price,state.entry_fee_rate,state.exit_fee_rate,state.slippage_rate,tick))
            if floor>=bid:
                current=replace(current,trigger_latched=True,trigger_reason='PROFIT_FLOOR_NOT_EXECUTABLE')
                reasons.append('POSITIVE_NET_PROFIT_NOT_LOCKED')
                status='EXIT_REQUIRED'
            else:candidate=max(candidate,floor)
        if stage=='S1_PROFIT_PROTECTION' and state.scheme=='APTS' and peak_gross>=S1_DYNAMIC_LOCK_TRIGGER:
            s1_dist=Decimal('0.004') if (ex_s1 and tighten_confirmed) else Decimal('0.0055')
            candidate=max(candidate,high*(1-s1_dist))
            if ex_s1 and tighten_confirmed:reasons.append('S1_EXHAUSTION_LOCK_TIGHTENED')
            elif ex_s1:reasons.append('S1_EXHAUSTION_PENDING_CONFIRMATION')
        if stage in ('S2_ADAPTIVE_TRAILING','S3_TREND_EXHAUSTION'):
            if state.scheme=='APTS':
                if regime=='WEAKENING' and not tighten_confirmed:
                    distance=max(last_normal_distance,Decimal('0.005'))
                else:
                    distance=effective_distance
                if ex_s2 and tighten_confirmed:
                    distance=min(distance,Decimal('0.004'))
                    reasons.append('S2_EXHAUSTION_TRAILING_TIGHTENED')
                elif ex_s2 or wants_weakening_shrink:
                    reasons.append('S2_EXHAUSTION_PENDING_CONFIRMATION')
            candidate=max(candidate,high*(1-distance))
        s0_half_stop=ceil_step(state.entry_price-(state.entry_price-state.floor_price)*Decimal('0.5'),tick)
        if stage=='S0_INITIAL_PROTECTION' and high>=state.entry_price*Decimal('1.006') and s0_half_stop<bid:
            candidate=max(candidate,s0_half_stop)
        reversal_hits=0
        if regime in ('CONFIRMED_REVERSAL','SEVERE_ADVERSE_MOVE'):
            # S0 keeps the resting -0.8%/-1.0% stop on the first reversal tick, and tightens stop by 50% on persistent reversal.
            if stage=='S0_INITIAL_PROTECTION' and bid>state.stop_price:
                reversal_hits=state.reversal_hits+1
                reasons.append('REVERSAL_OBSERVED_S0_STOP_RETAINED')
                if reversal_hits>=2 and s0_half_stop<bid:
                    candidate=max(candidate,s0_half_stop)
                    reasons.append('S0_REVERSAL_STOP_TIGHTENED')
            else:
                reversal_hits=state.reversal_hits+1
                confirm_needed=1 if regime=='SEVERE_ADVERSE_MOVE' else max(1,state.reversal_confirm_ticks)
                if reversal_hits>=confirm_needed:
                    stage='S3_TREND_EXHAUSTION'
                    current=replace(current,trigger_latched=True,trigger_reason=regime)
                    status='EXIT_REQUIRED'
                else:
                    reasons.append('REVERSAL_PENDING_CONFIRMATION')
        candidate=ceil_step(candidate,tick)
        if candidate>=bid:
            current=replace(current,trigger_latched=True,trigger_reason=current.trigger_reason or 'TRAILING_STOP_TOUCHED')
            status='EXIT_REQUIRED'
        # A stop that cannot be newly established below the current market is
        # not advertised as newly effective. Preserve the previous valid stop.
        effective=max(state.stop_price,candidate) if candidate<bid else state.stop_price
        current=replace(
            current,stage=stage,high_price=high,floor_price=floor,stop_price=effective,
            reversal_hits=reversal_hits,exhaustion_hits=exhaustion_hits,
            exhaustion_last_bar_ms=exhaustion_last_bar,last_normal_distance=last_normal_distance,
        )
    if context.decision_time_ms>state.last_decision_time_ms and fresh:
        current=replace(current,last_decision_time_ms=context.decision_time_ms)
    assert current.stop_price>=state.stop_price
    limit=stop_limit_price(current.stop_price,tick)
    if current.trigger_reason in ('CONFIRMED_REVERSAL','SEVERE_ADVERSE_MOVE','PROFIT_FLOOR_NOT_EXECUTABLE','STAGNATION_12H_EXIT'):
        limit=stop_limit_price(bid,tick)
    return ProtectionDecision(context=context,status=status,parameter_version=version,reasons=tuple(reasons),
        state=current,stop_price=current.stop_price,limit_price=limit,quantity=qty,
        fee_estimate=qty*bid*current.exit_fee_rate,
        risk_estimate=max(Decimal(0),(current.entry_price-current.stop_price)*qty),
        exit_required=bool(current.trigger_latched and qty),protection_status=protection,
        market_regime=regime,trailing_distance=distance)
