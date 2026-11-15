import unittest,tempfile,sqlite3,json
from contextlib import closing
from pathlib import Path
from solbot.app_api.data import DataReader
from solbot.app_api.notifications import NotificationStore,FCMHttpV1,SourceEventCollector
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
    def test_legacy_ledger_projects_sol_slots_and_unsynced_balance(self):
        from decimal import Decimal
        l=AllocationLedger(self.root/'risk.sqlite')
        with l.transaction() as db:
            db.execute("INSERT INTO robot_positions VALUES('sui-1','SUI','10','8','1','S0')")
            db.execute("INSERT INTO robot_positions VALUES('sol-1','SOL','0.2','30','1','S1')")
        result=DataReader({'guardian':l.path},clock=lambda:self.now).live_positions()
        self.assertTrue(result['available']);self.assertEqual(result['symbol'],'SOLUSDT')
        self.assertEqual(result['balance_state'],'UNSYNCED');self.assertEqual(result['reason'],'BALANCE_NOT_SYNCED')
        self.assertEqual([row['intent_id'] for row in result['slots']],['sol-1'])
        self.assertEqual(result['slots'][0]['source'],'ROBOT');self.assertEqual(result['slots'][0]['stage'],'S1')
        self.assertEqual(result['manual'],[]);self.assertEqual(Decimal(result['slots'][0]['quantity']),Decimal('0.2'))
    def test_manual_sol_is_balance_minus_robot_slots(self):
        from decimal import Decimal
        l=AllocationLedger(self.root/'risk.sqlite')
        with l.transaction() as db:
            db.execute("INSERT INTO robot_positions VALUES('sol-1','SOL','0.2','30','1','S0')")
            db.execute('INSERT INTO exchange_balances VALUES(?,?,?,?)',('SOL','0.5','0.1',self.now))
            db.execute('INSERT INTO exchange_balances VALUES(?,?,?,?)',('USDT','140','0',self.now))
            db.execute('INSERT INTO exchange_balances VALUES(?,?,?,?)',('BNB','0.01','0',self.now))
        result=DataReader({'guardian':l.path},clock=lambda:self.now).live_positions()
        self.assertEqual(result['balance_state'],'FRESH');self.assertIsNone(result['reason'])
        self.assertEqual(Decimal(result['manual'][0]['quantity']),Decimal('0.4'))
        self.assertEqual(result['manual'][0]['intent_id'],'manual:SOL')
        self.assertEqual(set(result['account']),{'SOL','USDT','BNB'})
        self.assertEqual(result['items'][0]['source'],'ROBOT');self.assertEqual(result['items'][1]['source'],'MANUAL')
    def test_strategy_latest_prefers_t1_and_aliases_signal_fields(self):
        path=self.root/'strategy.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE decisions(id INTEGER PRIMARY KEY,symbol TEXT,payload_json TEXT)')
            older={'symbol':'SOLUSDT','strategy_id':'T1','score':62.5,'priority':'WATCH','score_factors':{'trend':10,'momentum':12.5,'participation':8,'structure':16,'execution':16},'entry_low':'100','entry_high':'101','signal_time':self.now,'accepted':False,'signal_id':'t1'}
            newer={'symbol':'SOLUSDT','strategy_id':'R1','score':10,'priority':'REJECT','score_factors':{'trend':0,'momentum':0,'participation':0,'structure':0,'execution':0},'entry_low':'90','entry_high':'91','signal_time':self.now,'accepted':False,'signal_id':'r1'}
            db.execute('INSERT INTO decisions VALUES(?,?,?)',(1,'SOLUSDT',json.dumps(older)))
            db.execute('INSERT INTO decisions VALUES(?,?,?)',(2,'SOLUSDT',json.dumps(newer)))
        db.close()
        result=DataReader({'SOLUSDT_strategy':path}).strategies('SOLUSDT',10)
        self.assertEqual(result['latest']['strategy_id'],'T1');self.assertEqual(result['score'],62.5)
        self.assertFalse(result['r1_live_allowed']);self.assertEqual(result['items'][0]['price'],'90');self.assertEqual(result['items'][0]['type'],'R1')
        self.assertEqual(result['indicators_5m'],{})
    def test_five_minute_dashboard_uses_the_closed_bar(self):
        path=self.root/'market.sqlite'
        with sqlite3.connect(path) as db:
            for sql in migrations_for('SOLUSDT')[1]:db.execute(sql)
            for n in range(40):
                opened=self.now+n*300000
                close=80+n*0.1
                db.execute('INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('SOLUSDT','5m',opened,opened+299999,str(close-0.2),str(close+0.3),str(close-0.4),str(close),'10','800',1,'4','1',1,'rest',self.now,None))
        db.close()
        board=DataReader({'SOLUSDT_market':path},clock=lambda:self.now)._closed_indicators('SOLUSDT','5m')
        self.assertEqual(board['timeframe'],'5m')
        self.assertGreater(board['rsi6'],48)
        self.assertGreater(board['vwap'],0)
        self.assertGreater(board['atr'],0)
        self.assertLess(board['atr_pct'],3)
    def test_completed_day_window_fills_ticker_fields(self):
        path=self.root/'market.sqlite'
        with sqlite3.connect(path) as db:
            for sql in migrations_for('SOLUSDT')[1]:db.execute(sql)
            for n in (0,1440):
                opened=self.now+n*60000
                db.execute('INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('SOLUSDT','1m',opened,opened+59999,'100','110','90',str(100+n/1440),'2','2',1,'1','1',1,'rest',self.now,None))
        db.close()
        result=DataReader({'SOLUSDT_market':path},clock=lambda:self.now).markets('SOLUSDT','1m',10)
        self.assertEqual(result['price_source'],'LAST_CLOSED_CANDLE')
        self.assertEqual(result['high_24h'],'110');self.assertEqual(result['low_24h'],'90')
        self.assertTrue(result['change_24h'].startswith('+'))

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
        # Re-registering the same FCM token under a new device_id (e.g. re-paired phone) transfers ownership cleanly
        self.store.register('two',self.token)
        self.store.register('one',self.token)
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
    def test_collector_polls_r2_outbox_and_bot_orders(self):
        r2_db=self.root/'r2.sqlite';vault_db=self.root/'vault.sqlite'
        with closing(sqlite3.connect(r2_db)) as db,db:
            db.execute('CREATE TABLE r2_outbox(signal_id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO r2_outbox VALUES(?,?)',('r2-old',json.dumps({'symbol':'SOLUSDT','strategy_id':'R2','accepted':True,'signal_time':self.now-600000,'expires_at':self.now-540000})))
            db.execute('INSERT INTO r2_outbox VALUES(?,?)',('r2-fresh',json.dumps({'symbol':'SOLUSDT','strategy_id':'R2','accepted':True,'signal_time':self.now,'expires_at':self.now+60000})))
        with closing(sqlite3.connect(vault_db)) as db,db:
            db.execute('CREATE TABLE bot_orders(signal_id TEXT PRIMARY KEY,strategy_id TEXT,status TEXT,created_at INTEGER,filled_at INTEGER,stop_updated_at INTEGER)')
            db.execute('INSERT INTO bot_orders VALUES(?,?,?,?,?,?)',('sig-1','R2','NEW',self.now,None,None))
            db.execute('INSERT INTO bot_orders VALUES(?,?,?,?,?,?)',('sig-2','T2','EXPIRED',self.now-1000,None,None))
        reader=DataReader({'r2_state':r2_db,'binance_vault':vault_db},clock=lambda:self.now)
        collector=SourceEventCollector(reader,self.store)
        counts=collector.poll()
        self.assertEqual(counts['r2_state'],2);self.assertEqual(counts['binance_vault'],2)
        items=self.store.alerts()['items']
        self.assertEqual([(i['kind'],i['details'].get('strategy'),i['details'].get('status')) for i in items],[
            ('STRATEGY_SIGNAL','R2',None),
            ('LIVE_ORDER','R2','NEW'),
            ('LIVE_ORDER','T2','EXPIRED'),
        ])
        with closing(sqlite3.connect(vault_db)) as db,db:
            db.execute("UPDATE bot_orders SET status='FILLED',filled_at=? WHERE signal_id='sig-1'",(self.now+1000,))
        self.assertEqual(collector.poll()['binance_vault'],1)
        with closing(sqlite3.connect(vault_db)) as db,db:
            db.execute("ALTER TABLE bot_orders ADD COLUMN closed_at INTEGER")
            db.execute("UPDATE bot_orders SET status='CLOSED',stop_updated_at=?,closed_at=? WHERE signal_id='sig-1'",(self.now+2000,self.now+5000))
        self.assertEqual(collector.poll()['binance_vault'],1)
        self.assertEqual(collector.poll()['binance_vault'],0)
        closed_alert=self.store.alerts()['items'][-1]
        self.assertEqual((closed_alert['kind'],closed_alert['details'].get('status'),closed_alert['created_at']),('APTS_EXIT','CLOSED',self.now+5000))
        kinds=[(i['kind'],i['details'].get('status')) for i in self.store.alerts()['items'][-2:]]
        self.assertEqual(kinds,[('BUY_FILL','FILLED'),('APTS_EXIT','CLOSED')])

if __name__=='__main__':unittest.main()
