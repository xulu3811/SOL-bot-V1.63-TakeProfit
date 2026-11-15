"""Independent virtual ledger. No live executor, secrets, clients or network imports.

Orders reserve cash/slots atomically and fill only against a later fresh public
quote. Full-fill/no-depth and the default 0.1% fee are research approximations.
"""
from contextlib import closing, contextmanager
from decimal import Decimal, ROUND_DOWN, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from solbot.risk import RiskContract
from .models import SYMBOLS, amount, identifier, timestamp
from ._storage import verify_existing_store

RESEARCH_FLAGS = frozenset({
    "RESEARCH_PARAMETERS_UNVALIDATED",
    "EXECUTION_QUALITY_UNOBSERVED",
    "R1_LIVE_PERMANENTLY_DISABLED",
    "HIGHER_TIMEFRAME_DOWNTREND",
    "HIGHER_TIMEFRAME_DISAGREEMENT",
})


class ShadowRejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ShadowExecutor:
    storage_kind = "SHADOW"
    risk_policy = "LEGACY_FORCED_LIQUIDATION_EXPERIMENT"

    @property
    def minimum_notional(self):
        return self.risk.minimum_notional

    @property
    def maximum_notional(self):
        return self.risk.maximum_notional

    @property
    def maximum_slots(self):
        override = getattr(self, "_max_slots_override", None)
        return int(override) if override is not None and int(override) >= 1 else self.risk.maximum_slots

    def __init__(self, path, *, symbol="SOLUSDT", account_id="SOL-shadow", fee_rate="0.001"):
        if symbol not in SYMBOLS:
            raise ValueError("unsupported shadow symbol")
        self.path = Path(path).resolve()
        self.symbol, self.account_id = symbol, identifier(account_id, "account_id")
        self.risk = RiskContract()
        self.fee_rate = amount(fee_rate, "fee_rate", zero=True)
        if self.fee_rate > Decimal("0.01"):
            raise ValueError("fee estimate must not exceed one percent")
        verify_existing_store(self.path, self.storage_kind, self.account_id, self.symbol)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS execution_owner (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), kind TEXT NOT NULL,
                account_id TEXT NOT NULL, symbol TEXT NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO execution_owner VALUES (1,?,?,?)", (self.storage_kind, account_id, symbol))
            owner = db.execute("SELECT * FROM execution_owner").fetchone()
            if (owner["kind"], owner["account_id"], owner["symbol"]) != (self.storage_kind, account_id, symbol):
                raise ValueError("execution database belongs to another channel, account or symbol")
            db.execute("""CREATE TABLE IF NOT EXISTS shadow_account (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), cash TEXT NOT NULL,
                reserved TEXT NOT NULL, peak_equity TEXT NOT NULL, last_equity TEXT NOT NULL,
                hard_locked INTEGER NOT NULL CHECK(hard_locked IN (0,1)), lock_reason TEXT,
                fee_rate TEXT NOT NULL, last_quote_ms INTEGER, bid TEXT, ask TEXT)""")
            db.execute("INSERT OR IGNORE INTO shadow_account VALUES (1,'2000','0','2000','2000',0,NULL,?,NULL,NULL,NULL)",
                       (str(self.fee_rate),))
            stored_fee = Decimal(db.execute("SELECT fee_rate FROM shadow_account").fetchone()[0])
            if stored_fee != self.fee_rate:
                raise ValueError("existing account fee estimate cannot change on restart")
            db.execute("""CREATE TABLE IF NOT EXISTS shadow_orders (
                order_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
                account_id TEXT NOT NULL, symbol TEXT NOT NULL, strategy_id TEXT NOT NULL,
                signal_id TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                status TEXT NOT NULL CHECK(status IN ('PENDING','FILLED','CANCELLED','EXPIRED')),
                limit_price TEXT NOT NULL, quantity TEXT NOT NULL, notional TEXT NOT NULL,
                reserved TEXT NOT NULL, position_id TEXT, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, fill_at INTEGER, fill_price TEXT, fee TEXT,
                close_reason TEXT, candidate_json TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS shadow_orders_pending ON shadow_orders(status,created_at,order_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS shadow_positions (
                position_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
                strategy_id TEXT NOT NULL, signal_id TEXT NOT NULL, quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL, entry_cost TEXT NOT NULL, entry_fee TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
                opened_at INTEGER NOT NULL, closed_at INTEGER, exit_price TEXT,
                exit_fee TEXT, realized_pnl TEXT, close_reason TEXT)""")
            position_columns = {row[1] for row in db.execute("PRAGMA table_info(shadow_positions)")}
            if "high_price" not in position_columns:
                db.execute("ALTER TABLE shadow_positions ADD COLUMN high_price TEXT")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _transaction(self):
        with localcontext() as context, closing(self._connect()) as db:
            context.prec = 50
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_owner'").fetchone():
                    owner=db.execute('SELECT kind,account_id,symbol FROM execution_owner WHERE singleton=1').fetchone()
                    if owner and tuple(owner)!=(self.storage_kind,self.account_id,self.symbol):
                        raise ValueError('execution ownership changed; stale writer refused')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _id(self, key):
        return hashlib.sha256(f"{self.account_id}|{self.symbol}|{key}".encode()).hexdigest()

    @staticmethod
    def _account(db):
        return dict(db.execute("SELECT * FROM shadow_account WHERE singleton=1").fetchone())

    @staticmethod
    def _order(db, order_id):
        return dict(db.execute("SELECT * FROM shadow_orders WHERE order_id=?", (order_id,)).fetchone())

    @staticmethod
    def _expire(db, now_ms):
        rows = db.execute("SELECT order_id,reserved FROM shadow_orders WHERE status='PENDING' AND expires_at<=?", (now_ms,)).fetchall()
        released = sum((Decimal(row["reserved"]) for row in rows), Decimal(0))
        if rows:
            account = ShadowExecutor._account(db)
            db.execute("UPDATE shadow_account SET reserved=? WHERE singleton=1", (str(Decimal(account["reserved"]) - released),))
            db.executemany("UPDATE shadow_orders SET status='EXPIRED',reserved='0',close_reason='signal_expired' WHERE order_id=?",
                           [(row["order_id"],) for row in rows])

    def submit_candidate(self, candidate, *, notional="300", now_ms=None, limit_price=None):
        """Create a pending BUY; repeated identical signal requests return its order."""
        now_ms = timestamp(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        if not isinstance(candidate, dict) or candidate.get("accepted") is not True or candidate.get("reasons"):
            raise ShadowRejected("DENY_SIGNAL", "only accepted signals without rejection reasons may be submitted")
        if candidate.get("symbol") != self.symbol:
            raise ShadowRejected("DENY_SYMBOL", "candidate symbol differs from this shadow account")
        strategy = candidate.get("strategy_id")
        if strategy not in getattr(self, 'allowed_strategies', ("T1", "R1", "T2")):
            raise ShadowRejected("DENY_STRATEGY", "unsupported strategy")
        flags = candidate.get("risk_flags", [])
        if not isinstance(flags, list) or any(flag not in RESEARCH_FLAGS for flag in flags):
            raise ShadowRejected("DENY_RISK_FLAG", "candidate contains a hard or unrecognized risk flag")
        signal_id = identifier(candidate.get("signal_id"), "signal_id")
        signal_time, expires_at = timestamp(candidate.get("signal_time")), timestamp(candidate.get("expires_at"))
        low, high = amount(candidate.get("entry_low")), amount(candidate.get("entry_high"))
        price = amount(limit_price if limit_price is not None else candidate["entry_high"])
        size = amount(notional, "notional")
        min_bound = getattr(self, "_effective_minimum_notional", self.minimum_notional)
        if not low <= price <= high or not min_bound <= size <= self.maximum_notional:
            raise ShadowRejected("DENY_SIZE_PRICE", "order must stay within candidate price and account notional bounds")
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 65_536:
            raise ValueError("candidate exceeds 64 KiB")
        key = "BUY:" + signal_id
        fingerprint = self._id(encoded + "|" + str(size.normalize()) + "|" + str(price.normalize()))
        with self._transaction() as db:
            previous = db.execute("SELECT * FROM shadow_orders WHERE request_key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ShadowRejected("DENY_IDEMPOTENCY_CONFLICT", "signal retry changed its order or candidate")
                return dict(previous)
            self._expire(db, now_ms)
            if not signal_time <= now_ms < expires_at:
                raise ShadowRejected("DENY_SIGNAL_TIME", "candidate is expired or from the future")
            account = self._account(db)
            if account["hard_locked"]:
                raise ShadowRejected("DENY_HARD_LOCKED", "five percent account drawdown lock is persistent")
            slots = self._occupied_slots(db)
            if slots >= self.maximum_slots:
                raise ShadowRejected("DENY_MAX_SLOTS", "positions and pending buys occupy at most two slots")
            reserve = size * (1 + self.fee_rate)
            if Decimal(account["cash"]) - Decimal(account["reserved"]) < reserve:
                raise ShadowRejected("DENY_AVAILABLE_CASH", "insufficient unreserved cash including estimated fees")
            quantity = self._quantity(size,price)
            if quantity <= 0:
                raise ShadowRejected("DENY_QUANTITY", "size rounds below the shadow quantity precision")
            order_id = self._id(key)
            db.execute("""INSERT INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,strategy_id,
                signal_id,side,status,limit_price,quantity,notional,reserved,created_at,expires_at,candidate_json)
                VALUES (?,?,?,?,?,?,?,'BUY','PENDING',?,?,?,?,?,?,?)""",
                       (order_id, key, fingerprint, self.account_id, self.symbol, strategy, signal_id,
                        str(price), str(quantity), str(size), str(reserve), now_ms, expires_at, encoded))
            db.execute("UPDATE shadow_account SET reserved=? WHERE singleton=1", (str(Decimal(account["reserved"]) + reserve),))
            return self._order(db, order_id)

    @staticmethod
    def _occupied_slots(db):
        acc = db.execute("SELECT bid FROM shadow_account WHERE singleton=1").fetchone()
        bid = Decimal(acc[0]) if acc and acc[0] and Decimal(acc[0]) > 0 else None
        pending_buys = {
            "p-" + row[0]
            for row in db.execute("SELECT order_id FROM shadow_orders WHERE status='PENDING' AND side='BUY'").fetchall()
        }
        slots = set(pending_buys)
        for row in db.execute("SELECT position_id, quantity, entry_price, entry_cost FROM shadow_positions WHERE status='OPEN'").fetchall():
            pid = row[0]
            if pid in pending_buys:
                slots.add(pid)
                continue
            ref = bid if bid is not None else Decimal(row[2])
            val = Decimal(row[1]) * ref if ref > 0 else Decimal(row[3])
            if val >= Decimal("5"):
                slots.add(pid)
        return len(slots)

    @staticmethod
    def _quantity(size,price):
        return (size / price).quantize(Decimal("0.000000000000000001"), rounding=ROUND_DOWN)

    def submit_sell(self, position_id, limit_price, *, now_ms, request_id, expires_at=None):
        now_ms = timestamp(now_ms)
        expires_at = timestamp(expires_at if expires_at is not None else now_ms + 300_000)
        identifier(position_id, "position_id")
        identifier(request_id, "request_id")
        price = amount(limit_price)
        key = "SELL:" + request_id
        fingerprint = self._id(position_id + "|" + str(price.normalize()))
        with self._transaction() as db:
            previous = db.execute("SELECT * FROM shadow_orders WHERE request_key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ShadowRejected("DENY_IDEMPOTENCY_CONFLICT", "sell retry changed its order")
                return dict(previous)
            self._expire(db, now_ms)
            if expires_at <= now_ms:
                raise ValueError("sell expiration must be later than creation")
            position = db.execute("SELECT * FROM shadow_positions WHERE position_id=? AND status='OPEN'", (position_id,)).fetchone()
            if position is None:
                raise ShadowRejected("DENY_POSITION", "no open position in this account")
            if db.execute("SELECT 1 FROM shadow_orders WHERE position_id=? AND side='SELL' AND status='PENDING'", (position_id,)).fetchone():
                raise ShadowRejected("DENY_QUANTITY_RESERVED", "position already has a pending sell")
            self._before_sell(db,position,price)
            order_id = self._id(key)
            db.execute("""INSERT INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,strategy_id,
                signal_id,side,status,limit_price,quantity,notional,reserved,position_id,created_at,expires_at,candidate_json)
                VALUES (?,?,?,?,?,?,?,'SELL','PENDING',?,?,?,'0',?,?,?,'{}')""",
                       (order_id, key, fingerprint, self.account_id, self.symbol, position["strategy_id"], position["signal_id"],
                        str(price), position["quantity"], str(price * Decimal(position["quantity"])), position_id, now_ms, expires_at))
            return self._order(db, order_id)

    def _before_sell(self,db,position,price):
        """Extension point inside the same reservation transaction."""
        return None

    def cancel(self, order_id):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM shadow_orders WHERE order_id=?", (order_id,)).fetchone()
            if row is None:
                raise ShadowRejected("DENY_ORDER", "unknown shadow order")
            if row["status"] == "PENDING":
                account = self._account(db)
                db.execute("UPDATE shadow_account SET reserved=? WHERE singleton=1", (str(Decimal(account["reserved"]) - Decimal(row["reserved"])),))
                db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='user_cancelled' WHERE order_id=?", (order_id,))
            return self._order(db, order_id)

    def _sell_fill(self, db, position, price, now_ms, reason):
        quantity = Decimal(position["quantity"])
        proceeds = quantity * price
        fee = proceeds * self.fee_rate
        account = self._account(db)
        cash = Decimal(account["cash"]) + proceeds - fee
        pnl = proceeds - fee - Decimal(position["entry_cost"]) - Decimal(position["entry_fee"])
        db.execute("UPDATE shadow_account SET cash=? WHERE singleton=1", (str(cash),))
        db.execute("""UPDATE shadow_positions SET status='CLOSED',closed_at=?,exit_price=?,exit_fee=?,
            realized_pnl=?,close_reason=? WHERE position_id=?""",
                   (now_ms, str(price), str(fee), str(pnl), reason, position["position_id"]))
        return fee

    def _close_dust_positions(self, db, bid, now_ms):
        dust_fills = []
        pending_buys = {
            "p-" + row[0]
            for row in db.execute("SELECT order_id FROM shadow_orders WHERE status='PENDING' AND side='BUY'").fetchall()
        }
        for position in db.execute("SELECT * FROM shadow_positions WHERE status='OPEN'").fetchall():
            pid = position["position_id"]
            if pid in pending_buys:
                continue
            qty = Decimal(position["quantity"])
            if qty * bid < Decimal("5"):
                db.execute(
                    "UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='dust_below_5_usdt' WHERE position_id=? AND side='SELL' AND status='PENDING'",
                    (pid,),
                )
                fee = self._sell_fill(db, position, bid, now_ms, "dust_below_5_usdt_market_exit")
                key = f"DUST_EXIT:{pid}:{now_ms}"
                order_id = self._id(key)
                db.execute(
                    """INSERT OR IGNORE INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,
                        strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,position_id,
                        created_at,expires_at,fill_at,fill_price,fee,close_reason,candidate_json)
                        VALUES (?,?,?,?,?,?,?,'SELL','FILLED',?,?,?,'0',?,?,?,?,?,?,'dust_below_5_usdt_market_exit','{}')""",
                    (order_id, key, order_id, self.account_id, self.symbol, position["strategy_id"], position["signal_id"],
                     str(bid), position["quantity"], str(qty * bid), pid, now_ms, now_ms, now_ms, str(bid), str(fee)),
                )
                order = self._order(db, order_id)
                if order is not None:
                    dust_fills.append(order)
        return dust_fills

    def _mark_risk(self, db, bid, now_ms):
        account = self._account(db)
        positions = db.execute("SELECT * FROM shadow_positions WHERE status='OPEN'").fetchall()
        equity = Decimal(account["cash"]) + sum((Decimal(row["quantity"]) * bid * (1 - self.fee_rate) for row in positions), Decimal(0))
        peak = max(Decimal(account["peak_equity"]), equity)
        locked = bool(account["hard_locked"]) or equity <= peak * (1 - self.risk.maximum_drawdown)
        db.execute("UPDATE shadow_account SET peak_equity=?,last_equity=?,hard_locked=?,lock_reason=? WHERE singleton=1",
                   (str(peak), str(equity), int(locked), "maximum_drawdown_5_percent" if locked else None))
        if locked:
            db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason='hard_drawdown_lock' WHERE status='PENDING'")
            db.execute("UPDATE shadow_account SET reserved='0' WHERE singleton=1")
            for position in positions:
                fee = self._sell_fill(db, position, bid, now_ms, "hard_drawdown_lock")
                key = "HARD_STOP:" + position["position_id"]
                db.execute("""INSERT OR IGNORE INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,
                    strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,position_id,
                    created_at,expires_at,fill_at,fill_price,fee,close_reason,candidate_json)
                    VALUES (?,?,?,?,?,?,?,'SELL','FILLED',?,?,?,'0',?,?,?,?,?,?,'hard_drawdown_lock','{}')""",
                           (self._id(key), key, self._id(key), self.account_id, self.symbol, position["strategy_id"], position["signal_id"],
                            str(bid), position["quantity"], str(Decimal(position["quantity"]) * bid), position["position_id"],
                            now_ms, now_ms, now_ms, str(bid), str(fee)))
        return locked

    def process_quote(self, symbol, bid, ask, timestamp_ms, *, now_ms=None):
        """Observe a fresh quote, expire orders, mark risk and fill eligible limits."""
        if symbol != self.symbol:
            raise ShadowRejected("DENY_SYMBOL", "quote symbol differs from this account")
        quote_ms = timestamp(timestamp_ms)
        now_ms = timestamp(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        bid, ask = amount(bid, "bid"), amount(ask, "ask")
        if bid > ask:
            raise ValueError("crossed quote")
        if not 0 <= now_ms - quote_ms <= 30_000:
            return {"processed": False, "code": "STALE_OR_FUTURE_QUOTE", "fills": []}
        with self._transaction() as db:
            self._expire(db, now_ms)
            account = self._account(db)
            if account["last_quote_ms"] is not None and quote_ms <= account["last_quote_ms"]:
                return {"processed": False, "code": "DUPLICATE_OR_OLD_QUOTE", "fills": []}
            db.execute("UPDATE shadow_account SET last_quote_ms=?,bid=?,ask=? WHERE singleton=1", (quote_ms, str(bid), str(ask)))
            fills = self._close_dust_positions(db, bid, quote_ms)
            if self._mark_risk(db, bid, quote_ms):
                return {"processed": True, "code": "HARD_LOCKED", "fills": fills + self._hard_stop_fills(db, quote_ms)}
            orders = db.execute("SELECT * FROM shadow_orders WHERE status='PENDING' AND created_at<? ORDER BY created_at,order_id", (quote_ms,)).fetchall()
            for order in orders:
                price = ask if order["side"] == "BUY" else bid
                if (order["side"] == "BUY" and price > Decimal(order["limit_price"])) or (order["side"] == "SELL" and price < Decimal(order["limit_price"])):
                    continue
                quantity = Decimal(order["quantity"])
                cost, fee = quantity * price, quantity * price * self.fee_rate
                account = self._account(db)
                if order["side"] == "BUY":
                    position_id = "p-" + order["order_id"]
                    db.execute("UPDATE shadow_account SET cash=?,reserved=? WHERE singleton=1",
                               (str(Decimal(account["cash"]) - cost - fee), str(Decimal(account["reserved"]) - Decimal(order["reserved"]))))
                    db.execute("""INSERT INTO shadow_positions(position_id,account_id,symbol,strategy_id,signal_id,
                        quantity,entry_price,entry_cost,entry_fee,status,opened_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'OPEN',?)""", (position_id, self.account_id, self.symbol, order["strategy_id"],
                            order["signal_id"], str(quantity), str(price), str(cost), str(fee), quote_ms))
                else:
                    position_id = order["position_id"]
                    position = db.execute("SELECT * FROM shadow_positions WHERE position_id=? AND status='OPEN'", (position_id,)).fetchone()
                    if position is None:
                        raise ShadowRejected("DENY_POSITION", "pending sell lost its position")
                    fee = self._sell_fill(db, position, price, quote_ms, "limit_fill")
                db.execute("""UPDATE shadow_orders SET status='FILLED',reserved='0',position_id=?,fill_at=?,
                    fill_price=?,fee=?,close_reason='limit_fill' WHERE order_id=?""", (position_id, quote_ms, str(price), str(fee), order["order_id"]))
                fills.append(self._order(db, order["order_id"]))
                # Risk is a per-fill hard gate. A wide spread on the first buy
                # can breach drawdown before another pending buy is considered.
                if self._mark_risk(db, bid, quote_ms):
                    fills.extend(self._hard_stop_fills(db, quote_ms))
                    return {"processed": True, "code": "HARD_LOCKED", "fills": fills}
            self._manage_t2(db, bid, quote_ms)
            return {"processed": True, "code": "QUOTE_PROCESSED", "fills": fills}

    def _manage_t2(self, db, bid, now_ms):
        """Stop, research trail and 24h exit for T2. T1 positions are untouched."""
        rows = db.execute("SELECT * FROM shadow_positions WHERE status='OPEN' AND strategy_id='T2'").fetchall()
        for position in rows:
            entry = Decimal(position["entry_price"])
            high = max(Decimal(position["high_price"] or entry), bid)
            db.execute("UPDATE shadow_positions SET high_price=? WHERE position_id=?", (str(high), position["position_id"]))
            distance = Decimal("0.008")
            order = db.execute("SELECT candidate_json FROM shadow_orders WHERE signal_id=? AND side='BUY' ORDER BY created_at DESC LIMIT 1", (position["signal_id"],)).fetchone()
            if order and order[0]:
                try:
                    raw = Decimal(str(json.loads(order[0]).get("stop_distance") or "0.008"))
                except Exception:
                    raw = distance
                if Decimal("0.008") <= raw <= Decimal("0.02"):
                    distance = raw
            stop = entry * (1 - distance)
            trail = max(entry * Decimal("1.01"), high * Decimal("0.995")) if high >= entry * Decimal("1.015") else None
            if bid <= stop:
                reason = "t2_stop"
            elif trail is not None and bid <= trail:
                reason = "t2_trail"
            else:
                continue
            db.execute("UPDATE shadow_orders SET status='CANCELLED',reserved='0',close_reason=? WHERE position_id=? AND side='SELL' AND status='PENDING'", (reason, position["position_id"]))
            fee = self._sell_fill(db, position, bid, now_ms, reason)
            key = "T2_EXIT:" + position["position_id"]
            db.execute("""INSERT OR IGNORE INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,
                strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,position_id,
                created_at,expires_at,fill_at,fill_price,fee,close_reason,candidate_json)
                VALUES (?,?,?,?,?,?,?,'SELL','FILLED',?,?,?,'0',?,?,?,?,?,?,?,'{}')""",
                       (self._id(key), key, self._id(key), self.account_id, self.symbol, position["strategy_id"], position["signal_id"],
                        str(bid), position["quantity"], str(Decimal(position["quantity"]) * bid), position["position_id"],
                        now_ms, now_ms, now_ms, str(bid), str(fee), reason))

    @staticmethod
    def _hard_stop_fills(db, quote_ms):
        return [dict(row) for row in db.execute("""SELECT * FROM shadow_orders
            WHERE status='FILLED' AND close_reason='hard_drawdown_lock' AND fill_at=? ORDER BY order_id""", (quote_ms,))]

    def snapshot(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            account = self._account(db)
            account.update(account_id=self.account_id, symbol=self.symbol, mode="SHADOW",
                           risk_policy=self.risk_policy,
                           available_cash=str(Decimal(account["cash"]) - Decimal(account["reserved"])),
                           positions=[dict(row) for row in db.execute("SELECT * FROM shadow_positions WHERE status='OPEN' ORDER BY opened_at,position_id")],
                           pending_orders=[dict(row) for row in db.execute("SELECT * FROM shadow_orders WHERE status='PENDING' ORDER BY created_at,order_id")],
                           fee_model="estimated proportional fee; no depth or partial fills",
                           guardian_status="NOT_IMPLEMENTED_FOR_LIVE", apts_status="NOT_IMPLEMENTED",
                           initial_stop_contract="0.008", profit_protection_contract="0.008", apts_trigger_contract="0.015")
            return account

    def orders(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM shadow_orders ORDER BY created_at DESC,order_id LIMIT ?", (limit,))]

    def positions(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM shadow_positions ORDER BY opened_at DESC,position_id LIMIT ?", (limit,))]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Offline shadow ledger: all inputs are local JSON, no private access")
    parser.add_argument("--database", required=True)
    parser.add_argument("--symbol", default="SOLUSDT", choices=sorted(SYMBOLS))
    parser.add_argument("--account-id", default="SOL-shadow")
    parser.add_argument("--candidate-file")
    parser.add_argument("--quote-file")
    parser.add_argument("--notional", default="300")
    args = parser.parse_args()
    ledger = ShadowExecutor(args.database, symbol=args.symbol, account_id=args.account_id)
    if args.candidate_file:
        candidate = json.loads(Path(args.candidate_file).read_text(encoding="utf-8"))
        print(json.dumps(ledger.submit_candidate(candidate, notional=args.notional)))
    if args.quote_file:
        quote = json.loads(Path(args.quote_file).read_text(encoding="utf-8"))
        print(json.dumps(ledger.process_quote(**quote)))
    print(json.dumps(ledger.snapshot()))


if __name__ == "__main__":
    main()
