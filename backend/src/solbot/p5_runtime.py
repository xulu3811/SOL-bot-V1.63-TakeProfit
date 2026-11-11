"""Production P5 OFF watchdog. No credential or private transport construction."""
import argparse,json,time,os
from pathlib import Path
from solbot.execution.p5_store import OrderStore
from solbot.execution.p5_authorization import LiveSession

def main():
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--status-path',required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--control-socket');p.add_argument('--app-user',default='solbot-app')
    a=p.parse_args();store=OrderStore(a.database);gate=LiveSession()
    if a.control_socket:
        import threading,pwd
        from solbot.app_api.control_ipc import serve
        threading.Thread(target=serve,args=(a.control_socket,gate,pwd.getpwnam(a.app_user).pw_uid),daemon=True).start()
    with store.tx() as db:
        db.execute("UPDATE orders SET state='UNKNOWN' WHERE state IN ('SUBMITTING','ACKNOWLEDGED','CANCEL_PENDING')")
        db.execute("UPDATE execution_state SET state='LIVE_OFF',reason='P6_NOT_ACCEPTED_RECONCILE_BEFORE_RESUME'")
    while True:
        with store.tx() as db:
            counts=dict(db.execute('SELECT state,count(*) FROM orders GROUP BY state'))
            pending=db.execute('SELECT count(*) FROM outbox WHERE delivered=0').fetchone()[0]
        status={'version':'p5-execution-1','live_state':gate.state,'production_authorized':False,'private_calls':0,'real_orders':0,'orders_by_state':counts,'pending_settlements':pending,'at':int(time.time()*1000),'uid':os.getuid() if hasattr(os,'getuid') else None,'blockers':['P6_AUTHENTICATION','PRIVATE_ACCOUNT_ACCEPTANCE','NATIVE_PROTECTION_ACCEPTANCE','EXTERNAL_AUDIT','P0_CREDENTIAL_CLOSURE']}
        path=Path(a.status_path); tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(status),encoding='utf-8');tmp.replace(path)
        if a.once:break
        time.sleep(5)
if __name__=='__main__':main()
