"""P3 extension of the P2 virtual ledger, with versioned protection and partial fills.

No exchange client is imported or constructed. Depth participation/slippage are
research assumptions, not real queue simulation. The account hard lock never
liquidates positions by itself; protective exits remain independently active.
"""
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import zlib

from .shadow import ShadowExecutor,ShadowRejected
from .live_policy import DEFAULT_SLIPPAGE_RATE, LIVE_MAX_STOP_DISTANCE, LIVE_MIN_STOP_DISTANCE, compute_entry_limit_price, resolve_stop_distance
from ._storage import verify_existing_store
from .models import timestamp
from solbot.mathematics.contracts import make_context,SymbolRules
from solbot.mathematics.decimal_utils import D,floor_step,ceil_step,canonical_hash,json_safe
from solbot.mathematics.apts_exit import APTSState,update_protection
from solbot.mathematics.protective_stop import initial_stop


class P3ShadowExecutor(ShadowExecutor):
    storage_kind='SHADOW_P3'
    risk_policy='BLOCK_ENTRIES_KEEP_PROTECTION_V1'

    def __init__(self,path,*,rules:SymbolRules,symbol='SOLUSDT',account_id='SOL-shadow',fee_rate='0.001',
                 participation='0.10',slippage_rate='0.0005',minimum_notional=None,maximum_notional=None,maximum_slots=None):
        if symbol!='SOLUSDT' or account_id!='SOL-shadow' or rules.symbol!=symbol:
            raise ValueError('P3 simulation belongs only to SOL-shadow/SOLUSDT')
        self.rules=rules
        self._min_notional_override=D(minimum_notional,positive=True) if minimum_notional is not None else None
        self._max_notional_override=D(maximum_notional,positive=True) if maximum_notional is not None else Decimal('1000')
        self._max_slots_override=int(maximum_slots) if maximum_slots is not None and int(maximum_slots)>=1 else None
        self.participation=D(participation,positive=True)
        self.slippage_rate=D(slippage_rate,nonnegative=True)
        if self.participation>Decimal('.1') or self.slippage_rate>Decimal('.01'):
            raise ValueError('simulation assumptions outside conservative bounds')
        path=Path(path).resolve()
        kind=None
        if path.exists():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables:
                    if 'execution_owner' not in tables:raise ValueError('foreign database')
                    kind=db.execute('SELECT kind FROM execution_owner').fetchone()[0]
        if kind!='SHADOW_P3':
            # Base schema creation/ownership validation is reused; migration only
            # adds extension fields and changes the owner to block P2 writers.
            legacy=ShadowExecutor(path,symbol=symbol,account_id=account_id,fee_rate=fee_rate)
            with legacy._transaction() as db:
                for name,definition in (
                    ('executed_quantity',"TEXT NOT NULL DEFAULT '0'"),
                    ('executed_notional',"TEXT NOT NULL DEFAULT '0'"),
                    ('executed_fee',"TEXT NOT NULL DEFAULT '0'"),
                    ('protection_order',"INTEGER NOT NULL DEFAULT 0")):
                    db.execute(f'ALTER TABLE shadow_orders ADD COLUMN {name} {definition}')
                db.execute('CREATE TABLE shadow_protection(position_id TEXT PRIMARY KEY,state_json TEXT NOT NULL,status TEXT NOT NULL,updated_at INTEGER NOT NULL)')
                db.execute('CREATE TABLE shadow_fills(fill_id TEXT PRIMARY KEY,order_id TEXT NOT NULL,position_id TEXT NOT NULL,event_time INTEGER NOT NULL,side TEXT NOT NULL,quantity TEXT NOT NULL,price TEXT NOT NULL,fee TEXT NOT NULL,input_hash TEXT NOT NULL,assumptions TEXT NOT NULL)')
                db.execute('CREATE TABLE shadow_math_audit(id INTEGER PRIMARY KEY,input_hash TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL,input_json TEXT NOT NULL,output_json TEXT NOT NULL)')
                db.execute('CREATE TABLE shadow_p3_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                # Existing filled orders remain historical records, not newly
                # eligible fills. Existing balances/locks/positions are retained.
                for old_order in db.execute("SELECT order_id,quantity,fill_price,fee FROM shadow_orders WHERE status='FILLED'").fetchall():
                    actual_notional=D(old_order['quantity'])*D(old_order['fill_price'],positive=True)
                    db.execute('UPDATE shadow_orders SET executed_quantity=quantity,executed_notional=?,executed_fee=? WHERE order_id=?',
                        (str(actual_notional),old_order['fee'] or '0',old_order['order_id']))
                db.execute("UPDATE execution_owner SET kind='SHADOW_P3'")
                for key,value in [('risk_policy',self.risk_policy),('participation',str(self.participation)),('slippage_rate',str(self.slippage_rate))]:
                    db.execute('INSERT INTO shadow_p3_metadata VALUES (?,?)',(key,value))
        verify_existing_store(path,'SHADOW_P3',account_id,symbol)
        super().__init__(path,symbol=symbol,account_id=account_id,fee_rate=fee_rate)
        with self._transaction() as db:
            config=dict(db.execute("SELECT key,value FROM shadow_p3_metadata WHERE key IN ('risk_policy','participation','slippage_rate')"))
            if config!={'risk_policy':self.risk_policy,'participation':str(self.participation),'slippage_rate':str(self.slippage_rate)}:
                raise ValueError('persisted P3 policy/assumptions mismatch; explicit migration required')

    @property
    def minimum_notional(self):
        return self._min_notional_override if self._min_notional_override is not None else super().minimum_notional

    @property
    def maximum_notional(self):
        return self._max_notional_override

    @property
    def _effective_minimum_notional(self):
        min_n = self.minimum_notional
        if getattr(self, '_min_notional_override', None) is not None:
            return max(Decimal('5'), min_n - Decimal('2'))
        return min_n

    def apply_trading_config(self, min_order_usdt=None, max_order_usdt=None, max_slots=None, *, auto_scale_virtual_cash=True):
        if min_order_usdt is not None:
            self._min_notional_override = D(min_order_usdt, positive=True)
        if max_order_usdt is not None:
            self._max_notional_override = D(max_order_usdt, positive=True)
        if (
            self._min_notional_override is not None
            and self._max_notional_override is not None
            and self._min_notional_override > self._max_notional_override
        ):
            self._max_notional_override = self._min_notional_override
        if max_slots is not None and int(max_slots) >= 1:
            self._max_slots_override = int(max_slots)
        if auto_scale_virtual_cash and self._max_notional_override is not None:
            required = (self.maximum_notional * Decimal(self.maximum_slots) * Decimal('1.10')).quantize(Decimal('0.01'))
            with self._transaction() as db:
                account = self._account(db)
                open_cost = sum(
                    (Decimal(row[0]) for row in db.execute("SELECT entry_cost FROM shadow_positions WHERE status='OPEN'").fetchall()),
                    Decimal('0'),
                )
                current_cap = Decimal(account['cash']) + open_cost
                peak_eq = Decimal(account['peak_equity'])
                last_eq = Decimal(account['last_equity'])
                if required > current_cap and required > peak_eq:
                    delta = (required - max(current_cap, peak_eq)).quantize(Decimal('0.01'))
                    if delta > 0:
                        db.execute(
                            'UPDATE shadow_account SET cash=?, peak_equity=?, last_equity=? WHERE singleton=1',
                            (
                                str(Decimal(account['cash']) + delta),
                                str(peak_eq + delta),
                                str(last_eq + delta),
                            ),
                        )
                        prev_row = db.execute(
                            "SELECT value FROM shadow_p3_metadata WHERE key='virtual_capital_added'"
                        ).fetchone()
                        prev_added = Decimal(prev_row[0]) if prev_row and prev_row[0] else Decimal('0')
                        db.execute(
                            "INSERT OR REPLACE INTO shadow_p3_metadata(key, value) VALUES ('virtual_capital_added', ?)",
                            (str((prev_added + delta).quantize(Decimal('0.01'))),),
                        )

    def _quantity(self,size,price):
        quantity=floor_step(size/price,self.rules.step_size)
        eff_min=self._effective_minimum_notional
        min_allowed=max(self.rules.min_notional,eff_min-price*self.rules.step_size) if size>=eff_min else self.minimum_notional
        if quantity*price<min_allowed or quantity<self.rules.min_qty:
            raise ShadowRejected('DENY_ROUNDED_SIZE','rounded order falls below required size')
        if quantity>self.rules.max_qty:raise ShadowRejected('DENY_QUANTITY','quantity exceeds exchange rule')
        initial_stop(price,self.rules.tick_size)
        return quantity

    def submit_candidate(self,candidate,*,notional='300',now_ms=None,limit_price=None,allow_price_below_low=False):
        if not isinstance(candidate,dict):raise ValueError('candidate must be a mapping')
        price=D(limit_price if limit_price is not None else candidate.get('entry_high'),positive=True)
        quantity=self._quantity(D(notional,positive=True),price)
        reasons=self.rules.validate_order(price,quantity,'BUY',reference_price=candidate.get('p3_reference_price'),
                                          reference_minutes=candidate.get('p3_reference_minutes'))
        if reasons:raise ShadowRejected('DENY_EXCHANGE_RULES',','.join(reasons))
        return super().submit_candidate(candidate,notional=notional,now_ms=now_ms,limit_price=limit_price,allow_price_below_low=allow_price_below_low)

    def chase_pending(self, features, now_ms):
        """Re-quote unfilled R2/T2 pending BUY orders upward within entry_high, matching Live."""
        now_ms = timestamp(now_ms)
        if not isinstance(features, dict) or not features.get('ready'):
            return []
        stamp = features.get('exchange_time_ms')
        received = features.get('received_time_ms')
        if (
            features.get('synced') is not True
            or type(stamp) is not int
            or type(received) is not int
            or not 0 <= now_ms - stamp <= 3000
            or not 0 <= now_ms - received <= 3000
        ):
            return []
        bid, ask = features.get('bid'), features.get('ask')
        if not bid or not ask:
            return []
        chase_state = getattr(self, '_chase_state', None)
        if chase_state is None:
            chase_state = {}
            self._chase_state = chase_state
        chased = []
        with self._transaction() as db:
            if self._account(db)['hard_locked']:
                return []
            rows = db.execute(
                "SELECT * FROM shadow_orders WHERE side='BUY' AND status='PENDING' "
                "AND protection_order=0 AND executed_quantity='0' "
                "AND strategy_id IN ('R2','T2') AND expires_at>?",
                (now_ms,),
            ).fetchall()
            for row in rows:
                order = dict(row)
                oid = order['order_id']
                count, last_ts = chase_state.get(oid, (0, int(order['created_at'] or 0)))
                if count >= 4 or now_ms - last_ts < 5000:
                    continue
                try:
                    cand = json.loads(order['candidate_json'] or '{}')
                except Exception:
                    continue
                entry_high = cand.get('entry_high')
                if not entry_high:
                    continue
                new_price, _err = compute_entry_limit_price(
                    order['strategy_id'],
                    cand.get('entry_low'),
                    entry_high,
                    bid,
                    ask,
                    self.rules.tick_size,
                )
                if new_price is None or new_price <= D(order['limit_price']):
                    continue
                try:
                    new_qty = self._quantity(D(order['notional']), new_price)
                except ShadowRejected:
                    continue
                reasons = self.rules.validate_order(
                    new_price,
                    new_qty,
                    'BUY',
                    reference_price=cand.get('p3_reference_price'),
                    reference_minutes=cand.get('p3_reference_minutes'),
                )
                if reasons:
                    continue
                db.execute(
                    'UPDATE shadow_orders SET limit_price=?, quantity=? WHERE order_id=?',
                    (str(new_price), str(new_qty), oid),
                )
                chase_state[oid] = (count + 1, now_ms)
                chased.append({'order_id': oid, 'limit_price': str(new_price), 'quantity': str(new_qty), 'chase_count': count + 1})
        return chased

    @staticmethod
    def _occupied_slots(db):
        return ShadowExecutor._occupied_slots(db)

    def _before_sell(self,db,position,price):
        reasons=self.rules.validate_order(price,position['quantity'],'SELL')
        if reasons:raise ShadowRejected('DENY_EXCHANGE_RULES',','.join(reasons))
        buys=db.execute("SELECT order_id,reserved FROM shadow_orders WHERE side='BUY' AND status='PENDING' AND ('p-'||order_id)=?",(position['position_id'],)).fetchall()
        account=self._account(db)
        release=sum((D(row['reserved']) for row in buys),Decimal(0))
        db.execute('UPDATE shadow_account SET reserved=?',(str(D(account['reserved'])-release),))
        db.executemany("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='explicit_exit_entry_cancel' WHERE order_id=?",[(r['order_id'],) for r in buys])

    @staticmethod
    def _expire(db,now_ms):
        rows=db.execute("SELECT order_id,reserved FROM shadow_orders WHERE status='PENDING' AND protection_order=0 AND expires_at<=?",(now_ms,)).fetchall()
        if rows:
            account=ShadowExecutor._account(db)
            release=sum((D(r['reserved']) for r in rows),Decimal(0))
            db.execute('UPDATE shadow_account SET reserved=?',(str(D(account['reserved'])-release),))
            db.executemany("UPDATE shadow_orders SET status='EXPIRED',reserved='0',close_reason='timeout' WHERE order_id=?",[(r['order_id'],) for r in rows])

    def _mark_risk(self,db,bid,now_ms):
        account=self._account(db)
        equity=D(account['cash'])+sum((D(r[0])*bid*(1-self.fee_rate) for r in db.execute("SELECT quantity FROM shadow_positions WHERE status='OPEN'")),Decimal(0))
        peak=max(D(account['peak_equity']),equity)
        locked=bool(account['hard_locked']) or equity<=peak*Decimal('.95')
        db.execute('UPDATE shadow_account SET peak_equity=?,last_equity=?,hard_locked=?,lock_reason=?',
                   (str(peak),str(equity),int(locked),'maximum_drawdown_5_percent' if locked else None))
        if locked:
            # Existing protective/manual SELL orders survive a drawdown lock.
            buys=db.execute("SELECT reserved FROM shadow_orders WHERE side='BUY' AND status='PENDING'").fetchall()
            release=sum((D(r[0]) for r in buys),Decimal(0))
            db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='hard_lock_entry_cancel' WHERE side='BUY' AND status='PENDING'")
            db.execute('UPDATE shadow_account SET reserved=?',(str(D(account['reserved'])-release),))
        return locked

    def _context(self,position,features,now_ms,inputs):
        return make_context(symbol=self.symbol,account_id=self.account_id,strategy_id=position['strategy_id'],
            strategy_version='p3-math-research-1',signal_id=position['signal_id'],decision_time_ms=now_ms,
            features=features,inputs=inputs,parameter_version=self.symbol+'-apts-research-1')

    def _audit(self,db,inputs,outputs,now_ms):
        digest=canonical_hash(inputs)
        db.execute('INSERT OR IGNORE INTO shadow_math_audit(input_hash,created_at,input_json,output_json) VALUES (?,?,?,?)',
                   (digest,now_ms,zlib.compress(json.dumps(json_safe(inputs),sort_keys=True).encode(),9),
                    zlib.compress(json.dumps(json_safe(outputs),sort_keys=True).encode(),9)))
        return digest

    def _signal_stop_distance(self, db, position):
        distance = Decimal('0.008')
        row = db.execute("SELECT candidate_json FROM shadow_orders WHERE signal_id=? AND side='BUY' ORDER BY created_at DESC LIMIT 1", (position['signal_id'],)).fetchone()
        if row and row[0]:
            try:
                payload = json.loads(row[0])
                raw = resolve_stop_distance(payload, entry_ref=position.get('entry_price'))
                if raw is not None and LIVE_MIN_STOP_DISTANCE <= raw <= LIVE_MAX_STOP_DISTANCE:
                    return raw
            except Exception:
                pass
        return distance

    def _t2_stop_price(self, db, position):
        return D(position['entry_price']) * (1 - self._signal_stop_distance(db, position))

    def _protection(self,db,features,evidence,now_ms):
        outputs=[]
        apts_slippage = '0' if self.slippage_rate == 0 else format(DEFAULT_SLIPPAGE_RATE, 'f')
        for raw in db.execute("SELECT * FROM shadow_positions WHERE status='OPEN'").fetchall():
            position=dict(raw)
            context=self._context(position,features,now_ms,{'position':position,'evidence':evidence})
            previous=db.execute('SELECT state_json FROM shadow_protection WHERE position_id=?',(position['position_id'],)).fetchone()
            sig_distance = self._signal_stop_distance(db, position)
            if previous:
                state=APTSState.from_dict(json.loads(previous[0]))
                changed=(state.entry_price!=D(position['entry_price']) or state.quantity!=D(position['quantity']))
                # Added entry partial fills can tighten but never lower the stop.
                # Preserve resolved stop distances (0.5%-5.0% matching SOL-bot Live).
                if sig_distance != Decimal('0.008'):
                    new_stop = max(state.stop_price, initial_stop(position['entry_price'], self.rules.tick_size, stop_distance=sig_distance)) if changed else state.stop_price
                    state=replace(state,entry_price=D(position['entry_price']),quantity=D(position['quantity']),
                        stop_price=new_stop,
                        last_decision_time_ms=now_ms-1 if changed else state.last_decision_time_ms)
                else:
                    new_stop = max(state.stop_price, initial_stop(position['entry_price'], self.rules.tick_size)) if changed else state.stop_price
                    state=replace(state,entry_price=D(position['entry_price']),quantity=D(position['quantity']),
                        stop_price=new_stop,
                        last_decision_time_ms=now_ms-1 if changed else state.last_decision_time_ms)
            else:
                state=APTSState.create(context,position['entry_price'],position['quantity'],self.rules.tick_size,
                    entry_fee_rate=self.fee_rate,exit_fee_rate=self.fee_rate,slippage_rate=apts_slippage,
                    stop_distance=sig_distance,entry_time_ms=int(position.get('opened_at') or now_ms),
                    reversal_confirm_ticks=2)
                if position['strategy_id']=='T2':
                    wide=self._t2_stop_price(db,position)
                    if wide<state.stop_price:
                        state=replace(state,stop_price=wide)
                # Evaluate the first fresh post-fill quote, including an adverse
                # spread crossing the initial stop at the moment of entry.
                state=replace(state,last_decision_time_ms=now_ms-1)
            decision=update_protection(state,context,features['bid'],evidence,tick_size=self.rules.tick_size,quantity=position['quantity'])
            outputs.append(decision.asdict())
            db.execute('INSERT INTO shadow_protection VALUES (?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET state_json=excluded.state_json,status=excluded.status,updated_at=excluded.updated_at',
                (position['position_id'],json.dumps(decision.state.asdict(),sort_keys=True),decision.status,now_ms))
            if decision.exit_required:
                # No new entry remainder may re-open a position after its exit
                # trigger. Other positions are unaffected unless account-locked.
                buys=db.execute("SELECT order_id,reserved FROM shadow_orders WHERE side='BUY' AND status='PENDING' AND ('p-'||order_id)=?",(position['position_id'],)).fetchall()
                account=self._account(db)
                release=sum((D(r['reserved']) for r in buys),Decimal(0))
                db.execute('UPDATE shadow_account SET reserved=?',(str(D(account['reserved'])-release),))
                db.executemany("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='protection_triggered' WHERE order_id=?",[(r['order_id'],) for r in buys])
                pending=db.execute("SELECT * FROM shadow_orders WHERE side='SELL' AND status='PENDING' AND position_id=?",(position['position_id'],)).fetchone()
                if pending and not pending['protection_order']:
                    db.execute("UPDATE shadow_orders SET status='CANCELLED',close_reason='replaced_by_protective_exit' WHERE order_id=?",(pending['order_id'],))
                    pending=None
                if not pending:
                    key='P3_PROTECT:'+position['position_id']
                    order_id=self._id(key)
                    db.execute("""INSERT OR IGNORE INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,position_id,created_at,expires_at,candidate_json,protection_order)
                        VALUES (?,?,?,?,?,?,?,'SELL','PENDING',?,?,?,'0',?,?,?,?,1)""",
                        (order_id,key,self._id(key),self.account_id,self.symbol,position['strategy_id'],position['signal_id'],
                         str(decision.limit_price),position['quantity'],str(decision.limit_price*D(position['quantity'])),
                         position['position_id'],now_ms,now_ms+86_400_000,json.dumps(decision.asdict(),sort_keys=True)))
                else:
                    cur_limit = D(pending['limit_price'])
                    elapsed_ms = now_ms - int(pending['created_at'] or now_ms)
                    target_limit = min(cur_limit, decision.limit_price)
                    if elapsed_ms >= 5000:
                        bid_floor = floor_step(D(features['bid'], positive=True) * (Decimal('1') - self.slippage_rate), self.rules.tick_size)
                        if bid_floor > 0:
                            target_limit = min(target_limit, bid_floor)
                    if target_limit > 0 and target_limit < cur_limit:
                        rem_qty = max(Decimal('0'), D(pending['quantity']) - D(pending['executed_quantity']))
                        db.execute(
                            'UPDATE shadow_orders SET limit_price=?,notional=? WHERE order_id=?',
                            (str(target_limit), str(target_limit * rem_qty), pending['order_id']),
                        )
        return outputs

    def _fill(self,db,order,quantity,price,now_ms,input_hash):
        side=order['side']
        account=self._account(db)
        notional,fee=quantity*price,quantity*price*self.fee_rate
        position_id=order['position_id'] or 'p-'+order['order_id']
        previous_qty=D(order['executed_quantity'])
        total_qty=previous_qty+quantity
        total_cost=D(order['executed_notional'])+notional
        total_fee=D(order['executed_fee'])+fee
        remaining=D(order['quantity'])-total_qty
        if side=='BUY':
            reserve=remaining*D(order['limit_price'])*(1+self.fee_rate)
            cash=D(account['cash'])-notional-fee
            if cash<0:raise ShadowRejected('DENY_AVAILABLE_CASH','fill exceeds cash')
            db.execute('UPDATE shadow_account SET cash=?,reserved=?',
                (str(cash),str(D(account['reserved'])-D(order['reserved'])+reserve)))
            position=db.execute('SELECT * FROM shadow_positions WHERE position_id=?',(position_id,)).fetchone()
            if position:
                if position['status']!='OPEN':raise ValueError('closed position cannot accept another buy fill')
                qty=D(position['quantity'])+quantity
                cost=D(position['entry_cost'])+notional
                db.execute('UPDATE shadow_positions SET quantity=?,entry_price=?,entry_cost=?,entry_fee=? WHERE position_id=?',
                    (str(qty),str(cost/qty),str(cost),str(D(position['entry_fee'])+fee),position_id))
            else:
                db.execute("""INSERT INTO shadow_positions(position_id,account_id,symbol,strategy_id,signal_id,quantity,entry_price,entry_cost,entry_fee,status,opened_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'OPEN',?)""",(position_id,self.account_id,self.symbol,order['strategy_id'],order['signal_id'],str(quantity),str(price),str(notional),str(fee),now_ms))
        else:
            reserve=Decimal(0)
            position=db.execute("SELECT * FROM shadow_positions WHERE position_id=? AND status='OPEN'",(position_id,)).fetchone()
            if position is None or quantity>D(position['quantity']):raise ValueError('sell exceeds remaining position')
            ratio=quantity/D(position['quantity'])
            cost,entry_fee=D(position['entry_cost'])*ratio,D(position['entry_fee'])*ratio
            qty=D(position['quantity'])-quantity
            pnl=D(position['realized_pnl'] or '0')+notional-fee-cost-entry_fee
            db.execute('UPDATE shadow_account SET cash=?',(str(D(account['cash'])+notional-fee),))
            db.execute('UPDATE shadow_positions SET quantity=?,entry_cost=?,entry_fee=?,status=?,closed_at=?,exit_price=?,exit_fee=?,realized_pnl=?,close_reason=? WHERE position_id=?',
                (str(qty),str(D(position['entry_cost'])-cost),str(D(position['entry_fee'])-entry_fee),'OPEN' if qty else 'CLOSED',
                 None if qty else now_ms,str(price),str(D(position['exit_fee'] or '0')+fee),str(pnl),
                 'protective_exit' if order['protection_order'] else 'limit_exit',position_id))
        status='PENDING' if remaining else 'FILLED'
        db.execute('UPDATE shadow_orders SET status=?,reserved=?,position_id=?,executed_quantity=?,executed_notional=?,executed_fee=?,fill_at=?,fill_price=?,fee=?,close_reason=? WHERE order_id=?',
            (status,str(reserve),position_id,str(total_qty),str(total_cost),str(total_fee),now_ms,str(total_cost/total_qty),str(total_fee),'partial_fill' if remaining else 'full_fill',order['order_id']))
        fill_id=self._id(f"{order['order_id']}:{input_hash}")
        db.execute('INSERT INTO shadow_fills VALUES (?,?,?,?,?,?,?,?,?,?)',
            (fill_id,order['order_id'],position_id,now_ms,side,str(quantity),str(price),str(fee),input_hash,'ASSUMED_10_PERCENT_DISPLAYED_DEPTH_WITH_SLIPPAGE'))
        return {'fill_id':fill_id,'side':side,'quantity':str(quantity),'price':str(price),'fee':str(fee),'remaining':str(remaining),'order_id':order['order_id']}

    def _close_dust_positions(self,db,bid,now_ms,digest=''):
        dust_fills=[]
        pending_buys={'p-'+row[0] for row in db.execute("SELECT order_id FROM shadow_orders WHERE status='PENDING' AND side='BUY'").fetchall()}
        for position in db.execute("SELECT * FROM shadow_positions WHERE status='OPEN'").fetchall():
            pid=position['position_id']
            if pid in pending_buys:continue
            qty=D(position['quantity'],nonnegative=True)
            if qty*bid<Decimal('5'):
                db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='dust_below_5_usdt' WHERE position_id=? AND side='SELL' AND status='PENDING'",(pid,))
                db.execute('DELETE FROM shadow_protection WHERE position_id=?',(pid,))
                prev_pnl=D(position['realized_pnl'] or '0') if position['realized_pnl'] is not None else Decimal('0')
                fee=self._sell_fill(db,position,bid,now_ms,'dust_below_5_usdt_market_exit')
                if prev_pnl:
                    row=db.execute('SELECT realized_pnl FROM shadow_positions WHERE position_id=?',(pid,)).fetchone()
                    db.execute('UPDATE shadow_positions SET realized_pnl=? WHERE position_id=?',(str(D(row[0])+prev_pnl),pid))
                key=f'DUST_EXIT:{pid}:{now_ms}'
                order_id=self._id(key)
                notional=qty*bid
                input_hash=digest or self._id(key)
                db.execute("""INSERT OR IGNORE INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,
                    strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,position_id,
                    created_at,expires_at,fill_at,fill_price,fee,close_reason,candidate_json,
                    executed_quantity,executed_notional,executed_fee,protection_order)
                    VALUES (?,?,?,?,?,?,?,'SELL','FILLED',?,?,?,'0',?,?,?,?,?,?,'dust_below_5_usdt_market_exit','{}',?,?,?,1)""",
                    (order_id,key,order_id,self.account_id,self.symbol,position['strategy_id'],position['signal_id'],
                     str(bid),position['quantity'],str(notional),pid,now_ms,now_ms,now_ms,str(bid),str(fee),
                     position['quantity'],str(notional),str(fee)))
                fill_id=self._id(f'{order_id}:{input_hash}')
                db.execute('INSERT OR IGNORE INTO shadow_fills VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (fill_id,order_id,pid,now_ms,'SELL',position['quantity'],str(bid),str(fee),input_hash,'DUST_BELOW_5_USDT_MARKET_EXIT'))
                dust_fills.append({'fill_id':fill_id,'side':'SELL','quantity':position['quantity'],'price':str(bid),'fee':str(fee),'remaining':'0','order_id':order_id,'close_reason':'dust_below_5_usdt_market_exit'})
        return dust_fills

    def process_quote(self,*args,**kwargs):
        raise ValueError('P3 requires synchronized depth inputs via process_market; P2 quote-only execution disabled')

    def process_market(self,features,evidence,*,now_ms):
        now_ms=timestamp(now_ms)
        if features.get('symbol')!=self.symbol:raise ValueError('market/account symbol mismatch')
        stamp=features.get('exchange_time_ms')
        received=features.get('received_time_ms')
        fresh=(features.get('synced') is True and type(stamp) is int and type(received) is int
            and 0<=now_ms-stamp<=3000 and 0<=now_ms-received<=3000)
        if not fresh:
            with self._transaction() as db:self._expire(db,now_ms)
            return {'processed':False,'code':'DATA_UNAVAILABLE_KEEP_PROTECTION','fills':[]}
        bid,ask=D(features['bid'],positive=True),D(features['ask'],positive=True)
        if bid>=ask:raise ValueError('invalid/crossed depth quote')
        if not features.get('ready'):
            evidence=replace(evidence,ready=False)
        digest=canonical_hash({'features':features,'evidence':evidence,'now_ms':now_ms})
        with self._transaction() as db:
            had_exposure=bool(db.execute("SELECT 1 FROM shadow_positions WHERE status='OPEN' UNION SELECT 1 FROM shadow_orders WHERE status='PENDING' LIMIT 1").fetchone())
            last=db.execute("SELECT value FROM shadow_p3_metadata WHERE key='last_update_id'").fetchone()
            if last and int(features['update_id'])<=int(last[0]):return {'processed':False,'code':'DUPLICATE_OR_OLD_DEPTH','fills':[]}
            self._expire(db,now_ms)
            db.execute('UPDATE shadow_account SET last_quote_ms=?,bid=?,ask=?',(stamp,str(bid),str(ask)))
            dust_fills=self._close_dust_positions(db,bid,now_ms,digest)
            locked=self._mark_risk(db,bid,now_ms)
            protections=self._protection(db,features,evidence,now_ms)
            fills=list(dust_fills)
            # Shared per-event liquidity budget prevents each order reusing the
            # same displayed depth. No quantity exists beyond the observed book.
            levels={side:[[D(p,positive=True),floor_step(D(q,nonnegative=True)*self.participation,self.rules.step_size)]
                           for p,q in features['asks' if side=='BUY' else 'bids']] for side in ('BUY','SELL')}
            orders=db.execute("SELECT * FROM shadow_orders WHERE status='PENDING' AND created_at<? ORDER BY protection_order DESC,created_at,order_id",(now_ms,)).fetchall()
            for row in orders:
                order=dict(row)
                if order['side']=='BUY' and (self._account(db)['hard_locked'] or not features.get('ready')):continue
                remaining=D(order['quantity'])-D(order['executed_quantity'])
                available=[]
                for level in levels[order['side']]:
                    p,q=level
                    adjusted=p*(1+self.slippage_rate if order['side']=='BUY' else 1-self.slippage_rate)
                    # Entry/exit is still a protected LIMIT: modeled slippage may
                    # prevent a fill; it may never violate the stated limit.
                    price=(ceil_step(adjusted,self.rules.tick_size)
                           if order['side']=='BUY' else floor_step(adjusted,self.rules.tick_size))
                    if (order['side']=='BUY' and price>D(order['limit_price'])) or (order['side']=='SELL' and price<D(order['limit_price'])):break
                    take=min(remaining,q)
                    if take<=0:continue
                    available.append((level,take,price))
                    remaining-=take
                    if remaining<=0:break
                total=sum((v[1] for v in available),Decimal(0))
                if total<=0:continue
                price=sum((q*p for _,q,p in available),Decimal(0))/total
                for level,q,_ in available:level[1]-=q
                fills.append(self._fill(db,order,total,price,now_ms,digest))
                locked=self._mark_risk(db,bid,now_ms)
                protections=self._protection(db,features,evidence,now_ms)
            db.execute("INSERT INTO shadow_p3_metadata VALUES ('last_update_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(features['update_id']),))
            result={'processed':True,'code':'HARD_LOCKED_PROTECTION_ACTIVE' if locked else 'PROCESSED','fills':fills,'protection':protections,
                    'risk_policy':self.risk_policy,'simulation_model':'conservative displayed-depth participation, no true queue position'}
            if had_exposure or protections or fills:
                self._audit(db,{'features':features,'evidence':evidence,'now_ms':now_ms},result,now_ms)
            return result

    def cancel(self,order_id):
        with closing(self._connect()) as db:
            row=db.execute('SELECT protection_order,status FROM shadow_orders WHERE order_id=?',(order_id,)).fetchone()
            if row and row[0] and row[1]=='PENDING':raise ShadowRejected('DENY_PROTECTION_CANCEL','protective exits cannot be removed by ordinary cancel')
        return super().cancel(order_id)

    def snapshot(self):
        result=super().snapshot()
        result.update(apts_status='RESEARCH_MATH_AND_SIMULATED_PROTECTION',risk_policy=self.risk_policy,
                      fee_model='research proportional fee plus bounded partial depth participation/slippage')
        with closing(self._connect()) as db:
            result['protection']=[{'position_id':r['position_id'],'state':json.loads(r['state_json']),'status':r['status']}
                for r in db.execute("SELECT p.* FROM shadow_protection p JOIN shadow_positions s USING(position_id) WHERE s.status='OPEN'")]
            result['fill_count']=db.execute('SELECT count(*) FROM shadow_fills').fetchone()[0]
        for order in result['pending_orders']:
            order['fill_state']='PARTIALLY_FILLED' if D(order['executed_quantity']) else 'NO_FILL'
        return result
