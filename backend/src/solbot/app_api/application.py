"""TLS-only, signed app façade. No order submission or secret retrieval route."""
import json,sqlite3,uuid,time
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException,WebSocket,WebSocketDisconnect
from fastapi.responses import JSONResponse
from .auth import AuthStore,AuthError
from .data import DataReader
from .notifications import NotificationStore

PREFIX='/api/v1/app'
def create_app(auth,reader,notifications,control):
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
        return {'revoked':True,'control':result}
    @app.get(PREFIX+'/status')
    async def status():return reader.status()
    @app.get(PREFIX+'/markets')
    async def markets(symbol:str='SOLUSDT',timeframe:str='1m',limit:int=100):
        if symbol!='SOLUSDT':raise HTTPException(status_code=400,detail='unsupported market')
        return reader.markets(symbol,timeframe,bounded(limit))
    @app.get(PREFIX+'/strategies')
    async def strategies(symbol:str='SUIUSDT',limit:int=50):return reader.strategies(symbol,bounded(limit))
    @app.get(PREFIX+'/live/status')
    async def live_status():return reader.live_status()
    @app.get(PREFIX+'/live/orders')
    async def live_orders(limit:int=50,offset:int=0):
        if not 0<=offset<=100000:raise ValueError('offset')
        return reader.live_orders(bounded(limit),offset)
    @app.get(PREFIX+'/live/positions')
    async def live_positions():return reader.live_positions()
    @app.get(PREFIX+'/live/risk')
    async def live_risk():return reader.live_risk()
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
        if state=='HARD_LOCKED' and action=='request-enable':result={'accepted':False,'state':'HARD_LOCKED','code':'DENY_HARD_LOCKED'}
        else:
            result=control.action(action,p)
            if state=='HARD_LOCKED':result['state']='HARD_LOCKED'
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
        if v['asset']!='SUI' or v.get('risk_confirmed') is not True:raise ValueError('custody scope')
        expiry=int(v['expires_at'])
        if not auth.clock()<expiry<=auth.clock()+30*86400000:raise ValueError('custody expiry')
        with auth.tx() as db:db.execute('INSERT INTO custody_requests VALUES(?,?,?,?,?,?,0,?)',(p['request_id'],p['device_id'],'SUI',str(qty),'P4_FIXED_PROTECTION',expiry,auth.clock()))
        return {'authorization_id':p['request_id'],'status':'PENDING_ACCOUNT_VERIFICATION','active_custody':False,'private_calls':0}
    @app.post(PREFIX+'/custody/revoke')
    async def revoke_custody(request:Request):
        p=principal(request);v=await request.json()
        with auth.tx() as db:
            row=db.execute('SELECT device_id FROM custody_requests WHERE request_id=?',(v['authorization_id'],)).fetchone()
            if not row or (row[0]!=p['device_id'] and p['role']!='admin'):raise AuthError('NOT_AUTHORIZED')
            db.execute('UPDATE custody_requests SET revoked=1 WHERE request_id=?',(v['authorization_id'],))
        return {'revoked':True,'native_orders_changed':False}
    @app.websocket(PREFIX+'/events')
    async def events(socket:WebSocket):
        import asyncio
        if socket.url.scheme!='wss':await socket.close(code=1008);return
        try:
            path=socket.url.path+('?' + socket.url.query if socket.url.query else '')
            p=auth.authenticate('GET',path,b'',socket.headers)
            await socket.accept();cursor=0;deadline=auth.clock()+60000
            while auth.active(p['device_id']) and auth.clock()<deadline:
                batch=notifications.alerts(cursor,100);cursor=batch['next_cursor']
                await socket.send_json({'status':reader.live_status(),'alerts':batch,'resync_required':False})
                await asyncio.sleep(2)
            await socket.close(code=1008)
        except (AuthError,WebSocketDisconnect):
            try:await socket.close(code=1008)
            except RuntimeError:pass
    return app

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
    app=create_app(AuthStore(root/'auth.sqlite',enrollment_sha256=enrollment),DataReader(),NotificationStore(root/'notifications.sqlite'),ControlClient(a.control_socket,pwd.getpwnam('solbot-exec').pw_uid))
    uvicorn.run(app,host='127.0.0.1',port=a.port,workers=1,proxy_headers=True,forwarded_allow_ips='127.0.0.1',access_log=False)
if __name__=='__main__':main()
