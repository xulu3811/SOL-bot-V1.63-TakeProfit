"""Typed private read endpoints; balance deltas never imply robot ownership."""
from solbot.mathematics.decimal_utils import D

class AccountReader:
    def __init__(self,client): self.client=client
    def account(self): return self.client.request('GET','/api/v3/account',{'omitZeroBalances':'false'})
    def api_permissions(self): return self.client.request('GET','/sapi/v1/account/apiRestrictions')
    @staticmethod
    def validate_permissions(data):
        if data.get('enableReading') is not True or data.get('enableSpotAndMarginTrading') is not True:
            raise ValueError('read and Spot permission required')
        prohibited=('enableWithdrawals','enableInternalTransfer','permitsUniversalTransfer','enableMargin','enableFutures','enableVanillaOptions','enablePortfolioMarginTrading')
        if any(data.get(key) is not False for key in prohibited): raise ValueError('API privilege exceeds Spot-only policy or is unknown')
        return True
    def open_orders(self,symbol='SUIUSDT'): return self.client.request('GET','/api/v3/openOrders',{'symbol':symbol})
    def order(self,client_order_id,symbol='SUIUSDT'): return self.client.request('GET','/api/v3/order',{'symbol':symbol,'origClientOrderId':client_order_id})
    def history(self,symbol='SUIUSDT',*,order_id=None,limit=1000):
        params={'symbol':symbol,'limit':self._limit(limit)}
        if order_id is not None: params['orderId']=int(order_id)
        return self.client.request('GET','/api/v3/allOrders',params)
    def trades(self,symbol='SUIUSDT',*,from_id=None,order_id=None,limit=1000):
        params={'symbol':symbol,'limit':self._limit(limit)}
        if from_id is not None: params['fromId']=int(from_id)
        if order_id is not None: params['orderId']=int(order_id)
        return self.client.request('GET','/api/v3/myTrades',params)
    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1<=value<=1000: raise ValueError('invalid page size')
        return value
    @staticmethod
    def normalize_account(data):
        if data.get('canTrade') is not True or 'SPOT' not in data.get('permissions',[]): raise ValueError('SPOT trading permission missing')
        result={}
        for row in data.get('balances',[]):
            asset=row['asset']
            if asset in result: raise ValueError('duplicate account asset')
            result[asset]={'free':str(D(row['free'],nonnegative=True)),'locked':str(D(row['locked'],nonnegative=True))}
        return result
