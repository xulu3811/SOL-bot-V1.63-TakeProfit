"""P-256 device proof, short opaque sessions and durable replay protection."""
import base64,hashlib,json,re,secrets,sqlite3,time
from pathlib import Path
from contextlib import contextmanager,closing
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import ec

class AuthError(PermissionError):
    """Authentication/authorization error preserving exact HTTP error code."""

    def __init__(self, code, *, verified_device=False):
        super().__init__(code)
        self.verified_device = bool(verified_device)
def canonical(method,path,body,timestamp,nonce,request_id):
    return '\n'.join((method.upper(),path,hashlib.sha256(body).hexdigest(),str(timestamp),nonce,request_id)).encode()
def hashed(value): return hashlib.sha256(value.encode()).hexdigest()
def pubkey_sha256(pem):
    key=AuthStore.public_key(pem)
    der=key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()

class AuthStore:
    def __init__(self,path,clock=None,enrollment_sha256=None,*,allowed_device_ids=None,allowed_device_models=None,allowed_pubkey_sha256s=None,pairing_locked=False):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock or (lambda:time.time_ns()//1000000)
        self.enrollment=enrollment_sha256.strip() if isinstance(enrollment_sha256,str) and enrollment_sha256.strip() else None
        self.allowed_device_ids=frozenset(x.strip() for x in allowed_device_ids if x and x.strip()) if allowed_device_ids else None
        self.allowed_device_models=frozenset(x.strip() for x in allowed_device_models if x and x.strip()) if allowed_device_models else None
        self.allowed_pubkey_sha256s=frozenset(x.strip().lower() for x in allowed_pubkey_sha256s if x and x.strip()) if allowed_pubkey_sha256s else None
        self.pairing_locked=bool(pairing_locked)
        with self.tx() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and 'app_auth_meta' not in tables: raise ValueError('foreign authentication database')
            db.execute('CREATE TABLE IF NOT EXISTS app_auth_meta(version INTEGER PRIMARY KEY)');db.execute('INSERT OR IGNORE INTO app_auth_meta VALUES(7)')
            if db.execute('SELECT version FROM app_auth_meta').fetchall()!=[(7,)]:raise ValueError('unsupported schema')
            for sql in (
                'CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,public_key TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,revoked INTEGER NOT NULL,created_at INTEGER NOT NULL,passphrase_hash TEXT NOT NULL UNIQUE)',
                'CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,device_id TEXT NOT NULL,expires_at INTEGER NOT NULL,revoked INTEGER NOT NULL)',
                'CREATE TABLE IF NOT EXISTS replays(device_id TEXT,nonce TEXT,request_id TEXT,expires_at INTEGER,UNIQUE(device_id,nonce),UNIQUE(device_id,request_id))',
                'CREATE TABLE IF NOT EXISTS challenges(hash TEXT PRIMARY KEY,device_id TEXT,expires_at INTEGER,used INTEGER)',
                'CREATE TABLE IF NOT EXISTS auth_audit(id INTEGER PRIMARY KEY,device_id TEXT,action TEXT,result TEXT,created_at INTEGER)',
                'CREATE TABLE IF NOT EXISTS rate_limits(subject TEXT PRIMARY KEY,window INTEGER,count INTEGER)',
                'CREATE TABLE IF NOT EXISTS control_requests(request_id TEXT PRIMARY KEY,device_id TEXT,action TEXT,result TEXT,created_at INTEGER)',
                'CREATE TABLE IF NOT EXISTS custody_requests(request_id TEXT PRIMARY KEY,device_id TEXT,asset TEXT,quantity TEXT,rules TEXT,expires_at INTEGER,revoked INTEGER,created_at INTEGER)'):
                db.execute(sql)
    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None,timeout=10)) as db:
            db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise
    def rate(self,subject,limit=120):
        window=self.clock()//60000
        with self.tx() as db:
            db.execute('INSERT INTO rate_limits VALUES(?,?,1) ON CONFLICT(subject) DO UPDATE SET count=CASE WHEN window=excluded.window THEN count+1 ELSE 1 END,window=excluded.window',(subject,window))
            count=db.execute('SELECT count FROM rate_limits WHERE subject=?',(subject,)).fetchone()[0]
        if count>limit:raise AuthError('RATE_LIMITED')
    def issue_pairing_code(self,*args,**kwargs):
        raise AuthError('PAIRING_ROTATION_RETIRED')
    @staticmethod
    def public_key(pem):
        if not isinstance(pem,str) or len(pem)>2048:raise AuthError('INVALID_KEY')
        try:key=serialization.load_pem_public_key(pem.encode())
        except Exception:raise AuthError('INVALID_KEY') from None
        if not isinstance(key,ec.EllipticCurvePublicKey) or not isinstance(key.curve,ec.SECP256R1):raise AuthError('P256_REQUIRED')
        return key
    def _proof(self,key,method,path,body,headers):
        h={k.lower():v for k,v in headers.items()}
        try:
            timestamp=int(h['x-timestamp']);nonce=h['x-nonce'];rid=h['x-request-id']
            if not abs(self.clock()-timestamp)<=30000:raise ValueError()
            if any(not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',v) for v in (nonce,rid)):raise ValueError()
            if len(body)>65536 or len(path)>2048 or len(h['x-signature'])>256:raise ValueError()
            key.verify(base64.b64decode(h['x-signature'],validate=True),canonical(method,path,body,timestamp,nonce,rid),ec.ECDSA(hashes.SHA256()))
        except Exception:raise AuthError('INVALID_SIGNATURE_OR_TIME') from None
        return nonce,rid
    def _consume(self,db,device,nonce,rid,*,verified_device=False):
        db.execute('DELETE FROM replays WHERE expires_at<?',(self.clock(),))
        try:db.execute('INSERT INTO replays VALUES(?,?,?,?)',(device,nonce,rid,self.clock()+90000))
        except sqlite3.IntegrityError:raise AuthError('REPLAY_REJECTED',verified_device=verified_device) from None
    def _session(self,db,device):
        token=secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES(?,?,?,0)',(hashed(token),device,0))
        return {'device_id':device,'session_token':token,'expires_at':0,'permanent':True}
    def _check_device_allowlist(self,device_id=None,device_name=None,pem=None):
        if device_id is not None and self.allowed_device_ids and device_id not in self.allowed_device_ids:
            raise AuthError('UNAUTHORIZED_DEVICE')
        if device_name is not None and self.allowed_device_models and device_name not in self.allowed_device_models:
            raise AuthError('UNAUTHORIZED_DEVICE')
        if pem is not None and self.allowed_pubkey_sha256s and pubkey_sha256(pem) not in self.allowed_pubkey_sha256s:
            raise AuthError('UNAUTHORIZED_DEVICE')
    def pair(self,body,headers,*,path='/api/v1/app/devices/pair'):
        if self.pairing_locked:
            raise AuthError('PAIRING_LOCKED')
        value=json.loads(body);pem=value['public_key_pem'];key=self.public_key(pem)
        nonce,rid=self._proof(key,'POST',path,body,headers)
        secret=value.get('device_passphrase');name=value.get('device_name','Android')
        if not isinstance(secret,str) or not 4<=len(secret)<=128 or not isinstance(name,str) or len(name)>80:raise AuthError('INVALID_PASSPHRASE')
        self._check_device_allowlist(device_name=name,pem=pem)
        if self.enrollment and hashed(secret)!=self.enrollment:raise AuthError('PASSPHRASE_REJECTED')
        fingerprint=hashed(secret)
        with self.tx() as db:
            self._consume(db,'pairing',nonce,rid)
            bound=db.execute('SELECT id,revoked FROM devices WHERE passphrase_hash=?',(fingerprint,)).fetchone()
            if bound:
                db.execute('UPDATE sessions SET revoked=1 WHERE device_id=?',(bound[0],))
                db.execute('DELETE FROM devices WHERE id=?',(bound[0],))
            device=bound[0] if (bound and self.allowed_device_ids and bound[0] in self.allowed_device_ids) else secrets.token_hex(16)
            self._check_device_allowlist(device_id=device,device_name=name,pem=pem)
            try:db.execute('INSERT INTO devices VALUES(?,?,?,?,0,?,?)',(device,pem,name,'operator',self.clock(),fingerprint))
            except sqlite3.IntegrityError:raise AuthError('DEVICE_ALREADY_PAIRED') from None
            db.execute('INSERT INTO auth_audit(device_id,action,result,created_at) VALUES(?,?,?,?)',(device,'PAIR','PERMANENT',self.clock()))
            return self._session(db,device)
    def authenticate(self,method,path,body,headers):
        h={k.lower():v for k,v in headers.items()};device=h.get('x-device-id','')
        if not re.fullmatch(r'[0-9a-f]{32}',device):raise AuthError('UNAUTHENTICATED')
        self._check_device_allowlist(device_id=device)
        self.rate('device:'+device)
        token=h.get('authorization','')
        if not token.startswith('Bearer ') or len(token)>200:raise AuthError('UNAUTHENTICATED')
        with self.tx() as db:
            row=db.execute('SELECT public_key,role,revoked,name FROM devices WHERE id=?',(device,)).fetchone()
            session=db.execute('SELECT device_id,expires_at,revoked FROM sessions WHERE hash=?',(hashed(token[7:]),)).fetchone()
            if not row or row[2] or not session or session[0]!=device or session[2] or (session[1] and session[1]<=self.clock()):raise AuthError('SESSION_EXPIRED_OR_REVOKED')
            self._check_device_allowlist(device_id=device,device_name=row[3],pem=row[0])
            nonce,rid=self._proof(self.public_key(row[0]),method,path,body,h);self._consume(db,device,nonce,rid,verified_device=True)
            db.execute('INSERT INTO auth_audit(device_id,action,result,created_at) VALUES(?,?,?,?)',(device,method+' '+path.split('?')[0],'AUTHENTICATED',self.clock()))
            return {'device_id':device,'role':row[1],'request_id':rid}
    def active(self,device):
        if self.allowed_device_ids and device not in self.allowed_device_ids:return False
        with self.tx() as db:return db.execute('SELECT 1 FROM devices WHERE id=? AND revoked=0',(device,)).fetchone() is not None
    def renew(self,principal):
        self._check_device_allowlist(device_id=principal['device_id'])
        with self.tx() as db:
            if not db.execute('SELECT 1 FROM devices WHERE id=? AND revoked=0',(principal['device_id'],)).fetchone():raise AuthError('REVOKED')
            db.execute('UPDATE sessions SET revoked=1 WHERE device_id=?',(principal['device_id'],))
            return self._session(db,principal['device_id'])
    def challenge(self,device):
        if not isinstance(device,str) or not re.fullmatch(r'[0-9a-f]{32}',device):raise AuthError('UNAUTHENTICATED')
        self._check_device_allowlist(device_id=device)
        token=secrets.token_urlsafe(32)
        with self.tx() as db:
            db.execute('DELETE FROM challenges WHERE expires_at<?',(self.clock(),))
            db.execute('INSERT INTO challenges VALUES(?,?,?,0)',(hashed(token),device,self.clock()+30000))
        return {'challenge':token,'expires_at':self.clock()+30000}
    def open_session(self,body,headers):
        value=json.loads(body);device=value['device_id']
        self._check_device_allowlist(device_id=device)
        with self.tx() as db:
            row=db.execute('SELECT public_key,revoked,name FROM devices WHERE id=?',(device,)).fetchone()
            challenge=db.execute('SELECT device_id,expires_at,used FROM challenges WHERE hash=?',(hashed(value['challenge']),)).fetchone()
            if not row or row[1] or not challenge or challenge[0]!=device or challenge[1]<=self.clock() or challenge[2]:raise AuthError('CHALLENGE_REJECTED')
            self._check_device_allowlist(device_id=device,device_name=row[2],pem=row[0])
            nonce,rid=self._proof(self.public_key(row[0]),'POST','/api/v1/app/session/open',body,headers);self._consume(db,device,nonce,rid)
            db.execute('UPDATE challenges SET used=1 WHERE hash=?',(hashed(value['challenge']),))
            return self._session(db,device)
    def revoke(self,principal,target):
        if target!=principal['device_id'] and principal['role']!='admin':raise AuthError('ADMIN_REQUIRED')
        with self.tx() as db:
            db.execute('UPDATE devices SET revoked=1 WHERE id=?',(target,));db.execute('UPDATE sessions SET revoked=1 WHERE device_id=?',(target,))
            db.execute('INSERT INTO auth_audit(device_id,action,result,created_at) VALUES(?,?,?,?)',(principal['device_id'],'REVOKE:'+target,'OK',self.clock()))
