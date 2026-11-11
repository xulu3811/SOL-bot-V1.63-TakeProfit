"""Independent offline device authentication QA; generated keys, temporary DB only."""
import base64,json,secrets,tempfile,unittest
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from solbot.app_api.auth import AuthStore,AuthError,canonical,hashed

class DeviceAuthenticationQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1800000000000;self.store=AuthStore(Path(self.tmp.name)/'auth.sqlite',clock=lambda:self.now)
        self.key=ec.generate_private_key(ec.SECP256R1());self.session=self.pair(self.key)
    def sign(self,method,path,body,key=None,session=None,**changes):
        h={'x-timestamp':str(self.now),'x-nonce':secrets.token_hex(16),'x-request-id':secrets.token_hex(16)}
        h.update(changes)
        h['x-signature']=base64.b64encode((key or self.key).sign(canonical(method,path,body,int(h['x-timestamp']),h['x-nonce'],h['x-request-id']),ec.ECDSA(hashes.SHA256()))).decode()
        if session:h.update({'x-device-id':session['device_id'],'authorization':'Bearer '+session['session_token']})
        return h
    def pair(self,key,passphrase=None):
        secret=passphrase or secrets.token_urlsafe(12)
        body=json.dumps({'public_key_pem':key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),'device_passphrase':secret,'device_name':'qa'}).encode()
        return self.store.pair(body,self.sign('POST','/api/v1/app/devices/pair',body,key=key))
    def auth(self,body=b'{}',path='/api/v1/app/live/request-enable',method='POST'):
        h=self.sign(method,path,body,session=self.session)
        return self.store.authenticate(method,path,body,h),h
    def test_signed_request_yields_server_role(self):
        p,_=self.auth();self.assertEqual(p['role'],'operator');self.assertEqual(p['device_id'],self.session['device_id'])
    def test_missing_auth_rejected(self):
        with self.assertRaises(AuthError):self.store.authenticate('GET','/api/v1/app/status',b'',{})
    def test_body_and_path_query_are_signed(self):
        body=b'{"enabled":false}';path='/api/v1/app/markets?symbol=SOLUSDT'
        h=self.sign('POST',path,body,session=self.session)
        for changed_path,changed_body in ((path,b'{"enabled":true}'),(path.replace('SOL','SUI'),body)):
            with self.assertRaises(AuthError):self.store.authenticate('POST',changed_path,changed_body,h)
    def test_other_key_cannot_sign_device_request(self):
        h=self.sign('GET','/api/v1/app/status',b'',key=ec.generate_private_key(ec.SECP256R1()),session=self.session)
        with self.assertRaises(AuthError):self.store.authenticate('GET','/api/v1/app/status',b'',h)
    def test_nonce_replay_and_request_id_reuse_denied(self):
        _,h=self.auth()
        with self.assertRaises(AuthError):self.store.authenticate('POST','/api/v1/app/live/request-enable',b'{}',h)
        h2=self.sign('GET','/api/v1/app/status',b'',session=self.session,**{'x-request-id':h['x-request-id']})
        with self.assertRaises(AuthError):self.store.authenticate('GET','/api/v1/app/status',b'',h2)
    def test_old_and_future_signature_rejected(self):
        for delta in (-30001,30001):
            h=self.sign('GET','/api/v1/app/status',b'',session=self.session,**{'x-timestamp':str(self.now+delta)})
            with self.assertRaises(AuthError):self.store.authenticate('GET','/api/v1/app/status',b'',h)
    def test_session_has_no_expiry(self):
        self.assertEqual(self.session['expires_at'],0)
        self.now+=10*365*86400*1000
        self.auth()
    def test_revocation_invalidates_session(self):
        principal,_=self.auth();self.store.revoke(principal,self.session['device_id'])
        with self.assertRaises(AuthError):self.auth()
    def test_operator_cannot_revoke_other_device(self):
        other=self.pair(ec.generate_private_key(ec.SECP256R1()));principal,_=self.auth()
        with self.assertRaises(AuthError):self.store.revoke(principal,other['device_id'])
        self.assertTrue(self.store.active(other['device_id']))
    def test_claimed_admin_role_is_ignored(self):
        key=ec.generate_private_key(ec.SECP256R1())
        body=json.dumps({'public_key_pem':key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),'device_passphrase':secrets.token_urlsafe(12),'role':'admin'}).encode()
        result=self.store.pair(body,self.sign('POST','/api/v1/app/devices/pair',body,key=key))
        with self.store.tx() as db:self.assertEqual(db.execute('SELECT role FROM devices WHERE id=?',(result['device_id'],)).fetchone()[0],'operator')
    def test_passphrase_binds_one_device_permanently(self):
        secret='hardware-pass'
        self.pair(ec.generate_private_key(ec.SECP256R1()),passphrase=secret)
        with self.assertRaises(AuthError):self.pair(ec.generate_private_key(ec.SECP256R1()),passphrase=secret)
        self.now+=10*365*86400*1000
        self.auth()
    def test_enrollment_rejects_other_passphrase(self):
        store=AuthStore(Path(self.tmp.name)/'enroll.sqlite',clock=lambda:self.now,enrollment_sha256=hashed('correct-passphrase'))
        key=ec.generate_private_key(ec.SECP256R1())
        def body(secret):return json.dumps({'public_key_pem':key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),'device_passphrase':secret}).encode()
        bad=body('wrong-passphrase')
        with self.assertRaises(AuthError):store.pair(bad,self.sign('POST','/api/v1/app/devices/pair',bad,key=key))
        good=body('correct-passphrase')
        self.assertEqual(store.pair(good,self.sign('POST','/api/v1/app/devices/pair',good,key=key))['expires_at'],0)
    def test_pairing_rotation_is_retired(self):
        with self.assertRaises(AuthError):self.store.issue_pairing_code()
    def test_missing_control_socket_cannot_enable_live(self):
        from solbot.app_api.control_ipc import ControlClient
        result=ControlClient(str(Path(self.tmp.name)/'missing.sock'),1).action('request-enable',{'device_id':'a','request_id':'b'})
        self.assertFalse(result['accepted']);self.assertEqual(result['state'],'LIVE_OFF');self.assertEqual(result['private_calls'],0)
    def test_renew_revokes_old_token(self):
        principal,_=self.auth();new=self.store.renew(principal)
        with self.assertRaises(AuthError):self.auth()
        self.session=new;self.auth()
    def test_open_session_challenge_is_one_use_and_device_bound(self):
        challenge=self.store.challenge(self.session['device_id'])
        body=json.dumps({'device_id':self.session['device_id'],'challenge':challenge['challenge']}).encode()
        headers=self.sign('POST','/api/v1/app/session/open',body)
        result=self.store.open_session(body,headers);self.assertEqual(result['device_id'],self.session['device_id'])
        with self.assertRaises(AuthError):self.store.open_session(body,self.sign('POST','/api/v1/app/session/open',body))
    def test_rate_limit_persists_denial(self):
        for _ in range(3):self.store.rate('test',limit=3)
        with self.assertRaises(AuthError):self.store.rate('test',limit=3)
    def test_plain_tokens_not_stored(self):
        with self.store.tx() as db:
            hashes_in_db=[r[0] for r in db.execute('SELECT hash FROM sessions')]
        self.assertNotIn(self.session['session_token'],hashes_in_db)

try:
    from fastapi.testclient import TestClient
    from solbot.app_api.application import create_app
    HTTP_AVAILABLE=True
except ImportError:HTTP_AVAILABLE=False

class FakeData:
    def live_status(self):return {'available':True,'state':'LIVE_OFF','production_authorized':False}
    def status(self):return {'live':self.live_status()}
    def __getattr__(self,name):return lambda *a,**k:{'items':[]}
class FakeControl:
    def __init__(self):self.calls=[]
    def action(self,action,principal):
        self.calls.append(action)
        return {'accepted':action=='disable','state':'LIVE_OFF','code':'DENY_NOT_READY','private_calls':0}

@unittest.skipUnless(HTTP_AVAILABLE,'FastAPI/httpx unavailable locally; run server regression')
class AppBoundaryQA(unittest.TestCase):
    def setUp(self):
        from solbot.app_api.notifications import NotificationStore
        self.fixture=DeviceAuthenticationQA();self.fixture.setUp();self.addCleanup(self.fixture.tmp.cleanup)
        self.control=FakeControl();self.reader=FakeData()
        self.app=create_app(self.fixture.store,self.reader,NotificationStore(Path(self.fixture.tmp.name)/'notifications.sqlite'),self.control)
        self.client=TestClient(self.app,base_url='https://testserver');self.addCleanup(self.client.close)
    def request(self,method,path,body=b''):
        h=self.fixture.sign(method,path,body,session=self.fixture.session)
        return self.client.request(method,path,content=body,headers=h)
    def test_all_sensitive_read_routes_require_authentication(self):
        for name in ('status','markets','strategies','live/status','live/orders','live/positions','live/risk','shadow/status','shadow/orders','alerts'):
            self.assertEqual(self.client.get('/api/v1/app/'+name).status_code,401,name)
    def test_http_rejected_before_authenticated_request(self):
        self.assertEqual(self.client.get('http://testserver/api/v1/app/status').status_code,426)
    def test_enable_rejected_and_disable_is_server_confirmed(self):
        on=self.request('POST','/api/v1/app/live/request-enable',b'{"risk_confirmed":true}')
        self.assertEqual(on.status_code,200);self.assertFalse(on.json()['accepted']);self.assertEqual(on.json()['state'],'LIVE_OFF')
        off=self.request('POST','/api/v1/app/live/disable',b'{}')
        self.assertEqual(off.json()['state'],'LIVE_OFF');self.assertEqual(off.json()['private_calls'],0)
    def test_hardlock_enable_never_reaches_control(self):
        self.reader.live_status=lambda:{'available':True,'state':'HARD_LOCKED'}
        result=self.request('POST','/api/v1/app/live/request-enable',b'{"risk_confirmed":true}')
        self.assertEqual(result.json()['code'],'DENY_HARD_LOCKED');self.assertEqual(self.control.calls,[])
    def test_payload_admin_boolean_cannot_revoke_other_device(self):
        other=self.fixture.pair(ec.generate_private_key(ec.SECP256R1()))
        body=json.dumps({'device_id':other['device_id'],'role':'admin','is_admin':True}).encode()
        result=self.request('POST','/api/v1/app/devices/revoke',body)
        self.assertEqual(result.status_code,401);self.assertTrue(self.fixture.store.active(other['device_id']))
    def test_app_markets_accept_only_sol(self):
        denied=self.request('GET','/api/v1/app/markets?symbol=SUIUSDT&timeframe=1m')
        self.assertEqual(denied.status_code,400)
        allowed=self.request('GET','/api/v1/app/markets?symbol=SOLUSDT&timeframe=1m')
        self.assertEqual(allowed.status_code,200)
    def test_request_replay_cannot_repeat_control_action(self):
        path='/api/v1/app/live/disable';body=b'{}';h=self.fixture.sign('POST',path,body,session=self.fixture.session)
        self.assertEqual(self.client.post(path,content=body,headers=h).status_code,200)
        self.assertEqual(self.client.post(path,content=body,headers=h).status_code,401)
        self.assertEqual(self.control.calls,['disable'])

if __name__=='__main__':unittest.main()
