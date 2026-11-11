"""Transactional P4 sidecar. P3 databases are never silently upgraded in place."""
from contextlib import contextmanager, closing
from decimal import localcontext
from pathlib import Path
import hashlib
import json
import sqlite3
import time
from solbot.mathematics.decimal_utils import D, json_safe
from solbot.risk import risk_policy as policy

def encoded(value):
    return json.dumps(json_safe(value), sort_keys=True, separators=(',', ':'))

def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()

def now_ms():
    return time.time_ns() // 1000000

SCHEMA = (
    'CREATE TABLE guardian_owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL, account_id TEXT NOT NULL, symbol TEXT NOT NULL)',
    'CREATE TABLE risk_policy_versions(version TEXT PRIMARY KEY, policy_json TEXT NOT NULL)',
    'CREATE TABLE account_allocations(singleton INTEGER PRIMARY KEY CHECK(singleton=1), allocated TEXT NOT NULL, cash TEXT NOT NULL, live_state TEXT NOT NULL, authorization_intent INTEGER NOT NULL, reconciled_at INTEGER NOT NULL, equity_at INTEGER NOT NULL, recovery_required INTEGER NOT NULL)',
    'CREATE TABLE risk_high_watermarks(singleton INTEGER PRIMARY KEY CHECK(singleton=1), high TEXT NOT NULL, equity TEXT NOT NULL, units TEXT NOT NULL, nav_high TEXT NOT NULL)',
    'CREATE TABLE risk_hard_locks(singleton INTEGER PRIMARY KEY CHECK(singleton=1), locked INTEGER NOT NULL, reason TEXT NOT NULL)',
    'CREATE TABLE risk_events(event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL)',
    'CREATE TABLE risk_decisions(decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, fingerprint TEXT NOT NULL, decision TEXT NOT NULL, code TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL)',
    'CREATE TABLE asset_ownership(asset TEXT NOT NULL, category TEXT NOT NULL, quantity TEXT NOT NULL, PRIMARY KEY(asset,category))',
    'CREATE TABLE custody_authorizations(authorization_id TEXT PRIMARY KEY, asset TEXT NOT NULL, quantity TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL)',
    'CREATE TABLE fund_reservations(intent_id TEXT PRIMARY KEY, signal_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, principal TEXT NOT NULL, remaining TEXT NOT NULL, fees TEXT NOT NULL, risk TEXT NOT NULL, quantity TEXT NOT NULL, filled TEXT NOT NULL, cost TEXT NOT NULL, status TEXT NOT NULL, expires_at INTEGER NOT NULL)',
    'CREATE TABLE robot_positions(intent_id TEXT PRIMARY KEY, asset TEXT NOT NULL, quantity TEXT NOT NULL, cost TEXT NOT NULL, risk TEXT NOT NULL, protection TEXT NOT NULL)',
    'CREATE TABLE balance_reconciliation_events(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL)',
    'CREATE TABLE exchange_balances(asset TEXT PRIMARY KEY, free TEXT NOT NULL, locked TEXT NOT NULL, observed_at INTEGER NOT NULL)',
    'CREATE TABLE bnb_refill_intents(intent_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, reserve TEXT NOT NULL, attempts INTEGER NOT NULL)',
    'CREATE TABLE exit_reservations(intent_id TEXT PRIMARY KEY, asset TEXT NOT NULL, category TEXT NOT NULL, quantity TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL)',
    'CREATE TABLE pending_actions(action_id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL)',
)

class AllocationLedger:
    def __init__(self, path, account_id='SUI-live', symbol='SUIUSDT'):
        if account_id != 'SUI-live' or symbol != 'SUIUSDT':
            raise ValueError('P4 production allocation is SUI-live/SUIUSDT only')
        self.path = Path(path).resolve()
        self.account_id, self.symbol = account_id, symbol
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(initializing=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables:
                for sql in SCHEMA: db.execute(sql)
                for table in ('risk_events','risk_decisions','risk_policy_versions','balance_reconciliation_events'):
                    for operation in ('UPDATE','DELETE'):
                        db.execute(f"CREATE TRIGGER immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable audit'); END")
                db.execute('INSERT INTO guardian_owner VALUES(1,4,?,?)', (account_id,symbol))
                db.execute('INSERT INTO risk_policy_versions VALUES(?,?)', (policy.VERSION, encoded({'drawdown':policy.MAX_DRAWDOWN,'capital':policy.CAPITAL,'minimum':policy.MIN_ORDER,'maximum':policy.MAX_ORDER,'total':policy.MAX_PRINCIPAL,'slots':policy.MAX_SLOTS})))
                db.execute("INSERT INTO account_allocations VALUES(1,'2000','2000','LIVE_OFF',0,0,0,1)")
                db.execute("INSERT INTO risk_high_watermarks VALUES(1,'2000','2000','2000','1')")
                db.execute("INSERT INTO risk_hard_locks VALUES(1,0,'')")
            elif 'guardian_owner' not in tables:
                raise ValueError('foreign schema: P4 requires a separate database')
            self.check_owner(db)
        with closing(self.connect()) as db: db.execute('PRAGMA journal_mode=WAL')

    def connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA busy_timeout=15000')
        return db

    def check_owner(self, db):
        r = db.execute('SELECT * FROM guardian_owner').fetchone()
        if r is None or tuple(r) != (1,4,self.account_id,self.symbol):
            raise ValueError('schema or allocation identity changed: stale writer refused')

    @contextmanager
    def transaction(self, initializing=False):
        with closing(self.connect()) as db, localcontext() as ctx:
            ctx.prec = 50
            db.execute('BEGIN IMMEDIATE')
            try:
                if not initializing: self.check_owner(db)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def event(db, event_id, kind, payload, at):
        body=encoded(payload)
        old=db.execute('SELECT kind,payload FROM risk_events WHERE event_id=?',(event_id,)).fetchone()
        if old:
            if tuple(old)!=(kind,body): raise ValueError('event ID reused with different content')
            return False
        db.execute('INSERT INTO risk_events VALUES(?,?,?,?)',(event_id,kind,body,at))
        return True

    @staticmethod
    def account(db): return dict(db.execute('SELECT * FROM account_allocations').fetchone())

    @staticmethod
    def owned(db,asset,category='ROBOT_OWNED'):
        row=db.execute('SELECT quantity FROM asset_ownership WHERE asset=? AND category=?',(asset,category)).fetchone()
        return D(row[0]) if row else D('0')

    @staticmethod
    def set_owned(db,asset,category,quantity):
        quantity=D(quantity,nonnegative=True)
        db.execute('INSERT INTO asset_ownership VALUES(?,?,?) ON CONFLICT(asset,category) DO UPDATE SET quantity=excluded.quantity',(asset,category,str(quantity)))

    def snapshot(self):
        with self.transaction() as db:
            result=self.account(db)
            result.update(dict(db.execute('SELECT * FROM risk_high_watermarks').fetchone()))
            result.update(dict(db.execute('SELECT * FROM risk_hard_locks').fetchone()))
            result['positions']=[dict(r) for r in db.execute('SELECT * FROM robot_positions')]
            result['reservations']=[dict(r) for r in db.execute('SELECT * FROM fund_reservations')]
            result['pending_actions']=[dict(r) for r in db.execute('SELECT * FROM pending_actions')]
            result['policy_version']=policy.VERSION
            result['live_ready']=False
            return result

    def backup_restore(self, destination):
        """Online backup and isolated restore; never copies a live WAL main file."""
        target=Path(destination).resolve()
        restored=target.with_suffix('.restored.sqlite')
        if target == self.path or target.exists() or restored.exists(): raise ValueError('fresh isolated backup paths required')
        target.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as src, closing(sqlite3.connect(target)) as dst: src.backup(dst)
        with closing(sqlite3.connect(target)) as src, closing(sqlite3.connect(restored)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('restore integrity failed')
            hashes=[hashlib.sha256('\n'.join(c.iterdump()).encode()).hexdigest() for c in (src,dst)]
            if hashes[0]!=hashes[1]: raise ValueError('restore contents differ')
        return {'backup':str(target),'restored':str(restored),'logical_sha256':hashes[0],'integrity':'ok'}
