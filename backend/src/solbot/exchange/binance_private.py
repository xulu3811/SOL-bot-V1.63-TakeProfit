"""Signed Binance REST with lazy credentials, explicit scope and no write retries."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from solbot.storage.models import LIVE_SYMBOLS, resolve_live_account

ACCOUNT='SUI-live'
SYMBOLS=LIVE_SYMBOLS
READ_PATHS=frozenset(('/api/v3/account','/api/v3/openOrders','/api/v3/order','/api/v3/allOrders','/api/v3/myTrades','/api/v3/account/commission','/sapi/v1/account/apiRestrictions'))
WRITE_PATHS=frozenset(('/api/v3/order','/api/v3/orderList/oco','/api/v3/orderList/otoco','/api/v3/orderList','/api/v3/order/cancelReplace'))

class AuthorizationDenied(PermissionError): pass

class ExchangeError(RuntimeError):
    def __init__(self,code,status=0,*,unknown=False,retry_after=0):
        self.code,self.status,self.unknown,self.retry_after=code,status,unknown,retry_after
        # Never include exchange text, request URL, signature, headers or secrets.
        super().__init__(f'Binance error code={code} status={status} uncertain={unknown}')

@dataclass(repr=False)
class Credentials:
    api_key: str
    secret: str
    def __repr__(self): return 'Credentials(<redacted>)'

class ClosedGate:
    def require(self,scope,account_id,symbol=None):
        raise AuthorizationDenied('LIVE authorization absent')

class MockTransport:
    """In-memory endpoint queue. No sockets, URLs or injectable network callback."""
    def __init__(self,responses=()):
        self.responses=list(responses); self.calls=[]
    def request(self,method,path,query,headers):
        self.calls.append({'method':method,'path':path,'query_keys':tuple(k.split('=')[0] for k in query.split('&'))})
        if not self.responses: raise AssertionError('unconfigured mock response')
        value=self.responses.pop(0)
        if isinstance(value,BaseException): raise value
        return value
    def close(self): pass

class HTTPTransport:
    """Production transport implementation, compiled P5 release gate stays closed."""
    def request(self,method,path,query,headers):
        raise AuthorizationDenied('P6 production authentication acceptance missing')

    def _accepted_release_request(self,method,path,query,headers):
        """Future controlled release entry; not called by P5 public request path."""
        # Even calling this implementation directly cannot bypass the release gate.
        self._require_accepted_release()
        url='https://api.binance.com'+path
        body=None
        if method=='GET': url+='?'+query
        else: body=query.encode()
        request=Request(url,data=body,headers={**headers,'Content-Type':'application/x-www-form-urlencoded'},method=method)
        try:
            with urlopen(request,timeout=12) as response:
                raw=response.read(4_000_001)
                if len(raw)>4_000_000: raise ExchangeError('OVERSIZED_RESPONSE',unknown=method!='GET')
                return response.status,dict(response.headers),json.loads(raw)
        except HTTPError as error:
            try: payload=json.loads(error.read(200000))
            except (ValueError,OSError): payload={'code':'INVALID_RESPONSE'}
            return error.code,dict(error.headers),payload
        except (URLError,TimeoutError,OSError,ValueError):
            raise ExchangeError('TRANSPORT_FAILURE',unknown=method!='GET') from None

    @staticmethod
    def _require_accepted_release():
        raise AuthorizationDenied('P6 production authentication acceptance missing')
    def close(self): pass

class BinancePrivate:
    def __init__(self,credential_loader,transport=None,gate=None,*,clock=None,recv_window=5000,account_id='SUI-live',symbols=None):
        if type(recv_window) is not int or not 1<=recv_window<=5000: raise ValueError('bounded recvWindow required')
        acct,primary,_=resolve_live_account(account_id)
        self.account_id=acct
        self.allowed_symbols=frozenset(symbols) if symbols is not None else frozenset((primary,'BNBUSDT'))
        self.loader=credential_loader; self.transport=transport if transport is not None else HTTPTransport()
        self.gate=gate if gate is not None else ClosedGate(); self.clock=clock or (lambda:time.time_ns()//1000000)
        self.recv_window=recv_window; self.offset=0; self.synced_at=None; self.blocked_until=0; self.used_weight=0
        self._lock=threading.RLock(); self.closed=False
        if hasattr(self.gate,'register_close'):
            self.gate.register_close(self.close)

    def _authorize(self,scope,symbol=None):
        if self.closed: raise AuthorizationDenied('private adapter closed')
        if symbol is not None and symbol not in self.allowed_symbols: raise AuthorizationDenied('wrong live symbol')
        # The production release gate runs before invoking even a credential loader.
        if type(self.transport) is not MockTransport:
            HTTPTransport._require_accepted_release()
        self.gate.require('PRIVATE_READ' if scope=='READ' else scope,self.account_id,symbol)

    def sync_time(self,server_time,*,sent_at,received_at):
        if any(type(x) is not int for x in (server_time,sent_at,received_at)) or not 0<=received_at-sent_at<=1000:
            raise ValueError('invalid public clock measurement')
        self.offset=server_time-(sent_at+received_at)//2; self.synced_at=received_at

    def request(self,method,path,params=None,*,scope='READ'):
        params=dict(params or {})
        with self._lock:
            self._authorize(scope,params.get('symbol'))
            if method=='GET':
                if path not in READ_PATHS or scope!='READ': raise AuthorizationDenied('endpoint scope mismatch')
            elif method in ('POST','DELETE'):
                if path not in WRITE_PATHS or scope not in ('TRADE','PROTECT'): raise AuthorizationDenied('endpoint scope mismatch')
            else: raise ValueError('unsupported HTTP method')
            now=self.clock()
            if self.synced_at is None or not 0<=now-self.synced_at<=60000: raise AuthorizationDenied('public clock synchronization required')
            if now<self.blocked_until: raise ExchangeError('LOCAL_RATE_LIMIT',retry_after=(self.blocked_until-now)/1000)
            if any(k in params for k in ('signature','timestamp','recvWindow','apiKey')): raise ValueError('reserved authentication parameter')
            try: credentials=self.loader()
            except Exception: raise AuthorizationDenied('credential load failed') from None
            if not isinstance(credentials,Credentials) or not credentials.api_key or not credentials.secret: raise AuthorizationDenied('invalid credential source')
            try:
                params.update(timestamp=now+self.offset,recvWindow=self.recv_window)
                query=urlencode(sorted(params.items()))
                signature=hmac.new(credentials.secret.encode(),query.encode(),hashlib.sha256).hexdigest()
                # Revocation may happen while a credential is being loaded.
                self._authorize(scope,params.get('symbol'))
                try: status,headers,data=self.transport.request(method,path,query+'&signature='+signature,{'X-MBX-APIKEY':credentials.api_key})
                except (TimeoutError,ConnectionError,OSError):
                    raise ExchangeError('TRANSPORT_FAILURE',unknown=method!='GET') from None
            finally:
                credentials=None
            normalized={k.lower():v for k,v in headers.items()}
            weight=str(normalized.get('x-mbx-used-weight-1m',self.used_weight))
            if weight.isascii() and weight.isdecimal() and len(weight)<12: self.used_weight=int(weight)
            raw_code=data.get('code') if isinstance(data,dict) else None
            code=raw_code if type(raw_code) is int else ('INVALID_RESPONSE' if raw_code is not None else None)
            if status in (418,429):
                retry_text=str(normalized.get('retry-after','60'))
                retry=max(1,int(retry_text)) if retry_text.isascii() and retry_text.isdecimal() and len(retry_text)<9 else 60
                self.blocked_until=now+retry*1000
                raise ExchangeError(code,status,unknown=method!='GET',retry_after=retry)
            if status>=400 or (isinstance(code,int) and code<0):
                if code==-1021: self.synced_at=None
                raise ExchangeError(code,status,unknown=status>=500 or code in (-1006,-1007))
            return data

    def close(self):
        if not self.closed:
            self.closed=True; self.transport.close()
