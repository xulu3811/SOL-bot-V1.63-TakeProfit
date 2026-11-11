import unittest,tempfile,sqlite3,json
from pathlib import Path
from solbot.app_api.data import DataReader
from solbot.app_api.notifications import NotificationStore,FCMHttpV1
from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.storage.schema import migrations_for

class AppDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.now=1800000000000
    def test_missing_does_not_create_database(self):
        path=self.root/'missing.sqlite';d=DataReader({'guardian':path},clock=lambda:self.now)
        self.assertEqual(d.live_status()['state'],'UNKNOWN');self.assertFalse(path.exists())
    def test_plan_unverified_and_old_verified_not_current(self):
        l=AllocationLedger(self.root/'risk.sqlite');d=DataReader({'guardian':l.path},clock=lambda:self.now)
        self.assertFalse(d.live_status()['verified']);self.assertEqual(d.live_status()['state'],'LIVE_OFF')
        with l.transaction() as db:db.execute('UPDATE account_allocations SET reconciled_at=?,equity_at=?,recovery_required=0',(self.now-6000,self.now-6000))
        self.assertFalse(d.live_status()['verified']);self.assertFalse(d.live_risk()['verified'])
    def test_symbol_swapped_guardian_rejected(self):
        l=AllocationLedger(self.root/'risk.sqlite')
        with l.transaction() as db:db.execute("UPDATE guardian_owner SET symbol='SOLUSDT'")
        self.assertFalse(DataReader({'guardian':l.path}).live_positions()['available'])
    def test_empty_account_is_unknown(self):
        l=AllocationLedger(self.root/'risk.sqlite')
        with l.transaction() as db:db.execute('DELETE FROM account_allocations')
        self.assertEqual(DataReader({'guardian':l.path}).live_status()['state'],'UNKNOWN')
    def test_real_market_rows_and_indicators(self):
        path=self.root/'market.sqlite'
        with sqlite3.connect(path) as db:
            for sql in migrations_for('SUIUSDT')[1]:db.execute(sql)
            for n in range(130):
                db.execute('INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('SUIUSDT','1m',self.now+n*60000,self.now+n*60000+59999,'1','2','.5',str(1+n/1000),'10','10',2,'5','5',1,'rest',self.now,None))
        db.close()
        d=DataReader({'SUIUSDT_market':path,'SOLUSDT_market':path},clock=lambda:self.now)
        result=d.markets('SUIUSDT','1m',50)
        self.assertTrue(result['available']);self.assertEqual(len(result['candles']),50);self.assertIsNotNone(result['indicators']['rsi6'])
        self.assertFalse(d.markets('SOLUSDT')['available'])
    def test_invalid_pagination_rejected(self):
        with self.assertRaises(ValueError):DataReader().live_orders(limit=501)
        with self.assertRaises(ValueError):DataReader().markets('SUIUSDT','1s')

class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.now=1800000000000
        self.store=NotificationStore(self.root/'notifications.sqlite',clock=lambda:self.now);self.token='synthetic-device-token-123456'
    def test_history_survives_without_push_device(self):
        self.store.publish('event','HARD_LOCK','SUIUSDT',self.now)
        self.assertEqual(self.store.dispatch(lambda m:self.fail('send')),[])
        self.assertEqual(self.store.alerts()['next_cursor'],1)
    def test_dedup_and_conflicting_event(self):
        self.assertTrue(self.store.publish('one','LIVE_ORDER','SUIUSDT',self.now))
        self.assertFalse(self.store.publish('one','LIVE_ORDER','SUIUSDT',self.now))
        with self.assertRaises(ValueError):self.store.publish('one','HARD_LOCK','SUIUSDT',self.now)
    def test_high_priority_and_payload_no_secrets(self):
        self.store.register('device',self.token);self.store.publish('risk','HARD_LOCK','SUIUSDT',self.now,{'secret':'DO_NOT_LEAK','reason':'DO_NOT_LEAK'})
        sent=[];self.store.dispatch(lambda m:sent.append(m) or 'SENT',device_active=lambda d:True)
        self.assertEqual(sent[0]['android']['priority'],'HIGH');self.assertNotIn('DO_NOT_LEAK',json.dumps(sent))
        self.assertEqual(self.store.dispatch(lambda m:self.fail('duplicate'),device_active=lambda d:True),[])
    def test_market_priority_normal(self):
        self.store.register('device',self.token);self.store.publish('market','MARKET_SYNC','SOLUSDT',self.now)
        sent=[];self.store.dispatch(lambda m:sent.append(m) or 'SENT',device_active=lambda d:True)
        self.assertEqual(sent[0]['android']['priority'],'NORMAL')
    def test_device_revocation_prevents_send(self):
        self.store.register('device',self.token);self.store.publish('one','BUY_FILL','SUIUSDT',self.now)
        self.store.dispatch(lambda m:self.fail('revoked'),device_active=lambda d:False)
        self.assertEqual(self.store.dispatch(lambda m:self.fail('revoked'),device_active=lambda d:True),[])
    def test_unregistered_token_deactivated(self):
        self.store.register('device',self.token);self.store.publish('one','BUY_FILL','SUIUSDT',self.now)
        result=self.store.dispatch(lambda m:'UNREGISTERED',device_active=lambda d:True);self.assertEqual(result[0]['state'],'INVALID')
        self.store.publish('two','BUY_FILL','SUIUSDT',self.now);self.assertEqual(self.store.dispatch(lambda m:self.fail('invalid'),device_active=lambda d:True),[])
    def test_token_rotation_and_unique_owner(self):
        self.store.register('one',self.token)
        with self.assertRaises(sqlite3.IntegrityError):self.store.register('two',self.token)
        self.store.publish('one','BUY_FILL','SUIUSDT',self.now);self.store.register('one','new-synthetic-token-567890')
        sent=[];self.store.dispatch(lambda m:sent.append(m) or 'SENT',device_active=lambda d:True)
        self.assertEqual(sent[0]['token'],'new-synthetic-token-567890')
    def test_three_attempt_limit(self):
        self.store.register('one',self.token);self.store.publish('one','BUY_FILL','SUIUSDT',self.now)
        for _ in range(3):self.store.dispatch(lambda m:'RETRY',device_active=lambda d:True);self.now+=300000
        self.assertEqual(self.store.dispatch(lambda m:self.fail('unbounded'),device_active=lambda d:True),[])
    def test_disabled_sender_does_not_read_credential(self):
        sender=FCMHttpV1(self.root/'absent.json')
        self.assertEqual(sender({'token':self.token}),'DISABLED');self.assertFalse((self.root/'absent.json').exists())
        self.store.register('device',self.token);self.store.publish('queued','HARD_LOCK','SUIUSDT',self.now)
        for _ in range(4):self.store.dispatch(sender,device_active=lambda d:True)
        with self.store.tx() as db:self.assertEqual(db.execute('SELECT attempts FROM notification_delivery').fetchone()[0],0)
    def test_crash_recovery_retains_event_identity(self):
        self.store.register('one',self.token);self.store.publish('one','BUY_FILL','SUIUSDT',self.now)
        with self.store.tx() as db:db.execute("UPDATE notification_delivery SET state='SENDING',attempts=1,next_attempt=?",(self.now,))
        self.store.recover();sent=[];self.store.dispatch(lambda m:sent.append(m) or 'SENT',device_active=lambda d:True)
        self.assertEqual(sent[0]['data']['event_id'],'one')

if __name__=='__main__':unittest.main()
