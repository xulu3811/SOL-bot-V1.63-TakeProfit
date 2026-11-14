"""R2-only paper executor; production execution rejects this strategy ID."""
from decimal import Decimal
from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.execution.shadow import ShadowRejected
from solbot.mathematics.decimal_utils import D, floor_step


class R2ShadowExecutor(P3ShadowExecutor):
    allowed_strategies = ('R2',)
    minimum_notional = Decimal('100')
    risk_budget = Decimal('.002')

    def submit_intent(self, intent, now):
        # Retry with the identical request after a crash between ledger commit
        # and outbox acknowledgement, even if the original intent has expired.
        with self._transaction() as db:
            old = db.execute("SELECT * FROM shadow_orders WHERE request_key=?", ('BUY:' + intent['signal_id'],)).fetchone()
            if old:
                return dict(old)
        account = self.snapshot()
        price = floor_step(D(intent['entry_high']), self.rules.tick_size)
        stop = floor_step(D(intent['stop_price']), self.rules.tick_size)
        if not 0 < stop < price:
            raise ShadowRejected('INVALID_STRUCTURE_STOP', 'invalid structural stop')
        risk_fraction = (price-stop)/price + self.fee_rate*2 + Decimal('.001')
        notional = min(self.risk.maximum_notional, D(account['last_equity'])*self.risk_budget/risk_fraction,
                       (D(account['cash'])-D(account['reserved']))/(1+self.fee_rate))
        if notional < self.minimum_notional:
            raise ShadowRejected('R2_RISK_BELOW_MINIMUM', f'risk budget permits less than {self.minimum_notional} USDT')
        candidate = dict(intent, risk_flags=['RESEARCH_PARAMETERS_UNVALIDATED'], stop_price=str(stop))
        return self.submit_candidate(candidate, notional=str(notional), limit_price=str(price), now_ms=now)

    def invalidate_pending(self, close, now):
        with self._transaction() as db:
            import json
            release = Decimal(0)
            for row in db.execute("SELECT * FROM shadow_orders WHERE side='BUY' AND status='PENDING'").fetchall():
                candidate = json.loads(row['candidate_json'])
                if D(close) < D(candidate['candidate_low']):
                    release += D(row['reserved'])
                    db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='R2_LOW_BROKEN' WHERE order_id=?", (row['order_id'],))
            if release:
                account = self._account(db)
                db.execute('UPDATE shadow_account SET reserved=?', (str(D(account['reserved'])-release),))
