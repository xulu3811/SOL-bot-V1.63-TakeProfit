"""Observable trend/flow combinations, never volume-only continuation claims."""
from dataclasses import dataclass
from decimal import Decimal
from .decimal_utils import D
from .volatility_regime import decimal_atr


@dataclass(frozen=True)
class TrendEvidence:
    ready: bool = False
    trend_4h: bool = False
    trend_1h: bool = False
    trend_15m: bool = False
    higher_low_1m: bool = False
    higher_low_5m: bool = False
    structure_broken: bool = False
    failed_new_high: bool = False
    adverse_move: bool = False
    rvol: Decimal = Decimal(0)
    buy_ratio: Decimal = Decimal('.5')
    previous_buy_ratio: Decimal = Decimal('.5')
    rsi: Decimal = Decimal(50)
    previous_rsi: Decimal = Decimal(50)
    atr: Decimal = Decimal(0)
    previous_atr: Decimal = Decimal(0)

    def __post_init__(self):
        for name in ('rvol','buy_ratio','previous_buy_ratio','rsi','previous_rsi','atr','previous_atr'):
            object.__setattr__(self,name,D(getattr(self,name),name,nonnegative=True))
        if max(self.buy_ratio,self.previous_buy_ratio)>1 or max(self.rsi,self.previous_rsi)>100:
            raise ValueError('invalid flow/momentum evidence')


def classify(evidence: TrendEvidence):
    if not evidence.ready:return 'DATA_UNAVAILABLE'
    selling=evidence.buy_ratio<Decimal('.4') and evidence.buy_ratio<evidence.previous_buy_ratio
    momentum=evidence.rsi<evidence.previous_rsi-Decimal(8)
    severe=evidence.previous_atr>0 and evidence.atr>evidence.previous_atr*2 and evidence.adverse_move and selling
    if severe:return 'SEVERE_ADVERSE_MOVE'
    if evidence.structure_broken and (selling or momentum):return 'CONFIRMED_REVERSAL'
    if ((selling or momentum) and evidence.failed_new_high) or evidence.structure_broken:
        return 'WEAKENING'
    if (evidence.trend_4h and evidence.trend_1h and evidence.trend_15m
        and evidence.rvol>=2 and evidence.buy_ratio>=Decimal('.6')
        and evidence.buy_ratio>=evidence.previous_buy_ratio
        and evidence.higher_low_1m and evidence.higher_low_5m):
        return 'STRONG_BREAKOUT'
    if evidence.buy_ratio<evidence.previous_buy_ratio-Decimal('.1') or momentum:
        return 'WEAKENING'
    return 'NORMAL_UPTREND'


def _ema(values,period):
    value=sum(values[:period],Decimal(0))/period
    alpha=Decimal(2)/(period+1)
    for current in values[period:]:value+=(current-value)*alpha
    return value


def _rsi(values,period=6):
    changes=[b-a for a,b in zip(values,values[1:])]
    gain=sum((max(x,Decimal(0)) for x in changes[:period]),Decimal(0))/period
    loss=sum((max(-x,Decimal(0)) for x in changes[:period]),Decimal(0))/period
    for x in changes[period:]:
        gain=(gain*(period-1)+max(x,Decimal(0)))/period
        loss=(loss*(period-1)+max(-x,Decimal(0)))/period
    return Decimal(50) if gain==loss==0 else Decimal(100) if loss==0 else 100-100/(1+gain/loss)


def from_closed_inputs(symbol,candles,features,as_of_ms):
    # P2 validation supplies closed/contiguous/symbol/alignment gating, while
    # all new protective price/volatility arithmetic below uses Decimal.
    from solbot.strategy import prepare
    selected,errors=prepare(symbol,candles,as_of_ms)
    if errors or features.get('symbol')!=symbol or not features.get('ready') or features.get('buy_ratio') is None:
        return TrendEvidence()
    trends={}
    for name in ('4h','1h','15m'):
        values=[D(row['close']) for row in selected[name]]
        trends[name]=_ema(values,20)>_ema(values,50)
    five,one=selected['5m'],selected['1m']
    values=[D(row['close']) for row in five]
    prior=five[-2]
    prior_quote=D(prior['quote_volume'])
    average=sum((D(r['volume']) for r in five[-21:-1]),Decimal(0))/20
    return TrendEvidence(ready=True,trend_4h=trends['4h'],trend_1h=trends['1h'],trend_15m=trends['15m'],
        higher_low_1m=D(one[-1]['low'])>=D(one[-2]['low'])>D(one[-3]['low']),
        higher_low_5m=D(five[-1]['low'])>=D(five[-2]['low'])>D(five[-3]['low']),
        structure_broken=D(five[-1]['close'])<min(D(r['low']) for r in five[-4:-1]),
        failed_new_high=D(five[-1]['high'])>=max(D(r['high']) for r in five[-4:-1]) and D(five[-1]['close'])<=D(prior['close']),
        adverse_move=D(five[-1]['close'])<D(prior['close']),
        rvol=D(five[-1]['volume'])/average if average else Decimal(0),
        buy_ratio=D(features['buy_ratio']),
        previous_buy_ratio=D(prior['taker_buy_quote_volume'])/prior_quote if prior_quote else Decimal('.5'),
        rsi=_rsi(values),previous_rsi=_rsi(values[:-1]),
        atr=decimal_atr(five),previous_atr=decimal_atr(five[:-1]))
