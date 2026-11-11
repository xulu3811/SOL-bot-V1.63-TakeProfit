"""Fresh exchange rules, including BNB, with conservative unknown-filter denial."""
from dataclasses import dataclass
from solbot.mathematics.decimal_utils import D

@dataclass(frozen=True)
class ExchangeFilters:
    symbol: str
    info: dict
    observed_at: int
    exchange_filters: tuple=()

    @classmethod
    def from_exchange_info(cls,data,symbol,observed_at):
        if symbol not in ('SUIUSDT','BNBUSDT'): raise ValueError('wrong live symbol')
        rows=[r for r in data.get('symbols',[]) if r.get('symbol')==symbol]
        if len(rows)!=1: raise ValueError('symbol rules absent or duplicated')
        return cls(symbol,rows[0],observed_at,tuple(data.get('exchangeFilters',[])))

    def validate(self,params,*,at,reference=None,reference_minutes=None,open_orders=None,algo_orders=None,position=None,account_permissions=()):
        reasons=[]; info=self.info
        if params.get('symbol')!=self.symbol: reasons.append('SYMBOL_MISMATCH')
        if not 0<=at-self.observed_at<=60000: reasons.append('STALE_FILTERS')
        if info.get('status')!='TRADING' or info.get('isSpotTradingAllowed') is not True: reasons.append('SPOT_NOT_TRADING')
        permission_sets=info.get('permissionSets')
        if permission_sets and not all(any(p in account_permissions for p in group) for group in permission_sets): reasons.append('ACCOUNT_PERMISSION_SET')
        typ=params.get('type'); side=params.get('side')
        if side not in ('BUY','SELL') or typ not in info.get('orderTypes',[]): reasons.append('UNSUPPORTED_ORDER_TYPE')
        if 'trailingDelta' in params and info.get('allowTrailingStop') is not True: reasons.append('TRAILING_UNSUPPORTED')
        qty=D(params['quantity'],positive=True)
        price=D(params['price'],positive=True) if 'price' in params else (D(reference,positive=True) if reference is not None else None)
        stop=D(params['stopPrice'],positive=True) if 'stopPrice' in params else None
        fs=info.get('filters',[])
        names={r['filterType'] for r in fs}
        if not {'PRICE_FILTER','LOT_SIZE'}<=names or not names.intersection({'MIN_NOTIONAL','NOTIONAL'}): reasons.append('INCOMPLETE_FILTERS')
        for f in list(fs)+list(self.exchange_filters):
            name=f['filterType']
            if name=='PRICE_FILTER':
                for v in ([price] if 'price' in params else [])+([stop] if stop is not None else []):
                    low,high,step=(D(f[k],nonnegative=True) for k in ('minPrice','maxPrice','tickSize'))
                    if (low and v<low) or (high and v>high) or (step and v%step): reasons.append('PRICE_FILTER')
            elif name in ('LOT_SIZE','MARKET_LOT_SIZE'):
                if name=='MARKET_LOT_SIZE' and typ!='MARKET': continue
                low,high,step=(D(f[k],nonnegative=True) for k in ('minQty','maxQty','stepSize'))
                if qty<low or (high and qty>high) or (step and qty%step): reasons.append(name)
            elif name in ('MIN_NOTIONAL','NOTIONAL'):
                if price is None: reasons.append('NOTIONAL_REFERENCE_MISSING'); continue
                if typ=='MARKET' and int(f.get('avgPriceMins',0))!=reference_minutes: reasons.append('NOTIONAL_REFERENCE_WINDOW')
                n=price*qty
                if (typ!='MARKET' or f.get('applyToMarket',f.get('applyMinToMarket',False))) and n<D(f['minNotional']): reasons.append(name)
                if name=='NOTIONAL' and (typ!='MARKET' or f.get('applyMaxToMarket',False)) and D(f['maxNotional'])>0 and n>D(f['maxNotional']): reasons.append(name)
            elif name in ('PERCENT_PRICE','PERCENT_PRICE_BY_SIDE'):
                if reference is None or reference_minutes!=int(f['avgPriceMins']): reasons.append('PERCENT_REFERENCE_MISSING'); continue
                ref=D(reference,positive=True)
                prefix='bid' if side=='BUY' else 'ask'
                low=D(f['multiplierDown'] if name=='PERCENT_PRICE' else f[prefix+'MultiplierDown'])
                high=D(f['multiplierUp'] if name=='PERCENT_PRICE' else f[prefix+'MultiplierUp'])
                if price is None or not ref*low<=price<=ref*high: reasons.append(name)
            elif name in ('MAX_NUM_ORDERS','EXCHANGE_MAX_NUM_ORDERS'):
                if open_orders is None or open_orders>=int(f['maxNumOrders']): reasons.append(name)
            elif name in ('MAX_NUM_ALGO_ORDERS','EXCHANGE_MAX_NUM_ALGO_ORDERS'):
                if typ in ('STOP_LOSS','STOP_LOSS_LIMIT','TAKE_PROFIT','TAKE_PROFIT_LIMIT') and (algo_orders is None or algo_orders>=int(f['maxNumAlgoOrders'])): reasons.append(name)
            elif name=='MAX_POSITION':
                if side=='BUY' and (position is None or D(position)+qty>D(f['maxPosition'])): reasons.append(name)
            elif name=='TRAILING_DELTA':
                if 'trailingDelta' in params:
                    delta=params['trailingDelta']
                    above=(side=='BUY' and typ.startswith('STOP_LOSS')) or (side=='SELL' and typ.startswith('TAKE_PROFIT'))
                    stem='Above' if above else 'Below'
                    if type(delta) is not int or not int(f['minTrailing'+stem+'Delta'])<=delta<=int(f['maxTrailing'+stem+'Delta']): reasons.append(name)
            elif name in ('ICEBERG_PARTS','MAX_NUM_ICEBERG_ORDERS','EXCHANGE_MAX_NUM_ICEBERG_ORDERS'):
                if 'icebergQty' in params: reasons.append('ICEBERG_UNSUPPORTED')
            elif name in ('MAX_NUM_ORDER_AMENDS','MAX_NUM_ORDER_LISTS','EXCHANGE_MAX_NUM_ORDER_LISTS'):
                # These require an explicit per-operation list/amend validator.
                if 'listClientOrderId' in params: reasons.append('ORDER_LIST_COUNT_UNVERIFIED')
            else: reasons.append('UNKNOWN_FILTER:'+name)
        if typ.endswith('_LIMIT') or typ=='LIMIT':
            if 'price' not in params or params.get('timeInForce') not in ('GTC','IOC','FOK'): reasons.append('LIMIT_FIELDS')
        if typ.startswith(('STOP_LOSS','TAKE_PROFIT')) and stop is None and 'trailingDelta' not in params: reasons.append('TRIGGER_MISSING')
        return tuple(sorted(set(reasons)))

