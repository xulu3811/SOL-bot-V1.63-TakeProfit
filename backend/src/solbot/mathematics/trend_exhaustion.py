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
    # A one-bar 5m dip is a normal pullback. Reversal needs a deep break plus both flow and momentum.
    if evidence.structure_broken and selling and momentum:return 'CONFIRMED_REVERSAL'
    if ((selling or momentum) and evidence.failed_new_high) or evidence.structure_broken:
        return 'WEAKENING'
    macro_breakout=(evidence.trend_4h and evidence.trend_1h and evidence.trend_15m
        and evidence.rvol>=2 and evidence.buy_ratio>=Decimal('.6')
        and evidence.buy_ratio>=evidence.previous_buy_ratio
        and evidence.higher_low_1m and evidence.higher_low_5m)
    intraday_main_force=(evidence.higher_low_5m
        and not evidence.structure_broken
        and not evidence.adverse_move
        and not evidence.failed_new_high
        and evidence.rvol>=Decimal('1.2')
        and evidence.buy_ratio>=Decimal('.54')
        and (evidence.rsi>=Decimal('58') or evidence.higher_low_1m))
    if macro_breakout or intraday_main_force:
        return 'STRONG_BREAKOUT'
    if not (evidence.higher_low_5m and not evidence.adverse_move and evidence.buy_ratio>=Decimal('.50')):
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
    five_quote=D(five[-1]['quote_volume'])
    five_buy_ratio=D(five[-1]['taker_buy_quote_volume'])/five_quote if five_quote else Decimal('.5')
    prior_buy_ratio=D(prior['taker_buy_quote_volume'])/prior_quote if prior_quote else Decimal('.5')
    average=sum((D(r['volume']) for r in five[-21:-1]),Decimal(0))/20
    atr=decimal_atr(five)
    broken_low=min(D(r['low']) for r in five[-4:-1])
    post_5m=[r for r in one if int(r['open_time'])>int(five[-1]['close_time'])]
    rolling_5m=one[-5:]
    rolling_vol=sum((D(r['volume']) for r in rolling_5m),Decimal(0))
    rolling_quote=sum((D(r['quote_volume']) for r in rolling_5m),Decimal(0))
    rolling_buy=sum((D(r['taker_buy_quote_volume']) for r in rolling_5m),Decimal(0))
    rolling_buy_ratio=rolling_buy/rolling_quote if rolling_quote else five_buy_ratio
    closed_hl_5m=D(five[-1]['low'])>=D(five[-2]['low'])>=D(five[-3]['low']) and D(five[-1]['low'])>D(five[-3]['low'])
    if post_5m:
        dev_low=min(D(r['low']) for r in post_5m)
        dev_close=D(post_5m[-1]['close'])
        dev_hl_5m=dev_low>=D(five[-1]['low'])>=D(five[-2]['low']) and dev_low>D(five[-2]['low'])
        higher_low_5m=closed_hl_5m or dev_hl_5m
        adverse_move=dev_close<D(five[-1]['close']) and D(five[-1]['close'])<D(prior['close'])
        prev_buy_ratio=five_buy_ratio
        candle_buy_ratio=rolling_buy_ratio
        rsi=max(_rsi(values),_rsi(values+[dev_close])) if dev_close>=D(five[-1]['close']) else _rsi(values+[dev_close])
        previous_rsi=_rsi(values)
    else:
        higher_low_5m=closed_hl_5m
        adverse_move=D(five[-1]['close'])<D(prior['close'])
        prev_buy_ratio=prior_buy_ratio
        candle_buy_ratio=five_buy_ratio
        rsi=_rsi(values)
        previous_rsi=_rsi(values[:-1])
    raw_buy_ratio=D(features['buy_ratio'])
    if higher_low_5m and not adverse_move and raw_buy_ratio>=Decimal('.45') and candle_buy_ratio>=Decimal('.54'):
        effective_buy_ratio=max(raw_buy_ratio,candle_buy_ratio)
    else:
        effective_buy_ratio=raw_buy_ratio
    rvol=max(D(five[-1]['volume']),rolling_vol)/average if average else Decimal(0)
    return TrendEvidence(ready=True,trend_4h=trends['4h'],trend_1h=trends['1h'],trend_15m=trends['15m'],
        higher_low_1m=D(one[-1]['low'])>=D(one[-2]['low'])>=D(one[-3]['low']) and D(one[-1]['low'])>D(one[-3]['low']),
        higher_low_5m=higher_low_5m,
        structure_broken=D(one[-1]['close'] if post_5m else five[-1]['close'])<broken_low-atr*Decimal('0.5'),
        failed_new_high=D(five[-1]['high'])>=max(D(r['high']) for r in five[-4:-1]) and D(five[-1]['close'])<=D(prior['close']) and not (post_5m and D(post_5m[-1]['close'])>D(five[-1]['high'])),
        adverse_move=adverse_move,
        rvol=rvol,
        buy_ratio=effective_buy_ratio,
        previous_buy_ratio=prev_buy_ratio,
        rsi=rsi,previous_rsi=previous_rsi,
        atr=atr,previous_atr=decimal_atr(five[:-1]))
