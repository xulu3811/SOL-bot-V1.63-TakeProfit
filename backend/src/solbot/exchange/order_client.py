"""Submission never retries. State machine must durably persist before calling."""
import re
from .binance_private import AuthorizationDenied

class OrderClient:
    def __init__(self,client,approval_verifier): self.client,self.approval_verifier=client,approval_verifier
    def place(self,params,approval,*,protective=False):
        params=dict(params)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,36}',params.get('newClientOrderId','')): raise ValueError('valid durable clientOrderId required')
        if self.approval_verifier(params,approval,protective) is not True: raise AuthorizationDenied('Guardian approval rejected')
        return self.client.request('POST','/api/v3/order',params,scope='PROTECT' if protective else 'TRADE')
    def cancel(self,client_order_id,approval,*,symbol='SUIUSDT',protective=False):
        params={'symbol':symbol,'origClientOrderId':client_order_id}
        if self.approval_verifier(params,approval,protective) is not True: raise AuthorizationDenied('cancel ownership rejected')
        return self.client.request('DELETE','/api/v3/order',params,scope='PROTECT' if protective else 'TRADE')
    def order_list(self,kind,params,approval):
        if kind not in ('oco','otoco'): raise ValueError('unsupported order list')
        params=dict(params)
        if self.approval_verifier(params,approval,True) is not True: raise AuthorizationDenied('protection approval rejected')
        return self.client.request('POST','/api/v3/orderList/'+kind,params,scope='PROTECT')

class EngineClient:
    """State-machine bridge; approval_provider must use authenticated Guardian IPC."""
    def __init__(self,order_client,account_reader,approval_provider):
        self.orders,self.reader,self.approval_provider=order_client,account_reader,approval_provider
    def place(self,**params):
        protective=params.get('side')=='SELL'
        approval=self.approval_provider(params,protective=protective)
        return self.orders.place(params,approval,protective=protective)
    def cancel(self,*,symbol,origClientOrderId):
        params={'symbol':symbol,'origClientOrderId':origClientOrderId}
        approval=self.approval_provider(params,protective=True)
        return self.orders.cancel(origClientOrderId,approval,symbol=symbol,protective=True)
    def query(self,*,symbol,origClientOrderId): return self.reader.order(origClientOrderId,symbol)
    def trades(self,*,symbol,orderId):
        result=[]; cursor=0; seen=set()
        for _ in range(100):
            batch=self.reader.trades(symbol,order_id=orderId,from_id=cursor,limit=1000)
            for row in batch:
                ident=int(row['id'])
                if str(row['orderId'])!=str(orderId) or ident in seen or ident<cursor: raise ValueError('ambiguous trade pagination')
                seen.add(ident)
            result.extend(batch)
            if len(batch)<1000: return result
            cursor=max(int(row['id']) for row in batch)+1
        raise ValueError('bounded trade recovery incomplete')
