"""Actual commission asset is retained; unknown conversion must pause valuation."""
from solbot.mathematics.decimal_utils import D

class CommissionService:
    def __init__(self,client): self.client=client
    def rates(self,symbol='SUIUSDT'): return self.client.request('GET','/api/v3/account/commission',{'symbol':symbol})
    @staticmethod
    def conservative_rate(data=None,*,side='BUY',maker=False):
        if not data: return D('.001')
        role='maker' if maker else 'taker'; direction='buyer' if side=='BUY' else 'seller'
        total=D('0')
        for category in ('standardCommission','taxCommission','specialCommission'):
            rates=data.get(category,{})
            total+=D(rates.get(role,'0'),nonnegative=True)+D(rates.get(direction,'0'),nonnegative=True)
        # Discount isn't assumed; do not undercut an unverified fallback.
        return max(D('.001'),total)
    @staticmethod
    def quote_value(amount,asset,prices):
        amount=D(amount,nonnegative=True)
        if amount==0: return amount
        if asset=='USDT': return amount
        if asset not in prices: raise ValueError('unvalued commission asset')
        return amount*D(prices[asset],positive=True)
