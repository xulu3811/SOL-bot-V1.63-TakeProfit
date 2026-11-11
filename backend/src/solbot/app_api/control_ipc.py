"""Only authenticated application UID can revoke or request the P5 session."""
import socket,struct,json,os,threading
from solbot.execution.p5_authorization import AuthorizationDenied

def receive(sock):
    data=b''
    while b'\n' not in data:
        block=sock.recv(4096)
        if not block:raise ValueError('incomplete request')
        data+=block
        if len(data)>8192:raise ValueError('oversized request')
    return json.loads(data.split(b'\n')[0])

class ControlClient:
    def __init__(self,path,expected_uid):self.path,self.expected_uid=path,expected_uid
    def action(self,action,principal):
        try:return self._exchange(action,principal)
        except (FileNotFoundError,ConnectionRefusedError,TimeoutError,AttributeError):
            code='DENY_NOT_READY' if action=='request-enable' else 'CONTROL_UNAVAILABLE'
            return {'state':'LIVE_OFF','accepted':False,'code':code,'private_calls':0,'blockers':['P5_PRODUCTION_RELEASE_NOT_ACCEPTED','P0_CREDENTIAL_CLOSURE']}
    def _exchange(self,action,principal):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(4);sock.connect(self.path)
            _,uid,_=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=self.expected_uid:raise PermissionError('executor identity mismatch')
            sock.sendall(json.dumps({'action':action,'device_id':principal['device_id'],'request_id':principal['request_id']}).encode()+b'\n')
            result=receive(sock)
            if result.get('error'):raise PermissionError('executor denied')
            return result

def serve(path,gate,allowed_uid):
    if os.path.lexists(path):
        import stat
        if not stat.S_ISSOCK(os.lstat(path).st_mode):raise ValueError('not a socket')
        os.unlink(path)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(path);os.chmod(path,0o660);server.listen(8)
        while True:
            conn,_=server.accept()
            with conn:
                conn.settimeout(4)
                try:
                    _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if uid!=allowed_uid:raise PermissionError('peer denied')
                    message=receive(conn)
                    if message['action']=='disable':
                        gate.off();result={'state':'LIVE_OFF','accepted':True,'dynamic_management':False,'native_protection':'RETAINED_NOT_REVALIDATED'}
                    elif message['action']=='request-enable':
                        # P5's compiled production gate rejects before any private call.
                        try:gate.request_on(message,lambda session:{})
                        except AuthorizationDenied:pass
                        result={'state':gate.state,'accepted':False,'code':'DENY_NOT_READY','blockers':['P5_PRODUCTION_RELEASE_NOT_ACCEPTED','REAL_ACCOUNT_AND_PROTECTION_NOT_VERIFIED','EXTERNAL_AUDIT','P0_SSH_RISK']}
                    else:raise ValueError('unsupported control action')
                    conn.sendall(json.dumps(result).encode()+b'\n')
                except Exception:
                    try:conn.sendall(b'{"error":"DENIED"}\n')
                    except OSError:pass
