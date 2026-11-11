"""Tick-aware long-only protection. Trigger prices are not fill guarantees."""
from decimal import Decimal
from .decimal_utils import D,ceil_step,floor_step


def initial_stop(entry,tick):
    entry,tick=D(entry,positive=True),D(tick,positive=True)
    price=ceil_step(entry*Decimal('.992'),tick)
    if price<=0 or price>=entry:raise ValueError('tick prevents valid 0.8% initial protection')
    return price


def profit_floor(entry,entry_fee,exit_fee,slippage,tick):
    entry=D(entry,positive=True)
    entry_fee,exit_fee,slippage=(D(x,nonnegative=True) for x in (entry_fee,exit_fee,slippage))
    if max(entry_fee,exit_fee,slippage)>=Decimal('.1'):raise ValueError('unreasonable protective cost assumption')
    # One extra tick ensures strictly positive modeled net proceeds at the
    # modeled slippage. A market gap can still defeat that assumption.
    return ceil_step(entry*(1+entry_fee)/((1-exit_fee)*(1-slippage)),tick)+D(tick)


def stop_limit_price(stop,tick,buffer=Decimal('.003')):
    return floor_step(D(stop,positive=True)*(1-D(buffer,nonnegative=True)),tick)
