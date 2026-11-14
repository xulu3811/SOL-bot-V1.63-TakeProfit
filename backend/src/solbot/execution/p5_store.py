"""P5 execution sidecar, durable outbox bridges idempotent Guardian transactions."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import json
import hashlib
from solbot.accounts.allocation_ledger import encoded, now_ms

class OrderStore:
    def __init__(self,path,*,account_id='SUI-live'):
        from solbot.storage.models import resolve_live_account
        self.account_id=resolve_live_account(account_id)[0]
        self.path=Path(path).resolve(); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.tx(init=True) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and 'p5_owner' not in tables: raise ValueError('foreign order database')
            if not tables:
                db.execute("CREATE TABLE p5_owner(version INTEGER,account_id TEXT)")
                db.execute("INSERT INTO p5_owner VALUES(5,?)",(self.account_id,))
                db.execute('CREATE TABLE orders(client_id TEXT PRIMARY KEY,intent_id TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,purpose TEXT NOT NULL,state TEXT NOT NULL,exchange_id TEXT,cumulative TEXT NOT NULL,quote TEXT NOT NULL,last_time INTEGER NOT NULL,terminal TEXT,created_at INTEGER NOT NULL)')
                db.execute('CREATE TABLE order_events(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at INTEGER NOT NULL)')
                db.execute('CREATE TABLE outbox(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE fills(trade_key TEXT PRIMARY KEY,client_id TEXT NOT NULL,payload TEXT NOT NULL)')
                db.execute('CREATE TABLE execution_state(singleton INTEGER PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL)')
                db.execute("INSERT INTO execution_state VALUES(1,'LIVE_OFF','P6_NOT_ACCEPTED')")
            if tuple(db.execute('SELECT * FROM p5_owner').fetchone())!=(5,self.account_id): raise ValueError('schema or account mismatch')
        with closing(self.connect()) as db: db.execute('PRAGMA journal_mode=WAL')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None); db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL'); return db
    @contextmanager
    def tx(self,init=False):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not init and tuple(db.execute('SELECT * FROM p5_owner').fetchone())!=(5,self.account_id): raise ValueError('stale writer')
                yield db; db.commit()
            except BaseException: db.rollback(); raise
    def get(self,client_id):
        with self.tx() as db:
            r=db.execute('SELECT * FROM orders WHERE client_id=?',(client_id,)).fetchone()
            return dict(r) if r else None
    def unknown(self,client_id,reason):
        with self.tx() as db:
            db.execute("UPDATE orders SET state='UNKNOWN' WHERE client_id=? AND state NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED')",(client_id,))
            db.execute("UPDATE execution_state SET state='PAUSED',reason=?",(reason,))
    def backup(self,path):
        destination=Path(path)
        if destination.exists(): raise ValueError('fresh backup required')
        with closing(self.connect()) as src,closing(sqlite3.connect(destination)) as dst: src.backup(dst)
        restored=destination.with_suffix('.restored.sqlite')
        if restored.exists(): raise ValueError('fresh restore required')
        with closing(sqlite3.connect(destination)) as src,closing(sqlite3.connect(restored)) as dst:
            src.backup(dst)
            assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            a,b=[hashlib.sha256('\n'.join(d.iterdump()).encode()).hexdigest() for d in (src,dst)]
            assert a==b
        return {'backup':str(destination),'restore':str(restored),'sha256':a}

