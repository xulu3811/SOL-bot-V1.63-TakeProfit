"""Read-only projections of existing P1–P5 stores; no private exchange clients."""
from contextlib import closing
from pathlib import Path
from decimal import Decimal
import json,sqlite3,time

ROOT=Path('/var/lib/sol-bot/database')
DEFAULTS={'SOLUSDT_market':ROOT/'market.sqlite','SUIUSDT_market':ROOT/'sui-market.sqlite',
 'SOLUSDT_strategy':ROOT/'sol-shadow-strategy.sqlite','SUIUSDT_strategy':ROOT/'sui-strategy-strategy.sqlite',
 'guardian':ROOT/'sui-guardian-p4.sqlite','shadow':ROOT/'sol-shadow.sqlite',
 'orders':Path('/var/lib/sol-bot-execution/orders.sqlite')}

class DataReader:
    def __init__(self,paths=None,clock=None):
        self.paths={k:Path(v).resolve() for k,v in (DEFAULTS|dict(paths or {})).items()}
        self.clock=clock or (lambda:time.time_ns()//1000000)
    @staticmethod
    def bounds(limit,offset=0):
        if type(limit) is not int or not 1<=limit<=500 or type(offset) is not int or not 0<=offset<=100000: raise ValueError('invalid pagination')
    def read(self,key,sql,args=()):
        with closing(sqlite3.connect(self.paths[key].as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
            return [dict(r) for r in db.execute(sql,args)]
    def _owner(self,key,table,expected):
        row=self.read(key,'SELECT * FROM '+table)
        if len(row)!=1 or any(row[0].get(k)!=v for k,v in expected.items()): raise ValueError('source identity mismatch')
    def _safe(self,fn):
        try:return dict(available=True,**fn())
        except (sqlite3.Error,OSError,ValueError,KeyError,IndexError,json.JSONDecodeError):
            return {'available':False,'state':'UNKNOWN','reason':'SOURCE_UNAVAILABLE','updated_at':self.clock()}
    def status(self):
        live=self.live_status();shadow=self.shadow_status()
        return {'available':live['available'] and shadow['available'],'live':live,'shadow':shadow,'updated_at':self.clock(),'private_api_calls':0}
    def markets(self,symbol='SOLUSDT',timeframe='1m',limit=100):
        self.bounds(limit)
        if symbol not in ('SOLUSDT','SUIUSDT') or timeframe not in ('1m','5m','15m','1h','4h'):raise ValueError('unsupported market')
        def load():
            key=symbol+'_market'
            schema=self.read(key,"SELECT sql FROM sqlite_master WHERE name='market_klines'")[0]['sql']
            if "'"+symbol+"'" not in schema:raise ValueError('foreign market schema')
            rows=self.read(key,'SELECT * FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?',(symbol,timeframe,max(limit,120)))[::-1]
            if not rows:return {'symbol':symbol,'timeframe':timeframe,'candles':[],'latest_price':None,'state':'WARMING_UP','indicators':{}}
            from solbot.strategy.indicators import ema,rsi,atr,adx,rvol,vwap
            closes=[float(r['close']) for r in rows]
            indicators={'ema20':ema(closes,20),'ema50':ema(closes,50),'rsi6':rsi(closes,6),'atr':atr(rows),'adx':adx(rows),'rvol':rvol(rows),'vwap':vwap(rows)}
            latest={k:v[-1] if v else None for k,v in indicators.items()}
            for i,row in enumerate(rows):row['indicators']={k:v[i] for k,v in indicators.items()}
            age=self.clock()-rows[-1]['ingested_at']
            return {'symbol':symbol,'timeframe':timeframe,'candles':rows[-limit:],'latest_price':rows[-1]['close'],'price_source':'LAST_CLOSED_CANDLE','updated_at':rows[-1]['ingested_at'],'state':'FRESH' if 0<=age<=120000 else 'STALE','indicators':latest}
        return self._safe(load)
    def strategies(self,symbol='SUIUSDT',limit=50):
        self.bounds(limit)
        if symbol not in ('SOLUSDT','SUIUSDT'):raise ValueError('unsupported symbol')
        def load():
            rows=self.read(symbol+'_strategy','SELECT id,payload_json FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT ?',(symbol,limit))
            values=[]
            for row in rows:
                value=json.loads(row['payload_json'])
                if value.get('symbol')!=symbol:raise ValueError('strategy identity')
                values.append(dict(value,event_id=row['id']))
            return {'symbol':symbol,'items':values,'score_label':'策略条件评分','score_is_profit_probability':False,'r1_live_allowed':False}
        return self._safe(load)
    def live_status(self):
        def load():
            self._owner('guardian','guardian_owner',{'schema_version':4,'account_id':'SUI-live','symbol':'SUIUSDT'})
            a=self.read('guardian','SELECT * FROM account_allocations')[0]
            lock=self.read('guardian','SELECT * FROM risk_hard_locks')[0]
            a.update(state='HARD_LOCKED' if lock['locked'] else a['live_state'],account_id='SUI-live',symbol='SUIUSDT',production_authorized=False,
                     verified=0<=self.clock()-a['reconciled_at']<=5000 and not a['recovery_required'],plan_is_exchange_balance=False,reason=lock['reason'])
            return a
        return self._safe(load)
    def live_risk(self):
        def load():
            self._owner('guardian','guardian_owner',{'schema_version':4,'account_id':'SUI-live','symbol':'SUIUSDT'})
            a=self.read('guardian','SELECT * FROM account_allocations')[0];h=self.read('guardian','SELECT * FROM risk_high_watermarks')[0]
            dd=str((Decimal(h['high'])-Decimal(h['equity']))/Decimal(h['high'])) if Decimal(h['high'])>0 else None
            return dict(a,**{k:v for k,v in h.items() if k!='singleton'},drawdown=dd,hard_limit='0.05',assets=self.read('guardian','SELECT * FROM asset_ownership'),custody=self.read('guardian','SELECT * FROM custody_authorizations'),pending=self.read('guardian',"SELECT * FROM pending_actions WHERE status='PENDING' LIMIT 500"),verified=0<=self.clock()-a['equity_at']<=5000 and not a['recovery_required'])
        return self._safe(load)
    def live_orders(self,limit=50,offset=0):
        self.bounds(limit,offset)
        def load():
            self._owner('orders','p5_owner',{'version':5,'account_id':'SUI-live'})
            return {'mode':'LIVE','items':self.read('orders','SELECT * FROM orders ORDER BY created_at DESC,client_id LIMIT ? OFFSET ?',(limit,offset)),'offset':offset,'limit':limit}
        return self._safe(load)
    def live_positions(self):
        def load():
            self._owner('guardian','guardian_owner',{'schema_version':4,'account_id':'SUI-live','symbol':'SUIUSDT'})
            return {'mode':'LIVE','symbol':'SUIUSDT','items':self.read('guardian','SELECT * FROM robot_positions LIMIT 500')}
        return self._safe(load)
    def _shadow_identity(self):
        owner=self.read('shadow','SELECT kind,account_id,symbol FROM execution_owner')
        if len(owner)!=1 or owner[0]['account_id']!='SOL-shadow' or owner[0]['symbol']!='SOLUSDT' or owner[0]['kind'] not in ('SHADOW','SHADOW_P3'):
            raise ValueError('source identity mismatch')
        return owner[0]
    def shadow_status(self):
        def load():
            self._shadow_identity()
            account=self.read('shadow','SELECT * FROM shadow_account')[0]
            initial=Decimal('2000')
            equity=Decimal(account['last_equity'])
            pnl=equity-initial
            closed=self.read('shadow',"SELECT realized_pnl FROM shadow_positions WHERE status='CLOSED'")
            wins=sum(1 for row in closed if Decimal(row['realized_pnl'] or '0')>0)
            trades=int(self.read('shadow',"SELECT COUNT(*) AS n FROM shadow_orders WHERE status='FILLED'")[0]['n'])
            opens=int(self.read('shadow',"SELECT COUNT(*) AS n FROM shadow_positions WHERE status='OPEN'")[0]['n'])
            rate=(Decimal(wins)/Decimal(len(closed))*Decimal('100')) if closed else Decimal('0')
            return {'mode':'SIMULATED','symbol':'SOLUSDT','is_preview':False,'initial_capital':f'{initial:.2f}','equity':f'{equity:.2f}','pnl':f'{pnl:+.2f}','pnl_pct':f'{(pnl/initial*Decimal("100")):+.2f}%','win_rate':f'{rate:.1f}%','total_trades':trades,'active_positions':opens,'fee_asset':'BNB','fee_rate':'0.00075','fee_rate_label':'0.075%','bnb_cap_usdt':'20','bnb_refill_below_usdt':'3','bnb_refill_target_usdt':'10','ledger_fee_rate':account.get('fee_rate'),'bid':account.get('bid'),'ask':account.get('ask'),'account':account,'positions':self.read('shadow',"SELECT * FROM shadow_positions WHERE status='OPEN' LIMIT 500"),'protection':self.read('shadow','SELECT * FROM shadow_protection LIMIT 500')}
        return self._safe(load)
    def shadow_orders(self,limit=50,offset=0):
        self.bounds(limit,offset)
        def load():
            self._shadow_identity()
            items=[]
            for row in self.read('shadow','SELECT * FROM shadow_orders ORDER BY created_at DESC,order_id LIMIT ? OFFSET ?',(limit,offset)):
                items.append({'symbol':row['symbol'],'side':row['side'],'status':row['status'],'price':row['fill_price'] or row['limit_price'],'quantity':row['quantity'],'time':row['created_at'],'order_id':row['order_id'],'fee':row['fee']})
            return {'mode':'SIMULATED','is_preview':False,'items':items,'offset':offset,'limit':limit}
        return self._safe(load)
