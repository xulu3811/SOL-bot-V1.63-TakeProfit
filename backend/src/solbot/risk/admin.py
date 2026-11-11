"""Local administrator command; no remote authentication shortcut or unlock automation."""
import argparse
import os
import json
from solbot.accounts.allocation_ledger import AllocationLedger
from .recovery_guard import RecoveryGuard

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('action',choices=('status','off','on','unlock'))
    parser.add_argument('--request-id')
    parser.add_argument('--reason')
    parser.add_argument('--user-authorization')
    args=parser.parse_args()
    if args.action!='status':
        if not hasattr(os,'geteuid') or os.geteuid()!=0: parser.error('OS root required')
        if not args.request_id: parser.error('unique request ID required')
    ledger=AllocationLedger(args.database); recovery=RecoveryGuard(ledger)
    if args.action=='unlock':
        recovery.administrator_unlock(args.request_id,reason=args.reason,user_authorization=args.user_authorization)
    elif args.action=='on':
        print(recovery.request_on(args.request_id,actor='os-root'))
    elif args.action=='off':
        recovery.off(args.request_id,actor='os-root')
    print(json.dumps(ledger.snapshot(),sort_keys=True))

if __name__=='__main__': main()

