"""Local AF_UNIX RPC, kernel peer credentials, bounded frames, fixed method list."""
import json
import os
import socket
import struct
from solbot.accounts.allocation_ledger import encoded

MAX_FRAME=65536
METHODS={'approve','recheck','unknown','settle','approve_aux','reconcile','bind_order','verify_order','bnb_candidate','position','health'}
def receive(sock):
    buf=bytearray()
    while b'\n' not in buf:
        block=sock.recv(min(4096,MAX_FRAME+1-len(buf)))
        if not block: raise ConnectionError('incomplete IPC frame')
        buf.extend(block)
        if len(buf)>MAX_FRAME: raise ValueError('oversized IPC frame')
    return json.loads(bytes(buf).split(b'\n')[0])

class GuardianIPC:
    def __init__(self,path,*,expected_uid): self.path,self.expected_uid=str(path),expected_uid
    def call(self,method,*args):
        if method not in METHODS: raise ValueError('unsupported RPC')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(5); sock.connect(self.path)
            _,uid,_=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=self.expected_uid: raise PermissionError('Guardian peer UID mismatch')
            sock.sendall(encoded({'method':method,'args':args}).encode()+b'\n')
            response=receive(sock)
            if not response.get('ok'): raise PermissionError('Guardian request denied')
            return response['result']
    def approve(self,value): return self.call('approve',value)
    def recheck(self,value): return self.call('recheck',value)
    def unknown(self,value): return self.call('unknown',value)
    def settle(self,value): return self.call('settle',value)
    def approve_aux(self,params,purpose,position_id): return self.call('approve_aux',params,purpose,position_id)
    def reconcile(self,payload): return self.call('reconcile',payload)
    def bind_order(self,params,protective=False): return self.call('bind_order',params,protective)
    def verify_order(self,params,token,protective=False): return self.call('verify_order',params,token,protective)
    def bnb_candidate(self,params): return self.call('bnb_candidate',params)
    def position(self,intent_id): return self.call('position',intent_id)
    def health(self): return self.call('health')

def serve(path,bridge,allowed_uid):
    # Path lives in a systemd RuntimeDirectory, inaccessible to untrusted users.
    if os.path.lexists(path):
        import stat
        if not stat.S_ISSOCK(os.lstat(path).st_mode): raise ValueError('refusing non-socket path')
        os.unlink(path)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(path); os.chmod(path,0o660); listener.listen(8)
        while True:
            conn,_=listener.accept()
            with conn:
                conn.settimeout(5)
                _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                try:
                    if uid!=allowed_uid: raise PermissionError('untrusted caller')
                    request=receive(conn); method=request.get('method')
                    if method not in METHODS: raise ValueError('unsupported method')
                    result=getattr(bridge,method)(*request['args'])
                    conn.sendall(encoded({'ok':True,'result':result}).encode()+b'\n')
                except Exception:
                    try: conn.sendall(b'{"ok":false}\n')
                    except OSError: pass

def main():
    import argparse,pwd
    from solbot.accounts.allocation_ledger import AllocationLedger
    from .p5_bridge import GuardianBridge
    p=argparse.ArgumentParser(); p.add_argument('--database',required=True); p.add_argument('--socket',required=True); p.add_argument('--client-user',required=True)
    a=p.parse_args(); serve(a.socket,GuardianBridge(AllocationLedger(a.database)),pwd.getpwnam(a.client_user).pw_uid)
if __name__=='__main__': main()
