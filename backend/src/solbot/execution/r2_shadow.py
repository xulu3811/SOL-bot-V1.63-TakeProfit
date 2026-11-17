"""R2 paper executor aligned with SOL-bot Live entry pricing, risk sizing, and stop bounds."""
from decimal import Decimal
import json

from solbot.execution.live_policy import (
    R2_RISK_BUDGET_RATIO,
    compute_entry_limit_price,
    compute_live_order_size,
    is_custom_trading_config,
    parse_allowed_strategies,
    resolve_stop_distance,
    strategy_entry_allowed,
)
from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.execution.shadow import ShadowRejected
from solbot.mathematics.decimal_utils import D, floor_step


class R2ShadowExecutor(P3ShadowExecutor):
    allowed_strategies = ('R2',)
    default_minimum_notional = Decimal('500')
    default_maximum_notional = Decimal('1000')
    risk_budget = R2_RISK_BUDGET_RATIO

    @property
    def minimum_notional(self):
        return self._min_notional_override if self._min_notional_override is not None else self.default_minimum_notional

    @property
    def maximum_notional(self):
        return self._max_notional_override if self._max_notional_override is not None else self.default_maximum_notional

    def submit_intent(self, intent, now, *, bid=None, ask=None):
        # Retry with the identical request after a crash between ledger commit
        # and outbox acknowledgement, even if the original intent has expired.
        with self._transaction() as db:
            old = db.execute("SELECT * FROM shadow_orders WHERE request_key=?", ('BUY:' + intent['signal_id'],)).fetchone()
            if old:
                return dict(old)
        allowed, deny_reason = strategy_entry_allowed(
            intent.get('strategy_id', 'R2'),
            intent.get('regime'),
            allowed=parse_allowed_strategies(default=('T1', 'T2', 'R2')),
        )
        if not allowed:
            raise ShadowRejected(deny_reason or 'STRATEGY_NOT_ALLOWED', 'strategy disabled under live policy')

        bid_val = bid if bid is not None else intent.get('bid') or intent.get('p3_reference_price')
        ask_val = ask if ask is not None else intent.get('ask') or intent.get('p3_reference_price')
        price, price_err = compute_entry_limit_price(
            'R2',
            intent.get('entry_low'),
            intent.get('entry_high'),
            bid_val,
            ask_val,
            self.rules.tick_size,
        )
        if price is None:
            raise ShadowRejected(price_err or 'CHASE_EXCEEDED', 'R2 limit price rejected by live chase/price guard')

        stop_dist = resolve_stop_distance(intent, entry_ref=intent.get('entry_low') or price)
        if stop_dist is None:
            raise ShadowRejected('INVALID_STRUCTURE_STOP', 'invalid structural stop')

        account = self.snapshot()
        cash_cap = floor_step((D(account['cash']) - D(account['reserved'])) / (Decimal('1') + self.fee_rate), Decimal('0.01'))
        ref_equity = max(Decimal('2000'), D(account['last_equity']))
        is_custom_cfg = is_custom_trading_config(
            {'custom': getattr(self, '_min_notional_override', None) is not None},
            self.minimum_notional,
            self.maximum_notional,
        )
        qty, _order_notional, notional, size_err = compute_live_order_size(
            'R2',
            price,
            cash_cap,
            ref_equity,
            stop_dist,
            fee_rate=self.fee_rate,
            score=intent.get('score') or '75',
            min_order_usdt=self.minimum_notional,
            max_order_usdt=self.maximum_notional,
            is_custom_cfg=is_custom_cfg,
            step_size=self.rules.step_size,
            min_qty=self.rules.min_qty,
            rules_min_notional=self.rules.min_notional,
        )
        if qty is None:
            if size_err == 'R2_SIZE':
                raise ShadowRejected('R2_RISK_BELOW_MINIMUM', 'R2 stop/risk outside live bounds (0.5%-5.0% stop, 1.0% risk budget)')
            raise ShadowRejected(size_err or 'R2_RISK_BELOW_MINIMUM', f'available cash or order size below minimum {self.minimum_notional} USDT')

        stop = floor_step(D(intent['stop_price']), self.rules.tick_size) if intent.get('stop_price') not in (None, '') else floor_step(price * (Decimal('1') - stop_dist), self.rules.tick_size)
        candidate = dict(
            intent,
            risk_flags=['RESEARCH_PARAMETERS_UNVALIDATED'],
            stop_price=str(stop),
            stop_distance=format(stop_dist.normalize(), 'f'),
        )
        return self.submit_candidate(
            candidate,
            notional=str(floor_step(notional, Decimal('0.01'))),
            limit_price=str(price),
            now_ms=now,
            allow_price_below_low=True,
        )

    def invalidate_pending(self, close, now):
        with self._transaction() as db:
            release = Decimal(0)
            for row in db.execute("SELECT * FROM shadow_orders WHERE side='BUY' AND status='PENDING'").fetchall():
                candidate = json.loads(row['candidate_json'])
                if D(close) < D(candidate['candidate_low']):
                    release += D(row['reserved'])
                    db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='R2_LOW_BROKEN' WHERE order_id=?", (row['order_id'],))
            if release:
                account = self._account(db)
                db.execute('UPDATE shadow_account SET reserved=?', (str(D(account['reserved'])-release),))
