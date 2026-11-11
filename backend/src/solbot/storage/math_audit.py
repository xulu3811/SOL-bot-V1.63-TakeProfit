"""Immutable compressed math evidence, one result per durable signal identity."""
from contextlib import closing
from pathlib import Path
import hashlib,json,sqlite3,zlib
from solbot.mathematics.decimal_utils import json_safe,canonical_hash
from solbot.mathematics.contracts import ACCOUNTS

def normalize_signal(payload):
    original=json.dumps({k:v for k,v in payload.items() if k!='decision_id'},sort_keys=True,separators=(',',':'),allow_nan=False)
    value=json.loads(original)
    if 'score' in value:value['score']=str(value['score'])
    if 'score_factors' in value:value['score_factors']={k:str(v) for k,v in value['score_factors'].items()}
    value['p2_original_payload_hash']=hashlib.sha256(original.encode()).hexdigest()
    json_safe(value)
    return value,original

class MathAudit:
    def __init__(self,path,symbol):
        self.path=Path(path).resolve();self.symbol=symbol;self.account=ACCOUNTS[symbol]
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and (tables!={'math_owner','math_records'} or db.execute('SELECT symbol,account FROM math_owner').fetchone()!=(symbol,self.account)):
                    raise ValueError('foreign math audit database')
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE IF NOT EXISTS math_owner(symbol TEXT PRIMARY KEY,account TEXT NOT NULL); CREATE TABLE IF NOT EXISTS math_records(signal_id TEXT PRIMARY KEY,created_at INTEGER NOT NULL,input_hash TEXT NOT NULL,compressed BLOB NOT NULL);')
            db.execute('INSERT OR IGNORE INTO math_owner VALUES (?,?)',(symbol,self.account));db.commit()
    def connect(self):return sqlite3.connect(self.path,timeout=5)
    def contains(self,key):
        with closing(self.connect()) as db:return db.execute('SELECT 1 FROM math_records WHERE signal_id=?',(key,)).fetchone() is not None
    def record(self,key,now,inputs,result):
        if inputs['signal']['symbol']!=self.symbol:raise ValueError('audit symbol mismatch')
        document=json_safe({'symbol':self.symbol,'account':self.account,'version':'p3-runtime-1','inputs':inputs,'result':result})
        raw=json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        with closing(self.connect()) as db:
            db.execute('INSERT OR IGNORE INTO math_records VALUES (?,?,?,?)',(key,now,canonical_hash(inputs),zlib.compress(raw,9)));db.commit()
    def read(self,key):
        with closing(self.connect()) as db:row=db.execute('SELECT compressed FROM math_records WHERE signal_id=?',(key,)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None
