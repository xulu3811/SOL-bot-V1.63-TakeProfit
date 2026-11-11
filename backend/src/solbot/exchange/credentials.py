"""Lazy systemd encrypted-credential boundary; no repo/env fallback secrets."""
import json
import os
from pathlib import Path
import stat
from .binance_private import Credentials, AuthorizationDenied

class SystemdCredentialLoader:
    """Provision via LoadCredentialEncrypted in an accepted future live service."""
    def __init__(self,path): self.path=Path(path)
    def __call__(self):
        # Called only after the authenticated session and release gates succeed.
        path=self.path
        if os.name!='posix' or not str(path).startswith('/run/credentials/'):
            raise AuthorizationDenied('systemd credential mount required')
        fd=None
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.geteuid()) or info.st_mode & 0o077 or info.st_size>8192:
                raise AuthorizationDenied('unsafe credential permissions')
            with os.fdopen(fd,'r',encoding='utf-8') as stream:
                fd=None; data=json.load(stream)
            if set(data)!={'api_key','secret'} or not all(isinstance(v,str) and v for v in data.values()):
                raise AuthorizationDenied('invalid credential structure')
            return Credentials(data['api_key'],data['secret'])
        except (OSError,ValueError,KeyError):
            raise AuthorizationDenied('credential unavailable') from None
        finally:
            if fd is not None: os.close(fd)

