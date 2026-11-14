"""Session authority is separate from persisted display state. P6 remains closed."""
import secrets
import time

class AuthorizationDenied(PermissionError): pass

class LiveSession:
    def __init__(self,*,environment='production',clock=None,account_id='SUI-live'):
        from solbot.storage.models import resolve_live_account
        if environment not in ('production','mock'): raise ValueError('unsupported environment')
        self.environment=environment; self.clock=clock or (lambda:int(time.time()*1000))
        self.account_id,self.primary_symbol,_=resolve_live_account(account_id)
        self.state='LIVE_OFF'; self._lease=None; self.expires_at=0
        self.risk_locked=False; self.recovery_required=True
        self._closers=[]
    def register_close(self,callback): self._closers.append(callback)

    def request_on(self,authorization,checks):
        # No P6 authentication issuer exists in this release. Dicts, UI flags and
        # database LIVE_ON rows cannot create an in-memory authority lease.
        if self.environment!='mock' or not isinstance(authorization,MockAuthorization):
            raise AuthorizationDenied('DENY_P6_AUTHENTICATION_NOT_ACCEPTED')
        if authorization._marker is not _MOCK_ISSUER: raise AuthorizationDenied('invalid issuer')
        self.state='LIVE_PENDING'; self._lease=secrets.token_bytes(32); self.expires_at=self.clock()+60000
        try:
            evidence=checks(self)
            required={'account','permissions','orders','balances','protection','guardian','filters'}
            if set(evidence)!=required or any(evidence[k] is not True for k in required): raise AuthorizationDenied('incomplete readiness')
            self.recovery_required=False; self.state='LIVE_ON'
        except BaseException:
            self.off(); raise

    def require(self,scope,account_id=None,symbol=None):
        if self.environment=='production': raise AuthorizationDenied('DENY_P6_AUTHENTICATION_NOT_ACCEPTED')
        if not self._lease or self.clock()>=self.expires_at: raise AuthorizationDenied('authorization absent or expired')
        acct=self.account_id if account_id is None else account_id
        if acct!=self.account_id or symbol not in (None,self.primary_symbol,'BNBUSDT'): raise AuthorizationDenied('identity mismatch')
        if scope not in ('PRIVATE_READ','TRADE','PROTECT','CANCEL'): raise AuthorizationDenied('unknown scope')
        if self.state=='LIVE_PENDING' and scope=='PRIVATE_READ': return
        if self.state not in ('LIVE_ON','PAUSED','HARD_LOCKED'): raise AuthorizationDenied('LIVE OFF')
        if scope=='TRADE' and (self.state!='LIVE_ON' or self.risk_locked or self.recovery_required): raise AuthorizationDenied('new entry blocked')

    def pause(self):
        if self.state!='LIVE_OFF': self.state='HARD_LOCKED' if self.risk_locked else 'PAUSED'
        self.recovery_required=True

    def hard_lock(self):
        self.risk_locked=True
        if self.state!='LIVE_OFF': self.state='HARD_LOCKED'

    def off(self):
        self._lease=None; self.expires_at=0; self.state='LIVE_OFF'; self.recovery_required=True
        for close in self._closers:
            try: close()
            except Exception: pass
        self._closers=[]

_MOCK_ISSUER=object()
class MockAuthorization:
    """Only useful with in-memory mock transport; never a production credential."""
    def __init__(self): self._marker=_MOCK_ISSUER

