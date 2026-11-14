"""TLS-only, signed app façade. No order submission or secret retrieval route."""
import json,sqlite3,uuid,time,os,logging,threading
from decimal import Decimal
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException,WebSocket,WebSocketDisconnect
from fastapi.responses import JSONResponse
from .auth import AuthStore,AuthError
from .data import DataReader
from .notifications import NotificationStore

PREFIX='/api/v1/app'
def _quote(item,mark,source):
    from decimal import Decimal
    try:
        entry,qty,price=Decimal(str(item.get('entry_price') or item.get('cost'))),Decimal(str(item.get('quantity'))),Decimal(str(mark))
        if entry<=0:raise ValueError
    except Exception:
        return
    change=(price-entry)/entry*Decimal('100')
    pnl=(price-entry)*qty
    item.update(mark_price=format(price,'f'),change_pct=f'{change:+.2f}%',pnl_usdt=f'{pnl:+.2f}',price_source=source)
def create_app(auth,reader,notifications,control,vault=None):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.middleware('http')
    async def authorize(request,call_next):
        if request.url.path=='/healthz':return JSONResponse({'status':'ok','live_enabled':False})
        if request.url.scheme!='https':return JSONResponse({'error':'HTTPS_REQUIRED'},status_code=426)
        try:
            auth.rate('ip:'+(request.client.host if request.client else 'unknown'),300)
            length=request.headers.get('content-length')
            if length and int(length)>65536:return JSONResponse({'error':'REQUEST_TOO_LARGE'},status_code=413)
            body=await request.body()
            if len(body)>65536:return JSONResponse({'error':'REQUEST_TOO_LARGE'},status_code=413)
            path=request.url.path+('?' + request.url.query if request.url.query else '')
            public={PREFIX+'/devices/pair',PREFIX+'/session/challenge',PREFIX+'/session/open'}
            if request.url.path not in public:
                request.state.principal=auth.authenticate(request.method,path,body,request.headers)
            else:auth.rate('bootstrap:'+(request.client.host if request.client else 'unknown'),15)
            return await call_next(request)
        except AuthError as exc:return JSONResponse({'error':str(exc)},status_code=401)
        except (ValueError,KeyError,TypeError):return JSONResponse({'error':'INVALID_REQUEST'},status_code=400)
        except (sqlite3.Error,OSError):return JSONResponse({'error':'SERVICE_UNAVAILABLE'},status_code=503)
    def principal(request):return request.state.principal
    def bounded(value,default=50):
        number=int(value if value is not None else default)
        if not 1<=number<=200:raise ValueError('page size')
        return number
    @app.post(PREFIX+'/devices/pair')
    async def pair(request:Request):return auth.pair(await request.body(),request.headers)
    @app.post(PREFIX+'/session/challenge')
    async def challenge(request:Request):return auth.challenge((await request.json())['device_id'])
    @app.post(PREFIX+'/session/open')
    async def open_session(request:Request):return auth.open_session(await request.body(),request.headers)
    @app.post(PREFIX+'/session/renew')
    async def renew(request:Request):return auth.renew(principal(request))
    @app.post(PREFIX+'/devices/revoke')
    async def revoke(request:Request):
        target=(await request.json())['device_id'];auth.revoke(principal(request),target);notifications.revoke(target)
        # Revocation also cancels any outstanding control authority immediately.
        result=control.action('disable',principal(request))
        try:account_vault().disable_live()
        except Exception:pass
        return {'revoked':True,'control':result}
    @app.get(PREFIX+'/status')
    async def status():return reader.status()
    @app.get(PREFIX+'/markets')
    async def markets(symbol:str='SOLUSDT',timeframe:str='1m',limit:int=100):
        if symbol!='SOLUSDT':raise HTTPException(status_code=400,detail='unsupported market')
        return reader.markets(symbol,timeframe,bounded(limit))
    @app.get(PREFIX+'/strategies')
    async def strategies(symbol:str='SOLUSDT',limit:int=50):return reader.strategies(symbol,bounded(limit))
    def _build_readiness(live_status_doc, risk_doc=None):
        from decimal import Decimal
        state = str(live_status_doc.get('state') or 'UNKNOWN')
        try:
            acct_status = account_vault().status() or {}
        except Exception:
            acct_status = {}
        try:
            pos_snap = reader.live_positions() or {}
        except Exception:
            pos_snap = {}
        try:
            open_bots = account_vault().open_bot_slots() or []
        except Exception:
            open_bots = []
        acct = pos_snap.get('account') if isinstance(pos_snap.get('account'), dict) else {}
        usdt = acct.get('USDT') if isinstance(acct.get('USDT'), dict) else {}
        bnb = acct.get('BNB') if isinstance(acct.get('BNB'), dict) else {}
        sol = acct.get('SOL') if isinstance(acct.get('SOL'), dict) else {}
        def _dec(v):
            try:
                return Decimal(str(v or '0'))
            except Exception:
                return Decimal('0')
        free_usdt = _dec(usdt.get('free'))
        total_usdt = _dec(usdt.get('total')) if usdt.get('total') is not None else free_usdt + _dec(usdt.get('locked'))
        free_bnb = _dec(bnb.get('free')) + _dec(bnb.get('locked'))
        free_sol = _dec(sol.get('free')) + _dec(sol.get('locked'))
        guardian_slots = pos_snap.get('slots') if isinstance(pos_snap.get('slots'), list) else []
        open_slots = max(len(open_bots), len(guardian_slots))
        configured = bool(acct_status.get('configured'))
        orders_enabled = bool(acct_status.get('orders_enabled'))
        if state == 'HARD_LOCKED':
            blocker = 'HARD_LOCKED'
        elif state != 'LIVE_ON':
            blocker = 'LIVE_OFF'
        elif not configured:
            blocker = 'API_NOT_CONFIGURED'
        elif not orders_enabled:
            blocker = 'TRADE_PERMISSION_REQUIRED'
        elif open_slots >= 2:
            blocker = 'SLOTS_FULL'
        elif free_usdt < Decimal('300'):
            blocker = 'INSUFFICIENT_FREE_USDT'
        else:
            blocker = None
        dd_pct = (risk_doc or {}).get('drawdown_pct')
        if dd_pct is None:
            try:
                dd_raw = Decimal(str((risk_doc or {}).get('drawdown') or '0'))
                dd_pct = f'{(dd_raw * Decimal("100")):.2f}'
            except Exception:
                dd_pct = '0.00'
        return {
            'free_usdt': f'{free_usdt:.2f}',
            'total_usdt': f'{total_usdt:.2f}',
            'free_bnb': f'{free_bnb:.4f}',
            'free_sol': f'{free_sol:.3f}',
            'min_order_usdt': '300.00',
            'max_order_usdt': '700.00',
            'max_slots': 2,
            'open_slots': open_slots,
            'configured': configured,
            'orders_enabled': orders_enabled,
            'can_open_position': blocker is None,
            'blocker': blocker,
            'daily_drawdown_pct': str(dd_pct),
            'hard_circuit_pct': str((risk_doc or {}).get('hard_limit_pct') or '5.00'),
        }
    def present_live_status():
        base=dict(reader.live_status() or {})
        try: risk=dict(reader.live_risk() or {})
        except Exception: risk={}
        if base.get('state')!='HARD_LOCKED':
            try: user=account_vault().live_state()
            except Exception: user='LIVE_OFF'
            if user in ('LIVE_ON','LIVE_OFF','PAUSED','HARD_LOCKED'):
                base['state']=user
                base['live_state']=user
                base['available']=True
        if base.get('account_id') in (None,'SUI-live'):base['account_id']='SOL-live'
        if base.get('symbol') in (None,'SUIUSDT'):base['symbol']='SOLUSDT'
        readiness=_build_readiness(base,risk)
        base['readiness']=readiness
        base['orders_enabled']=readiness['orders_enabled']
        base['configured']=readiness['configured']
        allowed_fn=getattr(reader,'_allowed_live_strategies',None)
        base['live_strategies']=list(allowed_fn()) if callable(allowed_fn) else ['T1']
        return base
    def present_live_risk():
        base=dict(reader.live_risk() or {})
        status_doc=present_live_status()
        if status_doc.get('state'):
            base['state']=status_doc['state']
            base['live_state']=status_doc['state']
        if base.get('account_id') in (None,'SUI-live'):base['account_id']='SOL-live'
        if base.get('symbol') in (None,'SUIUSDT'):base['symbol']='SOLUSDT'
        base['readiness']=status_doc.get('readiness')
        return base
    def present_live_orders(limit:int=50,offset:int=0):
        base=dict(reader.live_orders(limit,offset) or {})
        try: bot_rows=account_vault().bot_orders(limit,0) or []
        except Exception: bot_rows=[]
        try: manual_rows=(reader.live_positions() or {}).get('manual_orders') or []
        except Exception: manual_rows=[]
        annotated_manual=[dict(row,source='MANUAL') for row in manual_rows]
        if not base.get('available') and (bot_rows or annotated_manual):
            base={'available':True,'mode':'LIVE','symbol':'SOLUSDT','items':[],'offset':offset,'limit':limit}
        base['bot_orders']=bot_rows
        base['manual_orders']=annotated_manual
        if not base.get('items'):
            combined=list(bot_rows)+list(annotated_manual)
            combined.sort(key=lambda r:int(r.get('time') or r.get('created_at') or 0),reverse=True)
            base['items']=combined[:limit]
        return base
    @app.get(PREFIX+'/live/status')
    async def live_status():return present_live_status()
    @app.get(PREFIX+'/live/orders')
    async def live_orders(limit:int=50,offset:int=0):
        if not 0<=offset<=100000:raise ValueError('offset')
        return present_live_orders(bounded(limit),offset)
    def present_positions(device):
        result=reader.live_positions()
        now=auth.clock()
        with auth.tx() as db:
            rows=db.execute('SELECT request_id,rules,expires_at,revoked FROM custody_requests WHERE device_id=?',(device,)).fetchall()
        active={}
        for request_id,rules,expires_at,revoked in rows:
            if revoked or expires_at<=now or not str(rules).startswith('POSITION:'):continue
            active[str(rules).split(':',1)[1]]=request_id
        try:
            prot=account_vault().protection() or {}
            fresh=account_vault().mark() or {}
            bot_slots=account_vault().open_bot_slots() or []
        except Exception:
            prot,fresh,bot_slots={}, {}, []
        slots=list(result.get('slots') or [])
        seen_intents={str(item.get('intent_id') or '') for item in slots}
        added_bot_qty=Decimal('0')
        for bs in bot_slots:
            if str(bs.get('intent_id') or '') not in seen_intents:
                slots.append(bs)
                seen_intents.add(str(bs.get('intent_id') or ''))
                try: added_bot_qty+=Decimal(str(bs.get('quantity') or '0'))
                except Exception: pass
        result['slots']=slots
        manual_list=list(result.get('manual') or [])
        if added_bot_qty>0 and manual_list and not reader.paths.get('binance_vault'):
            rem=Decimal(str(manual_list[0].get('quantity') or '0'))-added_bot_qty
            if rem>=Decimal('0.001') or prot.get('status') in ('OPEN','EXITING'):
                manual_list[0]['quantity']=reader._num(max(Decimal('0'),rem))
            else:
                manual_list=[]
            result['manual']=manual_list
        result['items']=slots+manual_list
        for item in slots+manual_list:
            intent=str(item.get('intent_id') or '')
            item['custody_on']=intent in active or (intent=='manual:SOL' and prot.get('status')=='OPEN')
            item['authorization_id']=active.get(intent)
            if intent=='manual:SOL':
                item['custody_mode']='FROM_CUSTODY_START_PRICE'
                if prot.get('status') in ('CLOSED','CANCELLED'):
                    item['custody_on']=False
                elif prot.get('status') in ('OPEN','EXITING'):
                    item['custody_on']=True
                if prot.get('stage'):
                    item['stage']=prot['stage']
                elif item.get('custody_on'):
                    item['stage']='S0'
                if prot.get('hold_note'):
                    item['hold_note']=prot['hold_note']
                if prot.get('apts'):
                    item['apts']=prot['apts']
                if prot.get('status') in ('OPEN','EXITING'):
                    item['stop_price']=prot.get('stop_price')
                    item['stop_order_id']=prot.get('exchange_order_id')
                    item['custody_started_at']=prot.get('placed_at')
                    if prot.get('entry_price'):
                        item['entry_price']=prot.get('entry_price')
                        item['cost']=prot.get('entry_price')
                        item['custody_anchor_price']=prot.get('entry_price')
            if fresh.get('mark_price') and 0<=auth.clock()-int(fresh.get('observed_at') or 0)<=60000:
                _quote(item,fresh['mark_price'],'BID')
            if intent=='manual:SOL':
                item['protection']={
                    'active':bool(item.get('custody_on')),
                    'stage':item.get('stage') or 'S0',
                    'stop_price':item.get('stop_price'),
                    'entry_price':item.get('entry_price'),
                    'apts':item.get('apts'),
                }
        return result
    @app.get(PREFIX+'/live/positions')
    async def live_positions(request:Request):
        return present_positions(principal(request)['device_id'])
    @app.get(PREFIX+'/live/risk')
    async def live_risk():return present_live_risk()
    @app.get(PREFIX+'/shadow/status')
    async def shadow_status():return reader.shadow_status()
    @app.get(PREFIX+'/shadow/orders')
    async def shadow_orders(limit:int=50,offset:int=0):
        if not 0<=offset<=100000:raise ValueError('offset')
        return reader.shadow_orders(bounded(limit),offset)
    async def change_live(request,action):
        p=principal(request)
        if p['role'] not in ('operator','admin'):raise AuthError('CONTROL_FORBIDDEN')
        body=await request.json()
        if action=='request-enable' and body.get('risk_confirmed') is not True:raise AuthError('RISK_CONFIRMATION_REQUIRED')
        state=reader.live_status().get('state','UNKNOWN')
        if state=='HARD_LOCKED':
            result={'accepted':False,'state':'HARD_LOCKED','code':'DENY_HARD_LOCKED'}
        elif action=='request-enable':
            try: result=account_vault().enable_live()
            except ValueError as exc:
                code=str(exc)
                result={'accepted':False,'state':'LIVE_OFF','code':code if code in codes|{'TRADE_PERMISSION_REQUIRED','NOT_CONFIGURED','WITHDRAWALS_ENABLED'} else 'BINANCE_REJECTED'}
        else:
            result=account_vault().disable_live()
        with auth.tx() as db:db.execute('INSERT INTO control_requests VALUES(?,?,?,?,?)',(p['request_id'],p['device_id'],action,json.dumps(result),auth.clock()))
        return result
    @app.post(PREFIX+'/live/request-enable')
    async def enable(request:Request):return await change_live(request,'request-enable')
    @app.post(PREFIX+'/live/disable')
    async def disable(request:Request):return await change_live(request,'disable')
    @app.post(PREFIX+'/notifications/register')
    async def register(request:Request):
        notifications.register(principal(request)['device_id'],(await request.json())['token']);return {'registered':True}
    @app.get(PREFIX+'/alerts')
    async def alerts(after:int=0,limit:int=50):
        if after<0:raise ValueError('cursor')
        return notifications.alerts(after,bounded(limit))
    @app.post(PREFIX+'/custody/grant')
    async def custody(request:Request):
        from solbot.mathematics.decimal_utils import D
        p=principal(request);v=await request.json();qty=D(v['quantity'],positive=True)
        asset=v['asset'];intent=str(v['position_id'])
        if asset not in ('SOL','SUI') or v.get('risk_confirmed') is not True or not intent:raise ValueError('custody scope')
        snapshot=present_positions(p['device_id'])
        held=next((row for row in (snapshot.get('slots') or [])+(snapshot.get('manual') or []) if str(row.get('intent_id'))==intent),None)
        if held is None:
            raw_snap=reader.live_positions()
            held=next((row for row in (raw_snap.get('slots') or [])+(raw_snap.get('manual') or []) if str(row.get('intent_id'))==intent),None)
        if held is None or held.get('asset')!=asset or (intent!='manual:SOL' and D(held['quantity'])!=qty):raise ValueError('position')
        expiry=int(v['expires_at'])
        if not auth.clock()<expiry<=auth.clock()+30*86400000:raise ValueError('custody expiry')
        protection=None
        if intent=='manual:SOL' and asset=='SOL':
            try: protection=account_vault().protect_manual(str(qty))
            except ValueError as exc:
                code=str(exc)
                allowed=codes|{'TRADE_PERMISSION_REQUIRED','INSUFFICIENT_FREE_SOL','PROTECTION_REJECTED','ENTRY_PRICE_MISSING','STOP_LOCKING_SOL'}
                return JSONResponse({'error':code if code in allowed else 'PROTECTION_REJECTED'},status_code=400)
        marker='POSITION:'+intent
        with auth.tx() as db:
            db.execute('UPDATE custody_requests SET revoked=1 WHERE device_id=? AND rules=? AND revoked=0',(p['device_id'],marker))
            db.execute('INSERT INTO custody_requests VALUES(?,?,?,?,?,?,0,?)',(p['request_id'],p['device_id'],asset,str(qty),marker,expiry,auth.clock()))
        placed=bool(protection)
        return {'authorization_id':p['request_id'],'position_id':intent,'status':'STOP_PLACED' if placed else 'PENDING_ACCOUNT_VERIFICATION','active_custody':placed,'private_calls':0,'buy_orders':0,**(protection or {})}
    def account_vault():
        if account_vault.cached is None:
            from .binance_account import AccountVault
            account_vault.cached=vault or AccountVault(auth.path.parent)
        return account_vault.cached
    account_vault.cached=vault
    codes={'INVALID_KEY','WITHDRAWALS_ENABLED','READ_PERMISSION_REQUIRED','BINANCE_REJECTED','BINANCE_UNREACHABLE','NOT_CONFIGURED'}
    def binance_call(action):
        try: return action()
        except ValueError as exc:
            code=str(exc)
            return JSONResponse({'error':code if code in codes else 'BINANCE_REJECTED'},status_code=400)
    @app.get(PREFIX+'/binance/account')
    async def binance_status(request:Request):
        principal(request)
        return account_vault().status()
    @app.post(PREFIX+'/binance/account')
    async def binance_save(request:Request):
        p=principal(request)
        if p['role'] not in ('operator','admin'): raise AuthError('CONTROL_FORBIDDEN')
        auth.rate('binance:'+p['device_id'],8)
        body=await request.json()
        return binance_call(lambda: account_vault().save(body.get('api_key'),body.get('secret')))
    @app.post(PREFIX+'/binance/sync')
    async def binance_sync(request:Request):
        p=principal(request)
        if p['role'] not in ('operator','admin'): raise AuthError('CONTROL_FORBIDDEN')
        auth.rate('binance:'+p['device_id'],8)
        return binance_call(lambda: account_vault().sync())
    @app.post(PREFIX+'/binance/clear')
    async def binance_clear(request:Request):
        p=principal(request)
        if p['role'] not in ('operator','admin'): raise AuthError('CONTROL_FORBIDDEN')
        return account_vault().clear()
    @app.post(PREFIX+'/custody/revoke')
    async def revoke_custody(request:Request):
        p=principal(request);v=await request.json()
        with auth.tx() as db:
            if v.get('position_id'):
                row=db.execute('SELECT request_id,device_id FROM custody_requests WHERE rules=? AND revoked=0 ORDER BY created_at DESC LIMIT 1',('POSITION:'+str(v['position_id']),)).fetchone()
                if not row or (row[1]!=p['device_id'] and p['role']!='admin'):raise AuthError('NOT_AUTHORIZED')
                target=row[0]
            else:
                row=db.execute('SELECT device_id FROM custody_requests WHERE request_id=?',(v['authorization_id'],)).fetchone()
                if not row or (row[0]!=p['device_id'] and p['role']!='admin'):raise AuthError('NOT_AUTHORIZED')
                target=v['authorization_id']
            rules=db.execute('SELECT rules FROM custody_requests WHERE request_id=?',(target,)).fetchone()
        cancelled=False
        if (v.get('position_id')=='manual:SOL') or (rules and str(rules[0]).startswith('POSITION:manual:SOL')):
            try: cancelled=bool(account_vault().cancel_manual().get('cancelled'))
            except ValueError as exc:
                code=str(exc)
                return JSONResponse({'error':code if code in codes|{'PROTECTION_REJECTED'} else 'PROTECTION_REJECTED'},status_code=400)
        with auth.tx() as db:
            db.execute('UPDATE custody_requests SET revoked=1 WHERE request_id=?',(target,))
        return {'revoked':True,'native_orders_changed':cancelled,'buy_orders':0}
    @app.websocket(PREFIX+'/events')
    async def events(socket:WebSocket):
        import asyncio
        if socket.url.scheme!='wss':await socket.close(code=1008);return
        try:
            path=socket.url.path+('?' + socket.url.query if socket.url.query else '')
            p=auth.authenticate('GET',path,b'',socket.headers)
            await socket.accept();cursor=0
            while auth.active(p['device_id']):
                try:
                    batch=notifications.alerts(cursor,100);cursor=batch['next_cursor']
                    await socket.send_json({'status':present_live_status(),'risk':present_live_risk(),'positions':present_positions(p['device_id']),'markets':reader.market_summary(),'strategies':reader.strategy_summary(),'alerts':batch,'resync_required':False})
                except Exception:
                    break
                await asyncio.sleep(2)
            try:await socket.close(code=1000)
            except Exception:pass
        except (AuthError,WebSocketDisconnect):
            try:await socket.close(code=1008)
            except RuntimeError:pass
    return app

def _notification_loop(reader,auth,notifications,fcm_path,interval=2.0):
    from .notifications import FCMHttpV1,SourceEventCollector
    log=logging.getLogger('solbot.app_api.notifications')
    cred=Path(fcm_path) if fcm_path else None
    sender=FCMHttpV1(cred,enabled=bool(cred and cred.is_file())) if cred else FCMHttpV1('/nonexistent')
    collector=SourceEventCollector(reader,notifications)
    notifications.recover()
    last_warn=0.0
    while True:
        try:
            if cred and not sender.enabled and cred.is_file():sender.enabled=True
            notifications.recover()
            collector.poll()
            notifications.dispatch(sender,device_active=auth.active)
        except Exception as exc:
            now_s=time.monotonic()
            if now_s-last_warn>=60.0:
                last_warn=now_s;log.warning('notification loop failed: %s',exc)
        time.sleep(interval)

def main():
    import argparse,pwd,uvicorn
    from .control_ipc import ControlClient
    p=argparse.ArgumentParser();p.add_argument('--state-dir',default='/var/lib/sol-bot-app');p.add_argument('--port',type=int,default=8016)
    p.add_argument('--control-socket',default='/run/solbot-ipc/control.sock');p.add_argument('--enrollment-file',default='/etc/sol-bot/secrets/device_passphrase.sha256')
    p.add_argument('--require-enrollment',action='store_true');a=p.parse_args()
    enrollment=None
    if a.require_enrollment:
        enrollment=Path(a.enrollment_file).read_text(encoding='utf-8').strip()
        if len(enrollment)!=64:raise SystemExit('enrollment hash invalid')
    root=Path(a.state_dir)
    from .binance_account import AccountVault
    from .custody_apts import loop
    auth=AuthStore(root/'auth.sqlite',enrollment_sha256=enrollment)
    notifications=NotificationStore(root/'notifications.sqlite')
    vault=AccountVault(root)
    app=create_app(auth,DataReader(),notifications,ControlClient(a.control_socket,pwd.getpwnam('solbot-exec').pw_uid),vault)
    fcm_path=os.environ.get('SOLBOT_FCM_CREDENTIALS','/etc/sol-bot/secrets/firebase-adminsdk.json')
    threading.Thread(target=loop,args=(vault,DataReader()),name='custody-apts',daemon=True).start()
    threading.Thread(target=_notification_loop,args=(DataReader(),auth,notifications,fcm_path),name='notification-loop',daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=a.port,workers=1,proxy_headers=True,forwarded_allow_ips='127.0.0.1',access_log=False)
if __name__=='__main__':main()
