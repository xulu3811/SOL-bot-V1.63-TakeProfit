"""Durable user-visible FCM reminders; never an order-control dependency."""
from pathlib import Path
from contextlib import closing,contextmanager
import json,sqlite3,time,re,hashlib,base64,urllib.request,urllib.error,stat

KINDS={'STRATEGY_SIGNAL':'策略信号','SIMULATED_TRADE':'模拟交易','LIVE_ORDER':'实盘订单更新','BUY_FILL':'买入成交','STOP_LOSS':'止损状态更新','APTS_EXIT':'APTS止盈更新','HARD_LOCK':'5%风险硬锁','RECONCILIATION_FAILED':'账户对账异常','SERVER_ERROR':'服务器异常','PROTECTION_ERROR':'保护订单异常','MARKET_SYNC':'行情更新'}
HIGH={'LIVE_ORDER','BUY_FILL','STOP_LOSS','APTS_EXIT','HARD_LOCK','RECONCILIATION_FAILED','SERVER_ERROR','PROTECTION_ERROR'}
def now():return time.time_ns()//1000000

def _describe_event(kind,symbol,details):
    sym=symbol or 'SOLUSDT'
    det=details if isinstance(details,dict) else {}
    strat=det.get('strategy') or ''
    status=det.get('status') or ''
    prefix=f'{sym} {strat}'.strip()
    base_title=KINDS.get(kind,'系统提醒')
    title=f'{prefix} · {base_title}' if strat else f'{sym} · {base_title}'
    if kind=='STRATEGY_SIGNAL':
        title=f'{prefix} 买入信号'
        body=f'{prefix} 策略已触发买入信号，请留意执行状态'
        route='strategy'
    elif kind=='BUY_FILL':
        body=f'{prefix} 实盘买单已成交，已挂原生止损保护'
        route='live'
    elif kind=='LIVE_ORDER':
        status_cn={'NEW':'已提交买入委托','PARTIALLY_FILLED':'买单部分成交','CANCELED':'委托已撤单','EXPIRED':'委托已过期未成交'}.get(status,f'订单状态更新 ({status})' if status else '订单状态已更新')
        body=f'{prefix} 实盘{status_cn}'
        route='live'
    elif kind=='APTS_EXIT':
        body=f'{prefix} 持仓已触发 APTS 止盈/止损平仓'
        route='live'
    elif kind=='STOP_LOSS':
        body=f'{prefix} 止损保护单已挂载/更新'
        route='live'
    elif kind=='SIMULATED_TRADE':
        body=f'{prefix} 影子沙盒模拟单已成交'
        route='shadow'
    elif kind=='HARD_LOCK':
        body=f'{sym} 触发 5% 日内风控硬锁，已暂停新开仓'
        route='live'
    elif kind=='RECONCILIATION_FAILED':
        body=f'{sym} 账户对账异常，请检查实盘持仓与挂单'
        route='live'
    else:
        body=f'{sym} {base_title}，请打开应用查看最新状态'
        route='live'
    priority='HIGH' if (kind=='STRATEGY_SIGNAL' or kind in HIGH or kind=='SIMULATED_TRADE') else 'NORMAL'
    return title,body,route,priority

class NotificationStore:
    def __init__(self,path,clock=now):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
        with self.tx() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables-{'notification_tokens','notification_events','notification_delivery','notification_source_cursors'}:raise ValueError('foreign notification database')
            db.execute('CREATE TABLE IF NOT EXISTS notification_tokens(device_id TEXT PRIMARY KEY,token TEXT NOT NULL UNIQUE,active INTEGER NOT NULL,updated_at INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS notification_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,symbol TEXT,created_at INTEGER NOT NULL,details TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS notification_delivery(event_id TEXT NOT NULL,device_id TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL,next_attempt INTEGER NOT NULL,PRIMARY KEY(event_id,device_id))')
            db.execute('CREATE TABLE IF NOT EXISTS notification_source_cursors(source TEXT PRIMARY KEY,cursor INTEGER NOT NULL)')
    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path,isolation_level=None,timeout=5)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise
    def register(self,device_id,token):
        if not isinstance(device_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',device_id):raise ValueError('invalid device')
        if not isinstance(token,str) or not 20<=len(token)<=4096 or not re.fullmatch(r'[A-Za-z0-9_:\-.]+',token):raise ValueError('invalid notification token')
        with self.tx() as db:
            db.execute('DELETE FROM notification_tokens WHERE token=? AND device_id!=?',(token,device_id))
            db.execute('INSERT INTO notification_tokens VALUES(?,?,1,?) ON CONFLICT(device_id) DO UPDATE SET token=excluded.token,active=1,updated_at=excluded.updated_at',(device_id,token,self.clock()))
            db.execute("UPDATE notification_delivery SET state='PENDING',attempts=0,next_attempt=0 WHERE device_id=? AND state IN ('SENDING','RETRY','INVALID')",(device_id,))
        return {'registered':True}
    def revoke(self,device_id):
        with self.tx() as db:
            db.execute('UPDATE notification_tokens SET active=0 WHERE device_id=?',(device_id,))
            db.execute("UPDATE notification_delivery SET state='REVOKED' WHERE device_id=? AND state!='SENT'",(device_id,))
    def publish(self,event_id,kind,symbol,at,details=None):
        if kind not in KINDS or symbol not in (None,'SOLUSDT','SUIUSDT','BNBUSDT') or type(at)is not int:raise ValueError('invalid notification event')
        if not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',event_id):raise ValueError('invalid event ID')
        # Do not permit arbitrary server errors, account records or credentials in a push.
        safe={}
        for key,allowed in {'mode':{'SIMULATED','LIVE'},'strategy':{'T1','R1','T2','R2'},'status':{'NEW','FILLED','PARTIALLY_FILLED','CANCELED','EXPIRED','CLOSED','UNKNOWN','PAUSED','LIVE_OFF','HARD_LOCKED'}}.items():
            value=(details or {}).get(key)
            if value in allowed:safe[key]=value
        body=json.dumps(safe,sort_keys=True)
        with self.tx() as db:
            old=db.execute('SELECT kind,symbol,created_at,details FROM notification_events WHERE event_id=?',(event_id,)).fetchone()
            if old:
                if tuple(old)!=(kind,symbol,at,body):raise ValueError('notification event conflict')
                return False
            db.execute('INSERT INTO notification_events(event_id,kind,symbol,created_at,details) VALUES(?,?,?,?,?)',(event_id,kind,symbol,at,body))
            db.execute("INSERT INTO notification_delivery SELECT ?,device_id,'PENDING',0,0 FROM notification_tokens WHERE active=1",(event_id,))
        return True
    def alerts(self,after=0,limit=50):
        if type(after)is not int or after<0 or type(limit)is not int or not 1<=limit<=500:raise ValueError('invalid pagination')
        with self.tx() as db:rows=[dict(r) for r in db.execute('SELECT * FROM notification_events WHERE sequence>? ORDER BY sequence LIMIT ?',(after,limit))]
        for r in rows:
            det=json.loads(r['details'])
            title,body,route,priority=_describe_event(r['kind'],r.get('symbol'),det)
            r.update(title=title,message=body,route=route,severity=priority,details=det)
        return {'items':rows,'next_cursor':rows[-1]['sequence'] if rows else after,'delivery_is_not_risk_success':True}
    def dispatch(self,sender,device_active=lambda device:False,limit=50):
        if not 1<=limit<=100:raise ValueError('invalid batch')
        if getattr(sender,'enabled',True) is False:return []
        with self.tx() as db:
            rows=[dict(r) for r in db.execute("SELECT d.*,e.kind,e.symbol,e.created_at,e.details,t.token FROM notification_delivery d JOIN notification_events e USING(event_id) JOIN notification_tokens t USING(device_id) WHERE d.state IN ('PENDING','RETRY') AND d.attempts<3 AND d.next_attempt<=? AND t.active=1 LIMIT ?",(self.clock(),limit))]
            for r in rows:db.execute("UPDATE notification_delivery SET state='SENDING',attempts=attempts+1,next_attempt=? WHERE event_id=? AND device_id=?",(self.clock()+60000,r['event_id'],r['device_id']))
        result=[]
        for r in rows:
            if not device_active(r['device_id']):self.revoke(r['device_id']);continue
            # Recheck registered token after claiming, so rotation/revocation is effective.
            with self.tx() as db:current=db.execute('SELECT token,active FROM notification_tokens WHERE device_id=?',(r['device_id'],)).fetchone()
            if not current or not current['active']:
                with self.tx() as db:db.execute("UPDATE notification_delivery SET state='RETRY' WHERE event_id=? AND device_id=? AND state='SENDING'",(r['event_id'],r['device_id']))
                continue
            det=json.loads(r.get('details') or '{}')
            title,body,route,priority=_describe_event(r['kind'],r.get('symbol'),det)
            message={'token':current['token'],'notification':{'title':title,'body':body},'data':{'event_id':r['event_id'],'kind':r['kind'],'symbol':r['symbol'] or '','title':title,'message':body,'route':route,'severity':priority},'android':{'priority':priority,'notification':{'channel_id':'solbot_critical_alarm_v2' if priority=='HIGH' else 'updates','tag':r['event_id'],'sound':'default','default_vibrate_timings':True,'visibility':'PUBLIC'}}}
            try:status=sender(message)
            except Exception:status='RETRY'
            state='SENT' if status=='SENT' else ('INVALID' if status=='UNREGISTERED' else ('FAILED' if r['attempts']+1>=3 else 'RETRY'))
            with self.tx() as db:
                db.execute('UPDATE notification_delivery SET state=?,next_attempt=? WHERE event_id=? AND device_id=?',(state,self.clock()+60000*(r['attempts']+1),r['event_id'],r['device_id']))
                if state=='INVALID':db.execute('UPDATE notification_tokens SET active=0 WHERE device_id=? AND token=?',(r['device_id'],current['token']))
            result.append({'event_id':r['event_id'],'state':state})
        return result
    def recover(self):
        with self.tx() as db:db.execute("UPDATE notification_delivery SET state=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'RETRY' END WHERE state='SENDING' AND next_attempt<=?",(self.clock(),))

class FCMHttpV1:
    """Lazy service-account JWT OAuth, fixed Google endpoints, no key logging."""
    def __init__(self,credential_path,*,enabled=False,clock=time.time,transport=None):
        self.path=Path(credential_path);self.enabled=enabled;self.clock=clock;self.transport=transport or self._post
        self._access=None;self._expires=0;self._project=None
    @staticmethod
    def _post(url,body,headers):
        request=urllib.request.Request(url,data=body,headers=headers,method='POST')
        try:
            with urllib.request.urlopen(request,timeout=10) as response:return response.status,json.loads(response.read(131072))
        except urllib.error.HTTPError as exc:
            try:return exc.code,json.loads(exc.read(131072))
            except Exception:return exc.code,{}
    def _token(self):
        if self._access and self.clock()<self._expires-60:return self._access
        if self.path.is_symlink() or not self.path.is_file():raise PermissionError('FCM credential file unavailable')
        mode=self.path.stat().st_mode
        if __import__('os').name!='nt' and mode&0o077:raise PermissionError('FCM credential permissions too broad')
        data=json.loads(self.path.read_text(encoding='utf-8-sig'))
        if data.get('type')!='service_account' or not re.fullmatch(r'[a-z][a-z0-9-]{4,62}',data.get('project_id','')):raise ValueError('invalid FCM service identity')
        from cryptography.hazmat.primitives import serialization,hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        def b64(v):return base64.urlsafe_b64encode(v).rstrip(b'=')
        at=int(self.clock());header=b64(b'{"alg":"RS256","typ":"JWT"}')
        claims=b64(json.dumps({'iss':data['client_email'],'scope':'https://www.googleapis.com/auth/firebase.messaging','aud':'https://oauth2.googleapis.com/token','iat':at,'exp':at+3600},separators=(',',':')).encode())
        body=header+b'.'+claims;key=serialization.load_pem_private_key(data['private_key'].encode(),password=None)
        assertion=(body+b'.'+b64(key.sign(body,padding.PKCS1v15(),hashes.SHA256()))).decode()
        from urllib.parse import urlencode
        status,response=self.transport('https://oauth2.googleapis.com/token',urlencode({'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion}).encode(),{'Content-Type':'application/x-www-form-urlencoded'})
        if status!=200 or not response.get('access_token'):raise RuntimeError('FCM authorization failed')
        self._access=response['access_token'];self._expires=self.clock()+min(int(response.get('expires_in',3600)),3600);self._project=data['project_id'];return self._access
    def __call__(self,message):
        if not self.enabled:return 'DISABLED'
        if not message.get('token'):raise ValueError('notification token required')
        try:
            token=self._token()
            status,response=self.transport('https://fcm.googleapis.com/v1/projects/'+self._project+'/messages:send',json.dumps({'message':message}).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+token})
            if status==200 and response.get('name'):return 'SENT'
            if status==401:self._access=None
            details=response.get('error',{}).get('details',[])
            if any(d.get('errorCode')=='UNREGISTERED' for d in details):return 'UNREGISTERED'
            return 'RETRY'
        except Exception:
            # Never return exception text, JWT, token or private-key material.
            return 'RETRY'

class SourceEventCollector:
    """Bounded incremental polling of committed service events, no source writes."""
    def __init__(self,reader,store):self.reader,self.store=reader,store
    def poll(self):
        sources=[('SOLUSDT_strategy','decisions','STRATEGY_SIGNAL','SOLUSDT'),('SUIUSDT_strategy','decisions','STRATEGY_SIGNAL','SUIUSDT'),('r2_state','r2_outbox','R2_SIGNAL','SOLUSDT'),('shadow','shadow_fills','SIMULATED_TRADE','SOLUSDT'),('orders','order_events','LIVE_ORDER','SUIUSDT'),('guardian','risk_events','RISK','SUIUSDT')]
        if hasattr(self.reader,'paths'):
            for r2_key in ('r2_shadow_read','r2_shadow'):
                p=self.reader.paths.get(r2_key)
                if p is not None and p.exists():
                    sources.append((r2_key,'shadow_fills','SIMULATED_TRADE','SOLUSDT'))
                    break
        counts={}
        for source,table,kind,symbol in sources:
            with self.store.tx() as db:
                cursor=db.execute('SELECT cursor FROM notification_source_cursors WHERE source=?',(source+':'+table,)).fetchone()
            after=cursor[0] if cursor else 0
            try:rows=self.reader.read(source,'SELECT rowid AS source_sequence,* FROM '+table+' WHERE rowid>? ORDER BY rowid LIMIT 100',(after,))
            except (sqlite3.Error,OSError,KeyError):counts[source]='UNAVAILABLE';continue
            for row in rows:
                event_kind=kind;details={}
                at=row.get('created_at',row.get('event_time',row.get('signal_time',self.store.clock())))
                if kind=='STRATEGY_SIGNAL':
                    details={'strategy':row['strategy_id']}
                    if not row.get('accepted'):event_kind=None
                elif kind=='R2_SIGNAL':
                    try:
                        sig=json.loads(row.get('payload') or '{}')
                    except Exception:
                        sig={}
                    at=int(sig.get('signal_time') or at)
                    expires_at=int(sig.get('expires_at') or 0)
                    stale=(expires_at<self.store.clock()-300000) if expires_at else (at<self.store.clock()-300000)
                    details={'strategy':sig.get('strategy_id') or 'R2'}
                    event_kind='STRATEGY_SIGNAL' if sig.get('accepted') and sig.get('symbol')=='SOLUSDT' and not stale else None
                elif kind=='SIMULATED_TRADE':
                    details={'mode':'SIMULATED'}
                    if source in ('r2_shadow','r2_shadow_read'):details['strategy']='R2'
                elif kind=='LIVE_ORDER':
                    payload=json.loads(row['payload']);symbol=payload.get('symbol',payload.get('s','SOLUSDT' if payload.get('account_id')=='SOL-live' else 'SUIUSDT'))
                    if payload.get('x')=='TRADE' and payload.get('S')=='BUY':event_kind='BUY_FILL'
                    details={'mode':'LIVE','status':payload.get('status',payload.get('X'))}
                elif kind=='RISK':
                    raw=row['kind']
                    try:
                        risk_payload=json.loads(row.get('payload') or '{}')
                    except Exception:
                        risk_payload={}
                    if risk_payload.get('symbol'):
                        symbol=risk_payload['symbol']
                    elif risk_payload.get('account_id')=='SOL-live':
                        symbol='SOLUSDT'
                    if raw=='UNKNOWN_ORDER':
                        event_kind='RECONCILIATION_FAILED'
                    elif raw=='RECONCILE':
                        check=self.reader.read('guardian','SELECT status FROM balance_reconciliation_events WHERE event_id=?',(row['event_id'],))
                        event_kind='RECONCILIATION_FAILED' if check and check[0]['status']=='UNRECONCILED' else None
                    elif raw in ('HARD_LOCK','DRAW_DOWN_LOCK'):event_kind='HARD_LOCK'
                    else:event_kind=None
                if event_kind:
                    identity='source:'+hashlib.sha256((source+':'+table+':'+str(row['source_sequence'])).encode()).hexdigest()
                    self.store.publish(identity,event_kind,symbol,at,details)
                with self.store.tx() as db:db.execute('INSERT INTO notification_source_cursors VALUES(?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor',(source+':'+table,row['source_sequence']))
            counts[source]=len(rows)
        counts['binance_vault']=self._poll_bot_orders()
        # Current hard lock is a persistent condition, not permission to clear it.
        live=self.reader.live_status()
        with self.store.tx() as db:
            row=db.execute("SELECT cursor FROM notification_source_cursors WHERE source='hard_lock_state'").fetchone()
            revision=row[0] if row else 0
        locked=live.get('state')=='HARD_LOCKED'
        if live.get('available') and locked!=bool(revision%2):
            revision+=1
            lock_symbol=live.get('symbol') or ('SOLUSDT' if live.get('account_id')=='SOL-live' else 'SUIUSDT')
            if locked:self.store.publish('hard-lock:'+str(revision),'HARD_LOCK',lock_symbol,self.store.clock())
            with self.store.tx() as db:db.execute("INSERT INTO notification_source_cursors VALUES('hard_lock_state',?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor",(revision,))
        return counts
    def _poll_bot_orders(self):
        try:rows=self.reader.read('binance_vault','SELECT signal_id,strategy_id,status,created_at,filled_at,stop_updated_at FROM bot_orders ORDER BY created_at DESC LIMIT 50',())
        except (sqlite3.Error,OSError,KeyError,TypeError):return 'UNAVAILABLE'
        emitted=0
        kind_map={'NEW':'LIVE_ORDER','FILLED':'BUY_FILL','CANCELED':'LIVE_ORDER','EXPIRED':'LIVE_ORDER','CLOSED':'APTS_EXIT'}
        for row in rows:
            status=str(row.get('status') or '')
            event_kind=kind_map.get(status);signal_id=str(row.get('signal_id') or '')
            if not event_kind or not signal_id:continue
            identity='bot:'+hashlib.sha256(f'{signal_id}:{status}'.encode()).hexdigest()[:32]
            if status=='FILLED':at=int(row.get('filled_at') or row.get('created_at') or 0)
            elif status=='CLOSED':at=int(row.get('stop_updated_at') or row.get('filled_at') or row.get('created_at') or 0)
            else:at=int(row.get('created_at') or 0)
            details={'mode':'LIVE','strategy':row.get('strategy_id') or 'T1','status':status}
            if self.store.publish(identity,event_kind,'SOLUSDT',at or self.store.clock(),details):emitted+=1
        try:
            prot_rows=self.reader.read('binance_vault',"SELECT p.status,p.exchange_order_id,p.placed_at,p.updated_at,COALESCE(c.stage,'S0') AS stage FROM protections p LEFT JOIN custody_apts c USING(intent_id) WHERE p.intent_id='manual:SOL'",())
        except (sqlite3.Error,OSError,KeyError,TypeError):
            try:
                prot_rows=self.reader.read('binance_vault','SELECT status,exchange_order_id,placed_at,updated_at,stage FROM manual_protection WHERE singleton=1',())
            except (sqlite3.Error,OSError,KeyError,TypeError):
                prot_rows=[]
        for pr in prot_rows:
            p_status=str(pr.get('status') or '')
            ex_id=str(pr.get('exchange_order_id') or '')
            stage=str(pr.get('stage') or 'S0')
            if not ex_id or p_status not in ('OPEN','CLOSED'):continue
            p_kind='STOP_LOSS' if p_status=='OPEN' else 'APTS_EXIT'
            p_ident='mprot:'+hashlib.sha256(f'{ex_id}:{p_status}:{stage}'.encode()).hexdigest()[:32]
            p_at=int(pr.get('updated_at') or pr.get('placed_at') or self.store.clock())
            p_details={'mode':'LIVE','status':'FILLED' if p_status=='OPEN' else 'CLOSED'}
            if self.store.publish(p_ident,p_kind,'SOLUSDT',p_at,p_details):emitted+=1
        return emitted
