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
    raw_buy_ratio: Decimal | None = None
    bar_time_ms: int = 0

    def __post_init__(self):
        for name in ('rvol','buy_ratio','previous_buy_ratio','rsi','previous_rsi','atr','previous_atr'):
            object.__setattr__(self,name,D(getattr(self,name),name,nonnegative=True))
        if self.raw_buy_ratio is not None:
            object.__setattr__(self,'raw_buy_ratio',D(self.raw_buy_ratio,'raw_buy_ratio',nonnegative=True))
        object.__setattr__(self,'bar_time_ms',max(0,int(self.bar_time_ms or 0)))
        ratios=(self.buy_ratio,self.previous_buy_ratio)+((self.raw_buy_ratio,) if self.raw_buy_ratio is not None else ())
        if max(ratios)>1 or max(self.rsi,self.previous_rsi)>100:
            raise ValueError('invalid flow/momentum evidence')


class FlowSmoother:
    """Stateful EWMA smoother for noisy short-window taker buy_ratio observations."""

    def __init__(self, alpha: Decimal = Decimal('0.25'), max_gap_ms: int = 30_000):
        self.alpha = D(alpha, 'alpha', positive=True)
        self.max_gap_ms = int(max_gap_ms)
        self.ema: Decimal | None = None
        self.last_ts_ms: int | None = None

    def update(self, buy_ratio, ts_ms: int | None = None) -> Decimal:
        raw = max(Decimal(0), min(Decimal(1), D(buy_ratio, 'buy_ratio', nonnegative=True)))
        stamp = int(ts_ms) if ts_ms is not None else None
        if self.ema is None:
            self.ema = raw
        elif stamp is not None and self.last_ts_ms is not None:
            if stamp == self.last_ts_ms:
                return self.ema
            if stamp < self.last_ts_ms or (stamp - self.last_ts_ms) > self.max_gap_ms:
                self.ema = raw
            else:
                self.ema = self.alpha * raw + (Decimal(1) - self.alpha) * self.ema
        else:
            self.ema = self.alpha * raw + (Decimal(1) - self.alpha) * self.ema
        if stamp is not None:
            self.last_ts_ms = stamp
        return self.ema


def smooth_micro_features(micro_features: dict | None, smoother: FlowSmoother | None = None) -> dict | None:
    """Attach smoothed_buy_ratio while keeping buy_ratio un-lagged for S3 reversal triggers."""
    if not isinstance(micro_features, dict) or not micro_features.get('ready') or micro_features.get('buy_ratio') is None:
        return micro_features
    out = dict(micro_features)
    if smoother is not None:
        ts = micro_features.get('exchange_time_ms') or micro_features.get('received_time_ms') or micro_features.get('feature_generated_time_ms')
        out['smoothed_buy_ratio'] = format(smoother.update(micro_features['buy_ratio'], ts), 'f')
    return out


def blend_flow_ratio(
    flow: Decimal,
    candle_buy_ratio: Decimal,
    *,
    higher_low_5m: bool,
    adverse_move: bool,
    has_smoothed: bool = False,
) -> Decimal:
    """Continuous [0.35, 0.55] blend preventing single-tick WEAKENING drop at 45%."""
    flow_dec = max(Decimal(0), min(Decimal(1), D(flow, 'flow', nonnegative=True)))
    candle_dec = max(Decimal(0), min(Decimal(1), D(candle_buy_ratio, 'candle_buy_ratio', nonnegative=True)))
    if higher_low_5m and not adverse_move and candle_dec >= Decimal('.54'):
        weight = max(Decimal(0), min(Decimal(1), (flow_dec - Decimal('.35')) / Decimal('.20')))
        eff = flow_dec + weight * max(Decimal(0), candle_dec - flow_dec)
    elif has_smoothed:
        eff = Decimal('.6') * flow_dec + Decimal('.4') * candle_dec
    else:
        eff = flow_dec
    return max(Decimal(0), min(Decimal(1), eff))


def classify(evidence: TrendEvidence):
    if not evidence.ready:return 'DATA_UNAVAILABLE'
    # Keep S3/severe reversal checks on instantaneous raw flow so smoothing never dulls emergency exits.
    raw_buy=evidence.buy_ratio if evidence.raw_buy_ratio is None else evidence.raw_buy_ratio
    raw_selling=raw_buy<Decimal('.4') and raw_buy<evidence.previous_buy_ratio
    selling=evidence.buy_ratio<Decimal('.4') and evidence.buy_ratio<evidence.previous_buy_ratio
    momentum=evidence.rsi<evidence.previous_rsi-Decimal(8)
    severe=evidence.previous_atr>0 and evidence.atr>evidence.previous_atr*2 and evidence.adverse_move and raw_selling
    if severe:return 'SEVERE_ADVERSE_MOVE'
    # A one-bar 5m dip is a normal pullback. Reversal needs a deep break plus both flow and momentum.
    if evidence.structure_broken and raw_selling and momentum:return 'CONFIRMED_REVERSAL'
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
    prior_high=max(D(r['high']) for r in five[-4:])
    post_5m=[r for r in one if int(r['open_time'])>int(five[-1]['close_time'])]
    rolling_5m=one[-5:]
    rolling_vol=sum((D(r['volume']) for r in rolling_5m),Decimal(0))
    rolling_quote=sum((D(r['quote_volume']) for r in rolling_5m),Decimal(0))
    rolling_buy=sum((D(r['taker_buy_quote_volume']) for r in rolling_5m),Decimal(0))
    rolling_buy_ratio=rolling_buy/rolling_quote if rolling_quote else five_buy_ratio
    closed_hl_5m=D(five[-1]['low'])>=D(five[-2]['low'])>=D(five[-3]['low']) and D(five[-1]['low'])>D(five[-3]['low'])
    post_failed_high=False
    if post_5m:
        dev_low=min(D(r['low']) for r in post_5m)
        dev_close=D(post_5m[-1]['close'])
        post_peak=max(D(r['high']) for r in post_5m)
        pullback_ref=max(atr*Decimal('0.4'),post_peak*Decimal('0.0025'))
        post_failed_high=post_peak>prior_high and dev_close<=post_peak-pullback_ref
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
    has_smoothed=features.get('smoothed_buy_ratio') is not None
    flow=D(features['smoothed_buy_ratio']) if has_smoothed else raw_buy_ratio
    effective_buy_ratio=blend_flow_ratio(flow,candle_buy_ratio,higher_low_5m=higher_low_5m,adverse_move=adverse_move,has_smoothed=has_smoothed)
    rvol=max(D(five[-1]['volume']),rolling_vol)/average if average else Decimal(0)
    closed_failed_high=D(five[-1]['high'])>=max(D(r['high']) for r in five[-4:-1]) and D(five[-1]['close'])<=D(prior['close']) and not (post_5m and D(post_5m[-1]['close'])>D(five[-1]['high']))
    return TrendEvidence(ready=True,trend_4h=trends['4h'],trend_1h=trends['1h'],trend_15m=trends['15m'],
        higher_low_1m=D(one[-1]['low'])>=D(one[-2]['low'])>=D(one[-3]['low']) and D(one[-1]['low'])>D(one[-3]['low']),
        higher_low_5m=higher_low_5m,
        structure_broken=D(one[-1]['close'] if post_5m else five[-1]['close'])<broken_low-atr*Decimal('0.5'),
        failed_new_high=closed_failed_high or post_failed_high,
        adverse_move=adverse_move,
        rvol=rvol,
        buy_ratio=effective_buy_ratio,
        previous_buy_ratio=prev_buy_ratio,
        rsi=rsi,previous_rsi=previous_rsi,
        atr=atr,previous_atr=decimal_atr(five[:-1]),
        raw_buy_ratio=raw_buy_ratio,
        bar_time_ms=int(one[-1]['close_time']))
