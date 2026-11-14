"""Common comparison view, preserving simulation provenance and separate symbols."""
from dataclasses import dataclass

@dataclass(frozen=True)
class OrderView:
    account_id: str
    symbol: str
    order_id: str
    state: str
    filled_quantity: str
    mode: str
    exchange_confirmed: bool

def shadow_view(row):
    if row['account_id']!='SOL-shadow' or row['symbol']!='SOLUSDT': raise ValueError('wrong simulation account')
    state={'PENDING':'PARTIALLY_FILLED' if row.get('executed_quantity','0')!='0' else 'NEW','CANCELLED':'CANCELED'}.get(row['status'],row['status'])
    return OrderView(row['account_id'],row['symbol'],row['order_id'],state,row.get('executed_quantity','0'),'SIMULATED',False)

def live_view(row,account_id=None):
    import json
    from solbot.storage.models import LIVE_ACCOUNT_BY_SYMBOL
    payload=json.loads(row['payload'])
    acct=account_id or payload.get('account_id') or LIVE_ACCOUNT_BY_SYMBOL.get(payload.get('symbol'),'SUI-live')
    return OrderView(acct,payload['symbol'],row['client_id'],row['state'],row['cumulative'],'LIVE',row['exchange_id'] is not None and row['state']!='UNKNOWN')
