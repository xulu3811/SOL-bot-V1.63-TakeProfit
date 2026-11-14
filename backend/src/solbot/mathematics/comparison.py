"""Controlled close-path exit comparison, not a strategy profitability backtest."""
from dataclasses import replace
from decimal import Decimal
from .apts_exit import APTSState,update_protection
from .contracts import DecisionContext
from .decimal_utils import D,canonical_hash,json_safe
from .trend_exhaustion import TrendEvidence

SCHEMES=('FIXED_0_5','FIXED_1_0','ATR','APTS')

def compare_path(name,observations,*,entry_price='100',notional='500',fee_rate='.001',slippage_rate='.001',tick_size='.01'):
    """Each observation is (UTC ms, observed price, contemporaneous evidence).

    A/B/C share S0/S1 protection but do not use D's trend exhaustion exit.
    Stops are observed only at supplied prices. Modeled liquidation is a research
    market-exit assumption, separate from P3 shadow's possibly-unfilled stop limit.
    Future prices appear solely in ex-post early-exit metrics, never decisions.
    """
    if not observations:raise ValueError('nonempty observations required')
    if any(b[0]<=a[0] for a,b in zip(observations,observations[1:])):raise ValueError('ordered observations required')
    entry,capital,fee,slip=D(entry_price,positive=True),D(notional,positive=True),D(fee_rate,nonnegative=True),D(slippage_rate,nonnegative=True)
    if fee>=1 or slip>=1:raise ValueError('invalid cost assumption')
    inputs={'name':name,'observations':observations,'entry_price':entry,'notional':capital,'fee_rate':fee,'slippage_rate':slip,'tick_size':tick_size}
    digest=canonical_hash(inputs)
    now=observations[0][0]-1
    ctx=DecisionContext('SOLUSDT','SOL-shadow','T1','p3-comparison-1','research-'+name,now,now,now+5000,digest)
    quantity=capital/entry
    result={}
    for scheme in SCHEMES:
        state=APTSState.create(ctx,entry,quantity,tick_size,fee,fee,slip,scheme=scheme)
        peak=capital*(1+fee);maxdd=Decimal(0);high=entry
        trajectory=[];exit_index=len(observations)-1;reason='HORIZON_MARK'
        for index,(stamp,price,evidence) in enumerate(observations):
            price=D(price,positive=True)
            used=evidence if scheme=='APTS' else replace(evidence,structure_broken=False,adverse_move=False,failed_new_high=False,
                buy_ratio=Decimal('.5'),previous_buy_ratio=Decimal('.5'),rsi=Decimal(50),previous_rsi=Decimal(50))
            context=replace(ctx,decision_time_ms=stamp,book_time_ms=stamp,valid_until_ms=stamp+5000)
            decision=update_protection(state,context,price,used,tick_size=tick_size)
            state=decision.state
            high=max(high,price)
            equity=quantity*price*(1-slip)*(1-fee)
            peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak)
            trajectory.append({'time':stamp,'price':price,'stop':state.stop_price,'stage':state.stage,'triggered':state.trigger_latched})
            if decision.exit_required:
                exit_index=index;reason=state.trigger_reason;break
        exit_observed=D(observations[exit_index][1]);fill=exit_observed*(1-slip)
        pnl=quantity*fill*(1-fee)-capital*(1+fee)
        maximum_profit=quantity*(high-entry)
        later_peak=max(D(row[1]) for row in observations[exit_index:])
        result[scheme]={'net_pnl':pnl,'net_return':pnl/capital,'max_equity_drawdown':maxdd,
            'peak_profit_retained':pnl/maximum_profit if maximum_profit>0 else None,
            'early_exit_ex_post':exit_index<len(observations)-1 and later_peak>exit_observed*Decimal('1.005'),
            'reversal_giveback':quantity*(high-exit_observed),'exit_price':fill,'exit_index':exit_index,'reason':reason,'trajectory':trajectory}
    return json_safe({'name':name,'input_hash':digest,'inputs':inputs,'models':result,
        'limitations':['Research exits at observed close with assumed slippage; not historical depth or guaranteed stop-limit fills',
                      'Same entry path and costs; not T1/R1 profitability or out-of-sample validation',
                      'Early-exit metric uses future path only after decisions; APTS superiority is not presumed']})

def synthetic_study():
    normal=TrendEvidence(ready=True,atr=Decimal('.4'))
    strong=replace(normal,trend_4h=True,trend_1h=True,trend_15m=True,higher_low_1m=True,higher_low_5m=True,
                   rvol=Decimal(3),buy_ratio=Decimal('.7'),previous_buy_ratio=Decimal('.65'),atr=Decimal('.5'))
    reversal=replace(normal,structure_broken=True,buy_ratio=Decimal('.3'),previous_buy_ratio=Decimal('.7'),rsi=Decimal('40'),previous_rsi=Decimal('52'))
    scenarios={'continuation':[('101',strong),('102',strong),('101.38',strong),('104',strong),('103',reversal)],
        'reversal':[('101',normal),('102',strong),('101.7',reversal),('99',reversal)],
        'gap':[('100.2',normal),('98',normal),('97',normal)],
        'narrow_range':[('100.4',normal),('100.1',normal),('100.6',normal),('100.2',normal)],
        'false_exhaustion':[('102',strong),('101.8',reversal),('105',strong),('104.8',strong)]}
    return [compare_path(name,[(1794400000000+i*60000,p,e) for i,(p,e) in enumerate(rows)]) for name,rows in scenarios.items()]
