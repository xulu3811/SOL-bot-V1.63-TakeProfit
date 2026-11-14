"""Independent, transactional allocation checks with an unconditional P4 live gate."""
from dataclasses import asdict
from solbot.mathematics.decimal_utils import D
from solbot.accounts.allocation_ledger import digest, encoded, now_ms
from .permission_engine import TradeIntent, Permission
from . import risk_policy as p

class Guardian:
    def __init__(self,ledger): self.ledger=ledger

    def evaluate(self,intent,*,at=None,reserve=False):
        """Reserve internal planning only. P4 approvals cannot authorize orders."""
        if not isinstance(intent,TradeIntent): raise ValueError('typed complete intent required')
        at=now_ms() if at is None else at
        fingerprint=digest(intent)
        with self.ledger.transaction() as db:
            a=self.ledger.account(db); h=db.execute('SELECT * FROM risk_high_watermarks').fetchone()
            prior=db.execute('SELECT * FROM fund_reservations WHERE intent_id=?',(intent.intent_id,)).fetchone()
            code='RISK_CHECKS_PASSED'; decision='ALLOW'
            price,qty,stop=D(intent.price),D(intent.quantity),D(intent.stop_price)
            notional=price*qty
            loss_fraction=(price-stop)/price+2*p.FEE+p.SLIPPAGE
            risk=notional*loss_fraction
            entries=[dict(r) for r in db.execute('SELECT * FROM fund_reservations') if not prior or r['intent_id']!=intent.intent_id]
            positions=[dict(r) for r in db.execute('SELECT * FROM robot_positions')]
            slots={r['intent_id'] for r in entries if D(r['remaining'])>0}|{r['intent_id'] for r in positions if D(r['quantity'])>0 and r['intent_id']!=intent.intent_id}
            principal=sum((D(r['remaining']) for r in entries),D('0'))+sum((D(r['cost']) for r in positions if r['intent_id']!=intent.intent_id),D('0'))
            committed=sum((D(r['remaining'])+D(r['fees']) for r in entries),D('0'))
            committed+=sum((D(r[0]) for r in db.execute("SELECT reserve FROM bnb_refill_intents WHERE status IN ('CANDIDATE','UNKNOWN')")),D('0'))
            existing_risk=sum((D(r['risk']) for r in entries if D(r['remaining'])>0),D('0'))+sum((D(r['risk']) for r in positions if r['intent_id']!=intent.intent_id),D('0'))
            balance=db.execute("SELECT * FROM exchange_balances WHERE asset='USDT'").fetchone()
            reasons=[]
            def deny(condition,reason):
                if condition: reasons.append(reason)
            deny(intent.account_id!=self.ledger.account_id or intent.symbol!=self.ledger.symbol,'DENY_ACCOUNT_SYMBOL')
            deny(intent.side!='BUY','DENY_ENTRY_ONLY')
            deny(intent.strategy_id!='T1' and intent.strategy_id!='T2','DENY_R1_NOT_APPROVED')
            deny(intent.strategy_version!=p.STRATEGY_VERSION,'DENY_STRATEGY_VERSION')
            deny(not 0<=at-intent.created_at<=60000 or not at<intent.expires_at<=intent.created_at+60000,'DENY_EXPIRED')
            deny(not 0<=at-intent.market_at<=2000 or not 0<=at-intent.math_at<=5000,'PAUSE_STALE_DATA')
            deny(bool(db.execute('SELECT locked FROM risk_hard_locks').fetchone()[0]),'DENY_HARD_LOCKED')
            deny(not 0<stop<price or (price-stop)/price<D('.008'),'DENY_STOP')
            deny(not p.MIN_ORDER<=notional<=p.MAX_ORDER,'DENY_NOTIONAL')
            deny(not D('75')<=D(intent.score)<=D('100') or notional>D('300')+D('400')*(D(intent.score)-75)/25,'DENY_SCORE_SIZE')
            deny(not 0<=at-a['equity_at']<=5000 or not 0<=at-a['reconciled_at']<=5000 or bool(a['recovery_required']),'PAUSE_RECONCILIATION')
            deny(db.execute("SELECT 1 FROM fund_reservations WHERE status IN ('UNKNOWN','CANCEL_PENDING')").fetchone() is not None,'PAUSE_UNKNOWN_ORDER')
            deny(db.execute("SELECT 1 FROM bnb_refill_intents WHERE status='UNKNOWN'").fetchone() is not None,'PAUSE_UNKNOWN_REFILL')
            deny(D(h['equity'])<=D(h['high'])*D('.95'),'DENY_DRAWDOWN')
            deny(risk>D(h['equity'])*p.T1_RISK or risk+existing_risk>D(h['equity'])*p.TOTAL_RISK,'DENY_RISK_BUDGET')
            deny(len(slots)>=p.MAX_SLOTS or principal+notional>p.MAX_PRINCIPAL,'DENY_EXPOSURE')
            deny(committed+notional*(1+p.FEE)>D(a['cash']),'DENY_CASH')
            deny(balance is None or (balance is not None and committed+notional*(1+p.FEE)>D(balance['free'])),'DENY_EXCHANGE_FREE')
            signal_key='|'.join((intent.account_id,intent.symbol,intent.strategy_id,intent.signal_id))
            other=db.execute('SELECT intent_id FROM fund_reservations WHERE signal_key=?',(signal_key,)).fetchone()
            deny(other is not None and other[0]!=intent.intent_id,'DENY_DUPLICATE_SIGNAL')
            deny(prior is not None and prior['fingerprint']!=fingerprint,'DENY_INTENT_REUSE')
            deny(prior is not None and prior['status']!='RESERVED','DENY_ALREADY_CONSUMED')
            if reasons: code=reasons[0]; decision='PAUSE' if code.startswith('PAUSE') else 'DENY'
            if decision=='ALLOW' and reserve and not prior:
                db.execute('INSERT INTO fund_reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(intent.intent_id,signal_key,fingerprint,encoded(intent),str(notional),str(notional),str(notional*p.FEE),str(risk),str(qty),'0','0','RESERVED',intent.expires_at))
            result=Permission(decision,code,fingerprint,p.VERSION,intent.expires_at)
            event={'intent':asdict(intent),'result':asdict(result),'reasons':reasons,'reserve':reserve,'at':at}
            db.execute('INSERT OR IGNORE INTO risk_decisions VALUES(?,?,?,?,?,?,?)',(digest(event),intent.intent_id,fingerprint,decision,code,encoded(event),at))
            return result

    def authorize_live(self,intent,*,at=None):
        result=self.evaluate(intent,at=at,reserve=False)
        if result.decision!='ALLOW': return result
        with self.ledger.transaction() as db:
            state=self.ledger.account(db)['live_state']
            code='DENY_LIVE_STATE' if state!='LIVE_ON' else 'DENY_NOT_READY'
            final=Permission('DENY',code,result.intent_hash,p.VERSION,result.expires_at)
            payload={'intent':asdict(intent),'final':asdict(final),'missing':'P5 executor, private account adapter, native protection and external acceptance'}
            db.execute('INSERT OR IGNORE INTO risk_decisions VALUES(?,?,?,?,?,?,?)',(digest(payload),intent.intent_id,result.intent_hash,'DENY',code,encoded(payload),now_ms() if at is None else at))
            return final

