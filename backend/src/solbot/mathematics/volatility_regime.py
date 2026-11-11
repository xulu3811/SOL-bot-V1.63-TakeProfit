"""Decimal volatility inputs and explicitly uncalibrated trailing distances."""
from decimal import Decimal
from .decimal_utils import D, clip


def decimal_atr(candles, period=14):
    if len(candles) <= period:
        raise ValueError('insufficient ATR history')
    ranges=[]
    for previous,current in zip(candles,candles[1:]):
        high,low,close=D(current['high']),D(current['low']),D(previous['close'])
        ranges.append(max(high-low,abs(high-close),abs(low-close)))
    value=sum(ranges[:period],Decimal(0))/period
    for item in ranges[period:]:value=(value*(period-1)+item)/period
    return value


def trailing_distance(regime, atr, price, *, scheme='APTS'):
    atr,price=D(atr,nonnegative=True),D(price,positive=True)
    if scheme=='FIXED_0_5':return Decimal('.005')
    if scheme=='FIXED_1_0':return Decimal('.01')
    if scheme=='ATR':return clip(2*atr/price,Decimal('.004'),Decimal('.012'))
    if scheme!='APTS':raise ValueError('unknown comparison scheme')
    if regime=='STRONG_BREAKOUT':return clip(2*atr/price,Decimal('.008'),Decimal('.012'))
    if regime=='WEAKENING':return Decimal('.004')
    return Decimal('.005')
