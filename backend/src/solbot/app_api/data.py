"""Read-only projections of existing P1–P5 stores; no private exchange clients."""
from contextlib import closing
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import json,os,sqlite3,time
from solbot.app_api.micro_feed import read_micro_snapshot

APTS_STAGE_SHORT={
    'S0_INITIAL_PROTECTION':'S0',
    'S1_PROFIT_PROTECTION':'S1',
    'S2_ADAPTIVE_TRAILING':'S2',
    'S3_TREND_EXHAUSTION':'S3',
}

ROOT=Path('/var/lib/sol-bot/database')
DEFAULTS={'SOLUSDT_market':ROOT/'market.sqlite','SUIUSDT_market':ROOT/'sui-market.sqlite',
 'SOLUSDT_strategy':ROOT/'sol-shadow-strategy.sqlite','SUIUSDT_strategy':ROOT/'sui-strategy-strategy.sqlite',
 'r2_state_read':Path('/var/lib/sol-bot/r2/r2-state-read.sqlite'),
 'r2_state':Path('/var/lib/sol-bot/r2/r2-v2-state.sqlite'),
 'r2_shadow_read':Path('/var/lib/sol-bot/r2/r2-shadow-read.sqlite'),
 'r2_shadow':Path('/var/lib/sol-bot/r2/r2-v2-shadow.sqlite'),
 'binance_vault':Path('/var/lib/sol-bot-app/binance-vault.sqlite'),
 'guardian':ROOT/'sui-guardian-p4.sqlite','shadow':ROOT/'sol-shadow-read.sqlite',
 'orders':Path('/var/lib/sol-bot-execution/orders.sqlite'),
 'binance_snapshot':Path('/var/lib/sol-bot-app/binance-snapshot.sqlite'),
 'micro_status':Path(os.environ.get('SOLBOT_MICRO_STATUS_PATH','/var/lib/sol-bot/sol-micro-status.json')),
 'shadow_status':Path('/var/lib/sol-bot/sol-shadow-status.json'),
 'r2_status':Path('/var/lib/sol-bot/r2/status.json')}

class DataReader:
    def __init__(self,paths=None,clock=None):
        self.paths={k:Path(v).resolve() for k,v in (DEFAULTS|dict(paths or {})).items()}
        self.clock=clock or (lambda:time.time_ns()//1000000)
    @staticmethod
    def bounds(limit,offset=0):
        if type(limit) is not int or not 1<=limit<=500 or type(offset) is not int or not 0<=offset<=100000: raise ValueError('invalid pagination')
    def read(self,key,sql,args=()):
        # The app service root is read-only, so a normal read-only open cannot
        # create the SQLite lock file. immutable=1 reads without locking.
        path=self.paths[key].as_uri()
        last=None
        for uri in (path+'?mode=ro', path+'?mode=ro&immutable=1'):
            try:
                with closing(sqlite3.connect(uri,uri=True,timeout=2)) as db:
                    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
                    return [dict(r) for r in db.execute(sql,args)]
            except sqlite3.OperationalError as exc:
                last=exc
                if 'unable to open database file' not in str(exc): raise
        raise last
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
    @staticmethod
    def _num(value):
        text=format(value,'f')
        if '.' in text:text=text.rstrip('0').rstrip('.')
        return text or '0'
    @staticmethod
    def _compact_volume(value):
        if value>=Decimal('1000000'):return f'{(value/Decimal("1000000")):.2f}M'
        if value>=Decimal('1000'):return f'{(value/Decimal("1000")):.2f}K'
        return f'{value:.2f}'
    def _day_stats(self,key,symbol):
        try:
            rows=self.read(key,"SELECT open_time,open,high,low,close,volume FROM market_klines WHERE symbol=? AND interval='1m' ORDER BY open_time DESC LIMIT 1500",(symbol,))
        except (sqlite3.Error,OSError):
            return {}
        if len(rows)<2:return {}
        rows=rows[::-1]
        end=rows[-1]['open_time']
        window=[row for row in rows if end-row['open_time']<=86400000]
        if len(window)<2 or end-window[0]['open_time']<23*3600000:return {}
        high=max(Decimal(row['high']) for row in window)
        low=min(Decimal(row['low']) for row in window)
        volume=sum((Decimal(row['volume']) for row in window),Decimal('0'))
        first,last=Decimal(window[0]['open']),Decimal(window[-1]['close'])
        change=((last-first)/first*Decimal('100')) if first else None
        return {'high_24h':self._num(high),'low_24h':self._num(low),'volume_24h':self._compact_volume(volume),
                'change_24h':None if change is None else f'{change:+.2f}%'}
    def _mark(self,symbol):
        try:
            rows=self.read(symbol+'_market','SELECT close FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 1',(symbol,'1m'))
            return Decimal(rows[0]['close']) if rows else None
        except (sqlite3.Error,OSError,KeyError,IndexError,ArithmeticError):
            return None
    @staticmethod
    def _stage(value):
        if isinstance(value,str):
            text=value.strip()
            if text[:1] in '{[':
                try:parsed=json.loads(text)
                except json.JSONDecodeError:parsed=None
                if isinstance(parsed,dict):return str(parsed.get('stage') or parsed.get('state') or 'S0')
            if text in ('S0','S1','S2','S3','S2_B','S2_C'):return text
        return 'S0'
    @staticmethod
    def _allowed_live_strategies():
        items=tuple(item.strip() for item in os.environ.get('SOLBOT_LIVE_STRATEGIES','T1').split(',') if item.strip())
        return items or ('T1',)
    def _micro_snapshot(self):
        path=self.paths.get('micro_status')
        if path is None or not path.exists():return None
        now=self.clock()
        snap=read_micro_snapshot(path,now_ms=now,max_age_ms=10_000,symbol='SOLUSDT')
        if snap is not None:
            bid_dec=Decimal(snap['bid'])
            ask_dec=Decimal(snap['ask']) if snap.get('ask') else None
            mid=format((bid_dec+ask_dec)/Decimal('2'),'f') if ask_dec and ask_dec>0 else snap['bid']
            ts=int(snap.get('received_time_ms') or now)
            return {'available':True,'ready':True,'bid':snap['bid'],'ask':snap['ask'],'mid':mid,
                    'spread_bps':snap.get('spread_bps'),'imbalance':snap.get('imbalance'),'buy_ratio':snap.get('buy_ratio'),
                    'exchange_time_ms':snap.get('exchange_time_ms'),'received_time_ms':ts,'age_ms':max(0,now-ts),
                    'price_source':'LIVE_ORDERBOOK','stale':False}
        try:
            payload=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError):
            return None
        if not isinstance(payload,dict) or payload.get('symbol')!='SOLUSDT':return None
        ts=payload.get('feature_generated_time_ms') or payload.get('received_time_ms')
        age=max(0,now-int(ts)) if ts is not None else None
        return {'available':True,'ready':False,'bid':str(payload.get('bid') or ''),'ask':str(payload.get('ask') or ''),
                'spread_bps':str(payload.get('spread_bps') or ''),'imbalance':str(payload.get('imbalance') or ''),
                'age_ms':age,'price_source':'LIVE_ORDERBOOK','stale':True}
    def markets(self,symbol='SOLUSDT',timeframe='1m',limit=100):
        self.bounds(limit)
        if symbol not in ('SOLUSDT','SUIUSDT') or timeframe not in ('1m','5m','15m','1h','4h'):raise ValueError('unsupported market')
        def load():
            key=symbol+'_market'
            schema=self.read(key,"SELECT sql FROM sqlite_master WHERE name='market_klines'")[0]['sql']
            if "'"+symbol+"'" not in schema:raise ValueError('foreign market schema')
            rows=self.read(key,'SELECT * FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?',(symbol,timeframe,max(limit,120)))[::-1]
            if not rows:return {'symbol':symbol,'timeframe':timeframe,'candles':[],'latest_price':None,'state':'WARMING_UP','indicators':{},'micro':self._micro_snapshot() if symbol=='SOLUSDT' else None}
            from solbot.strategy.indicators import ema,rsi,atr,adx,rvol,vwap
            closes=[float(r['close']) for r in rows]
            indicators={'ema20':ema(closes,20),'ema50':ema(closes,50),'rsi6':rsi(closes,6),'atr':atr(rows),'adx':adx(rows),'rvol':rvol(rows),'vwap':vwap(rows)}
            latest={k:v[-1] if v else None for k,v in indicators.items()}
            for i,row in enumerate(rows):row['indicators']={k:v[i] for k,v in indicators.items()}
            age=self.clock()-rows[-1]['ingested_at']
            ema20,ema50=latest.get('ema20'),latest.get('ema50')
            ema_alignment='UNKNOWN';ema_spread_pct=None
            if ema20 is not None and ema50 is not None and ema50>0:
                spread=(float(ema20)-float(ema50))/float(ema50)*100.0
                ema_spread_pct=round(spread,4)
                ema_alignment='BULLISH' if spread>0.05 else ('BEARISH' if spread<-0.05 else 'CONVERGING')
            return dict(symbol=symbol,timeframe=timeframe,candles=rows[-limit:],latest_price=rows[-1]['close'],price_source='LAST_CLOSED_CANDLE',updated_at=rows[-1]['ingested_at'],state='FRESH' if 0<=age<=120000 else 'STALE',indicators=latest,ema_alignment=ema_alignment,ema_spread_pct=ema_spread_pct,volume_24h_asset=symbol[:-4] if symbol.endswith('USDT') else symbol,micro=self._micro_snapshot() if symbol=='SOLUSDT' else None,**self._day_stats(key,symbol))
        return self._safe(load)
    def market_summary(self,symbol='SOLUSDT',timeframe='1m'):
        full=self.markets(symbol,timeframe,2)
        keep=('available','symbol','timeframe','latest_price','price_source','state','indicators','ema_alignment','ema_spread_pct','micro','change_24h','high_24h','low_24h','volume_24h','volume_24h_asset','updated_at','reason')
        return {key:full[key] for key in keep if key in full}
    def _last_accepted_time(self,symbol,strategy_id):
        try:
            rows=self.read(symbol+'_strategy','SELECT MAX(signal_time) AS ts FROM decisions WHERE symbol=? AND strategy_id=? AND accepted=1',(symbol,strategy_id))
            return int(rows[0]['ts']) if rows and rows[0].get('ts') else None
        except (sqlite3.Error,TypeError,ValueError):
            return None
    def _engine_view(self,row,allowed,now_ms,cooldown_remaining_ms=0):
        sid=row.get('strategy_id')
        reasons=list(row.get('reasons') or [])
        ind=row.get('indicators') if isinstance(row.get('indicators'),dict) else {}
        h1=ind.get('1h') if isinstance(ind.get('1h'),dict) else {}
        h4=ind.get('4h') if isinstance(ind.get('4h'),dict) else {}
        if sid=='T1':
            gates={
                'regime_uptrend':(row.get('regime')=='UPTREND' or not reasons) if row.get('regime') else 'REGIME_NOT_UPTREND' not in reasons,
                'trend_4h_above_ema20':'HIGHER_TIMEFRAME_WEAK_TREND' not in reasons if reasons or 'HIGHER_TIMEFRAME_WEAK_TREND' in reasons else True,
                'adx_1h_ge_23':float(h1.get('adx14'))>=23.0 if h1.get('adx14') is not None else 'HIGHER_TIMEFRAME_WEAK_TREND' not in reasons,
                'rsi_recover_above_48':'RSI_PULLBACK_NOT_RECOVERED' not in reasons,
                'rvol_ge_1_2':'RVOL_BELOW_1_2' not in reasons,
                'vwap_reclaim':'VWAP_OR_SUPPORT_NOT_RECLAIMED' not in reasons,
                'breakout_1m':'ONE_MINUTE_BREAKOUT_UNCONFIRMED' not in reasons,
                'chase_1m_ok':'ONE_MINUTE_CHASE_EXCEEDED' not in reasons,
                'cooldown_ok':'STRATEGY_COOLDOWN_ACTIVE' not in reasons and cooldown_remaining_ms==0,
            }
        elif sid=='T2':
            gates={
                'trend_4h_up':'FOUR_HOUR_DOWNTREND' not in reasons and 'FOUR_HOUR_EMA_NOT_UP' not in reasons,
                'pullback_1h':'ONE_HOUR_NOT_PULLED_BACK' not in reasons,
                'reclaim_15m':'FIFTEEN_MINUTE_NOT_RECLAIMED' not in reasons,
                'rvol_15m_ok':'RVOL_15M_BELOW_1' not in reasons,
                'rsi_5m_turn':'RSI_TURN_NOT_CONFIRMED' not in reasons,
                'breakout_1m':'ONE_MINUTE_BREAKOUT_UNCONFIRMED' not in reasons,
            }
        else:
            gates={
                'regime_range':'R1_REQUIRES_RANGE' not in reasons,
                'macro_4h_guard':'MACRO_DOWNTREND_GUARD' not in reasons,
                'not_accelerating_drop':'ACCELERATING_DECLINE' not in reasons,
                'rsi_oversold_recover':'RSI_OVERSOLD_NOT_RECOVERED' not in reasons,
                'rvol_ge_1_5':'RVOL_BELOW_1_5' not in reasons,
                'taker_buy_ok':'TAKER_BUY_NOT_RECOVERED' not in reasons,
                'floor_5m':'FIVE_MINUTE_FLOOR_UNCONFIRMED' not in reasons,
                'breakout_1m':'ONE_MINUTE_BREAKOUT_UNCONFIRMED' not in reasons,
            }
        default_stop='0.010' if sid=='T1' else ('0.008' if sid=='T2' else None)
        return {
            'strategy_id':sid,'live_enabled':sid in allowed,'r1_live_allowed':False,
            'score':row.get('score'),'threshold':75.0,'priority':row.get('priority'),
            'accepted':bool(row.get('accepted')),'regime':row.get('regime'),
            'reasons':reasons,'score_factors':row.get('score_factors'),
            'stop_distance':row.get('stop_distance') or default_stop,'stop_price':row.get('stop_price'),
            'signal_time':row.get('signal_time'),'candidate_time':row.get('candidate_time'),
            'expires_at':row.get('expires_at'),'entry_low':row.get('entry_low'),'entry_high':row.get('entry_high'),
            'signal_id':row.get('signal_id'),'event_id':row.get('event_id'),
            'strategy_version':row.get('strategy_version'),'risk_flags':row.get('risk_flags') or [],
            'gates':gates,'cooldown_remaining_ms':cooldown_remaining_ms,
            'htf_snapshot':{'adx_1h':h1.get('adx14'),'ema20_4h':h4.get('ema20'),'ema50_4h':h4.get('ema50')} if (h1 or h4) else None,
        }
    def _r2_engine_view(self,allowed,now_ms):
        state_rows=self._read_r2('r2_state','SELECT as_of_ms,payload FROM r2_state WHERE singleton=1')
        outbox_rows=self._read_r2('r2_state','SELECT rowid AS id,payload FROM r2_outbox ORDER BY rowid DESC LIMIT 1')
        st={}
        if state_rows:
            try:st=json.loads(state_rows[0].get('payload') or '{}')
            except Exception:st={}
        last_intent={}
        if outbox_rows:
            try:last_intent=json.loads(outbox_rows[0].get('payload') or '{}')
            except Exception:last_intent={}
        stage=str(st.get('state') or 'IDLE')
        reason=str(st.get('reason') or '')
        accepted=stage=='TRIGGERED' or (bool(last_intent.get('accepted')) and int(last_intent.get('expires_at') or 0)>now_ms)
        gates={
            'oversold_event':stage in ('WATCHING','ARMED','TRIGGERED','CONSUMED'),
            'floor_held':reason!='CANDIDATE_LOW_BROKEN' and stage in ('WATCHING','ARMED','TRIGGERED','CONSUMED'),
            'rsi_turn':bool(st.get('recovery_time') or st.get('climax_turn_time')) or stage in ('ARMED','TRIGGERED','CONSUMED'),
            'vwap_or_climax':stage in ('ARMED','TRIGGERED','CONSUMED'),
            'breakout_1m':stage in ('TRIGGERED','CONSUMED') or accepted,
        }
        try:max_rvol=float(st.get('max_rvol') or 0.0)
        except Exception:max_rvol=0.0
        factors={
            'trend':18.0 if stage!='IDLE' else 8.0,
            'momentum':23.0 if gates['rsi_turn'] else (14.0 if gates['oversold_event'] else 6.0),
            'participation':19.0 if max_rvol>=1.5 else 8.0,
            'structure':14.0 if gates['floor_held'] else 4.0,
            'execution':18.0 if gates['breakout_1m'] else (10.0 if stage=='ARMED' else 2.0),
        }
        score=round(sum(factors.values()),1) if not accepted else max(82.0,round(sum(factors.values()),1))
        reasons=[] if accepted else ([reason] if reason else ['WAITING_OVERSOLD_CANDIDATE'])
        return {
            'strategy_id':'R2','live_enabled':'R2' in allowed,'r1_live_allowed':False,
            'score':score,'threshold':75.0,'priority':'HIGH' if accepted else 'REJECT',
            'accepted':accepted,'regime':last_intent.get('regime') or 'DOWNTREND',
            'reasons':reasons,'score_factors':factors,
            'stop_distance':last_intent.get('stop_distance') or '0.012',
            'stop_price':last_intent.get('stop_price') or st.get('candidate_low'),
            'signal_time':last_intent.get('signal_time') or st.get('as_of_ms'),
            'candidate_time':st.get('origin_time'),
            'expires_at':st.get('expires_at'),
            'entry_low':last_intent.get('entry_low'),'entry_high':last_intent.get('entry_high'),
            'signal_id':last_intent.get('signal_id') or st.get('candidate_id'),
            'strategy_version':last_intent.get('strategy_version') or 'r2-rebound-2',
            'risk_flags':[],'gates':gates,'cooldown_remaining_ms':0,'r2_stage':stage,
            'htf_snapshot':None,
        }
    def strategies(self,symbol='SOLUSDT',limit=50):
        self.bounds(limit)
        if symbol not in ('SOLUSDT','SUIUSDT'):raise ValueError('unsupported symbol')
        def load():
            fetch=max(limit,30)
            rows=self.read(symbol+'_strategy','SELECT id,payload_json FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT ?',(symbol,fetch))
            allowed=self._allowed_live_strategies()
            values=[]
            for row in rows:
                value=json.loads(row['payload_json'])
                if value.get('symbol')!=symbol:raise ValueError('strategy identity')
                shown=dict(value,event_id=row['id'],type=value.get('strategy_id'),price=value.get('entry_low'),target=value.get('entry_high'))
                if 'signal_time' in value:shown['time']=value['signal_time']
                values.append(shown)
            now_ms=self.clock()
            cooldown_by_sid={}
            for sid in ('T1','T2','R1'):
                ts=self._last_accepted_time(symbol,sid)
                cooldown_by_sid[sid]=max(0,3_600_000-(now_ms-ts)) if ts is not None else 0
            engines={}
            for sid in ('T1','T2','R1'):
                match=next((row for row in values if row.get('strategy_id')==sid),None)
                if match is not None:
                    engines[sid]=self._engine_view(match,allowed,now_ms,cooldown_by_sid.get(sid,0))
            if symbol=='SOLUSDT':
                try:engines['R2']=self._r2_engine_view(allowed,now_ms)
                except Exception:pass
            live_pool=[row for row in values if row.get('strategy_id') in allowed]
            live_latest=[row for sid in allowed for row in (next((r for r in values if r.get('strategy_id')==sid),None),) if row is not None]
            tradable=[row for row in values if row.get('strategy_id') in ('T1','T2')]
            tradable_latest=[row for sid in ('T1','T2') for row in (next((r for r in values if r.get('strategy_id')==sid),None),) if row is not None]
            latest=next((row for row in live_latest if row.get('accepted')),live_latest[0] if live_latest else (live_pool[0] if live_pool else None))
            if latest is None:
                latest=next((row for row in tradable_latest if row.get('accepted')),tradable_latest[0] if tradable_latest else (tradable[0] if tradable else None))
            if latest is None and values:latest=values[0]
            if latest is None:
                picked=None
            else:
                lsid=latest.get('strategy_id')
                ev=self._engine_view(latest,allowed,now_ms,cooldown_by_sid.get(lsid,0))
                picked={key:latest.get(key) for key in ('score','priority','score_factors','strategy_id','accepted','signal_time','entry_low','entry_high','signal_id','event_id','regime','reasons','stop_distance','stop_price','expires_at','candidate_time','strategy_version','risk_flags')}
                picked.update(live_enabled=lsid in allowed,gates=ev['gates'],cooldown_remaining_ms=ev['cooldown_remaining_ms'],stop_distance=ev['stop_distance'])
            return {'symbol':symbol,'items':values[:limit],'latest':picked,'score':None if picked is None else picked.get('score'),'priority':None if picked is None else picked.get('priority'),'score_factors':None if picked is None else picked.get('score_factors'),'score_label':'策略条件评分','score_is_profit_probability':False,'r1_live_allowed':False,'allowed_live_strategies':list(allowed),'live_strategy_id':None if picked is None else picked.get('strategy_id'),'engines':engines,'indicators_5m':self._closed_indicators(symbol,'5m')}
        return self._safe(load)
    def strategy_summary(self,symbol='SOLUSDT'):
        full=self.strategies(symbol,20)
        keep=('available','symbol','latest','score','priority','score_factors','r1_live_allowed','allowed_live_strategies','live_strategy_id','engines','score_label','indicators_5m','reason')
        return {key:full[key] for key in keep if key in full}
    def _closed_indicators(self,symbol,timeframe):
        try:
            rows=self.read(symbol+'_market','SELECT open_time,high,low,close,volume,quote_volume FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 120',(symbol,timeframe))[::-1]
            if len(rows)<30:return {}
            from solbot.strategy.indicators import rsi,atr,rvol,vwap
            closes=[float(row['close']) for row in rows]
            series={'rsi6':rsi(closes,6),'atr':atr(rows),'rvol':rvol(rows),'vwap':vwap(rows)}
            latest={name:(values[-1] if values else None) for name,values in series.items()}
            close=float(rows[-1]['close'])
            width=latest.get('atr')
            return {'timeframe':timeframe,'open_time':rows[-1]['open_time'],'close':self._json_number(close),'rsi6':self._json_number(latest.get('rsi6')),'rvol':self._json_number(latest.get('rvol')),'vwap':self._json_number(latest.get('vwap')),'atr':self._json_number(width),'atr_pct':self._json_number(None if width is None or close<=0 else width/close*100)}
        except Exception:
            return {}
    @staticmethod
    def _json_number(value):
        if value is None:return None
        try:number=float(value)
        except (TypeError,ValueError):return None
        if number!=number or number in (float('inf'),float('-inf')):return None
        return round(number,4)
    def _live_ledger(self):
        rows=self.read('guardian','SELECT * FROM guardian_owner')
        if len(rows)!=1:raise ValueError('source identity mismatch')
        owner=rows[0]
        if (owner.get('schema_version'),owner.get('account_id'),owner.get('symbol')) not in ((4,'SOL-live','SOLUSDT'),(4,'SUI-live','SUIUSDT')):
            raise ValueError('source identity mismatch')
        return owner
    def _order_owner(self):
        rows=self.read('orders','SELECT * FROM p5_owner')
        if len(rows)!=1 or rows[0].get('version')!=5 or rows[0].get('account_id') not in ('SOL-live','SUI-live'):
            raise ValueError('source identity mismatch')
        return rows[0]
    def live_status(self):
        def load():
            owner=self._live_ledger()
            a=self.read('guardian','SELECT * FROM account_allocations')[0]
            lock=self.read('guardian','SELECT * FROM risk_hard_locks')[0]
            a.update(state='HARD_LOCKED' if lock['locked'] else a['live_state'],account_id='SOL-live',symbol='SOLUSDT',book_symbol='SOLUSDT',guardian_cap_usdt=a.get('allocated'),legacy_account_id=owner['account_id'],production_authorized=False,
                     verified=0<=self.clock()-a['reconciled_at']<=5000 and not a['recovery_required'],plan_is_exchange_balance=False,reason=lock['reason'])
            return a
        return self._safe(load)
    def live_risk(self):
        def load():
            owner=self._live_ledger()
            a=self.read('guardian','SELECT * FROM account_allocations')[0];h=self.read('guardian','SELECT * FROM risk_high_watermarks')[0]
            dd=str((Decimal(h['high'])-Decimal(h['equity']))/Decimal(h['high'])) if Decimal(h['high'])>0 else None
            dd_pct=f'{(Decimal(dd)*Decimal("100")):.2f}' if dd is not None else '0.00'
            state=a.get('live_state')
            daily_loss_usdt='0.00'
            loss_scope='GUARDIAN_WATERMARK'
            if self._optional('binance_vault',"SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_orders' LIMIT 1"):
                loss_scope='BOT_ONLY_EXCLUDES_MANUAL'
                day_start_ms=(int(self.clock())//86_400_000)*86_400_000
                mark_dec=self._mark('SOLUSDT')
                realized_today=Decimal('0')
                for row in self._optional('binance_vault',"SELECT price,quantity,exit_price,realized_pnl,COALESCE(closed_at,stop_updated_at,exit_started_at,filled_at,created_at) AS close_ts FROM bot_orders WHERE status='CLOSED'"):
                    ts=row.get('close_ts')
                    if not ts or int(ts)<day_start_ms:continue
                    if row.get('realized_pnl') not in (None,''):
                        try:
                            realized_today+=Decimal(str(row['realized_pnl']))
                            continue
                        except Exception:pass
                    try:
                        ep,qty,ex=Decimal(str(row.get('price') or '0')),Decimal(str(row.get('quantity') or '0')),Decimal(str(row.get('exit_price') or '0'))
                        if ep>0 and qty>0 and ex>0:realized_today+=(ex-ep)*qty
                    except Exception:pass
                unrealized=Decimal('0')
                if mark_dec is not None and mark_dec>0:
                    for row in self._optional('binance_vault',"SELECT price,quantity FROM bot_orders WHERE status IN ('PARTIALLY_FILLED','FILLED','EXIT_PENDING')"):
                        try:
                            ep,qty=Decimal(str(row.get('price') or '0')),Decimal(str(row.get('quantity') or '0'))
                            if ep>0 and qty>0 and qty*mark_dec>=Decimal('5'):unrealized+=(mark_dec-ep)*qty
                        except Exception:pass
                cap=Decimal(str(a.get('allocated') or '2000'))
                if cap<=0:cap=Decimal('2000')
                loss=max(Decimal('0'),-(realized_today+unrealized))
                daily_loss_usdt=f'{loss:.2f}'
                dd_val=min(Decimal('1'),loss/cap)
                dd=format(dd_val.quantize(Decimal('0.0001')),'f')
                dd_pct=f'{(dd_val*Decimal("100")):.2f}'
                if state=='HARD_LOCKED' and dd_val<Decimal('0.05'):
                    state='NORMAL'
            return dict(a,state=state,account_id='SOL-live',symbol='SOLUSDT',book_symbol='SOLUSDT',guardian_cap_usdt=a.get('allocated'),legacy_account_id=owner['account_id'],**{k:v for k,v in h.items() if k!='singleton'},drawdown=dd,drawdown_pct=dd_pct,daily_loss_usdt=daily_loss_usdt,daily_drawdown_pct=dd_pct,loss_scope=loss_scope,hard_limit='0.05',hard_limit_pct='5.00',assets=self.read('guardian','SELECT * FROM asset_ownership'),custody=self.read('guardian','SELECT * FROM custody_authorizations'),pending=self.read('guardian',"SELECT * FROM pending_actions WHERE status='PENDING' LIMIT 500"),verified=0<=self.clock()-a['equity_at']<=5000 and not a['recovery_required'])
        return self._safe(load)
    def live_orders(self,limit=50,offset=0):
        self.bounds(limit,offset)
        def load():
            self._order_owner()
            return {'mode':'LIVE','symbol':'SOLUSDT','items':self.read('orders','SELECT * FROM orders ORDER BY created_at DESC,client_id LIMIT ? OFFSET ?',(limit,offset)),'offset':offset,'limit':limit}
        return self._safe(load)
    def live_positions(self):
        def load():
            self._live_ledger()
            mark=self._mark('SOLUSDT');slots=[];robot=Decimal('0')
            for row in self.read('guardian','SELECT * FROM robot_positions LIMIT 500'):
                if row.get('asset')!='SOL':continue
                quantity,cost=Decimal(row['quantity']),Decimal(row['cost'])
                robot+=quantity
                entry=(cost/quantity) if quantity>0 else None
                ref_px=mark if (mark is not None and mark>0) else entry
                if ref_px is not None and ref_px>0 and quantity*ref_px<Decimal('5'):continue
                if len(slots)>=2:continue
                stage=self._stage(row.get('protection'))
                slots.append({'source':'ROBOT','slot':len(slots)+1,'intent_id':row['intent_id'],'asset':'SOL','symbol':'SOLUSDT','quantity':self._num(quantity),'entry_price':None if entry is None else self._num(entry),'cost':self._num(cost),'cost_usdt':self._num(cost),'stage':stage,'protection':stage,'risk':row.get('risk'),**self._performance(entry,quantity,mark,'LAST_CLOSED_CANDLE')})
            bot_open=Decimal('0')
            for row in self._optional('binance_vault',"SELECT quantity,price,status FROM bot_orders WHERE status IN ('PARTIALLY_FILLED','FILLED','EXIT_PENDING','EXITING')"):
                try:
                    qty=Decimal(str(row.get('quantity') or '0'))
                    entry_px=Decimal(str(row.get('price') or '0'))
                    ref_px=mark if (mark is not None and mark>0) else entry_px
                    if qty>0 and (ref_px<=0 or qty*ref_px>=Decimal('5')):
                        bot_open+=qty
                except Exception: pass
            account,observed,manual_orders=self._user_account()
            if not account:
                for row in self._optional('guardian','SELECT asset,free,locked,observed_at FROM exchange_balances'):
                    if row['asset'] not in ('SOL','USDT','BNB'):continue
                    account[row['asset']]={'free':row['free'],'locked':row['locked']}
                    observed=row['observed_at'] if observed is None else max(observed,row['observed_at'])
            manual=[]
            sol=account.get('SOL')
            if sol:
                excess=Decimal(sol['free'])+Decimal(sol['locked'])-robot-bot_open
                entry=self._average_entry(manual_orders)
                if entry is None:
                    buys=[row for row in manual_orders if row.get('side')=='BUY' and row.get('status')=='FILLED' and Decimal(str(row.get('price') or '0'))>0]
                    if buys:
                        entry=Decimal(str(max(buys, key=lambda row: int(row.get('time') or 0))['price']))
                manual_ref=mark if (mark is not None and mark>0) else entry
                is_dust_manual=(manual_ref is not None and manual_ref>0 and excess*manual_ref<Decimal('5'))
                if excess>0 and (bot_open==0 or excess>=Decimal('0.001')) and not is_dust_manual:
                    manual.append({'source':'MANUAL','intent_id':'manual:SOL','asset':'SOL','symbol':'SOLUSDT','quantity':self._num(excess),'free':sol['free'],'locked':sol['locked']})
                    if entry is not None:
                        shown=self._num(entry)
                        manual[0]['entry_price']=shown
                        manual[0]['cost']=shown
                    manual[0].update(self._performance(entry,excess,mark,'LAST_CLOSED_CANDLE'))
            synced=bool(account)
            return {'mode':'LIVE','symbol':'SOLUSDT','slots':slots,'manual':manual,'manual_orders':manual_orders,'items':slots+manual,'account':account,'balance_observed_at':observed,'balance_state':'FRESH' if synced else 'UNSYNCED','reason':None if synced else 'BALANCE_NOT_SYNCED'}
        return self._safe(load)
    def _performance(self,entry,quantity,mark,source):
        if mark is None or entry is None or entry<=0 or quantity is None:
            return {'mark_price':None if mark is None else self._num(mark),'change_pct':None,'pnl_usdt':None,'price_source':source if mark is not None else None}
        change=(mark-entry)/entry*Decimal('100')
        pnl=(mark-entry)*quantity
        return {'mark_price':self._num(mark),'change_pct':f'{change:+.2f}%','pnl_usdt':f'{pnl:+.2f}','unrealized_pnl':f'{pnl:+f}','price_source':source}
    def _user_account(self):
        path=self.paths.get('binance_snapshot')
        if path is None or not path.exists(): return {},None,[]
        try:
            balances=self.read('binance_snapshot','SELECT asset,free,locked,observed_at FROM balances')
            orders=self.read('binance_snapshot','SELECT order_id,symbol,side,status,price,quantity,executed_qty,time FROM manual_orders ORDER BY time DESC LIMIT 100')
        except (sqlite3.Error,OSError):
            return {},None,[]
        account={};observed=None
        for row in balances:
            if row['asset'] not in ('SOL','USDT','BNB'): continue
            free,locked=Decimal(str(row['free'] or '0')),Decimal(str(row['locked'] or '0'))
            account[row['asset']]={'free':row['free'],'locked':row['locked'],'total':self._num(free+locked)}
            observed=row['observed_at'] if observed is None else max(observed,row['observed_at'])
        return account,observed,orders
    @staticmethod
    def _average_entry(orders):
        qty,cost=Decimal(0),Decimal(0)
        rows=sorted((row for row in orders if row.get('status')=='FILLED' and row.get('side') in ('BUY','SELL')), key=lambda row: int(row.get('time') or 0))
        for row in rows:
            try:
                executed=Decimal(str(row.get('executed_qty') or row.get('quantity') or '0'))
                price=Decimal(str(row.get('price') or '0'))
            except Exception:
                continue
            if executed<=0 or price<=0: continue
            if row.get('side')=='BUY':
                qty+=executed; cost+=executed*price
            elif qty>0:
                sold=min(executed, qty)
                cost-=(cost/qty)*sold
                qty-=sold
        if qty<=0 or cost<=0: return None
        return (cost/qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    def _shadow_identity(self):
        owner=self.read('shadow','SELECT kind,account_id,symbol FROM execution_owner')
        if len(owner)!=1 or owner[0]['account_id']!='SOL-shadow' or owner[0]['symbol']!='SOLUSDT' or owner[0]['kind'] not in ('SHADOW','SHADOW_P3'):
            raise ValueError('source identity mismatch')
        return owner[0]
    def _optional(self,key,sql,args=()):
        try:return self.read(key,sql,args)
        except (sqlite3.Error,OSError):return []
    def _read_r2(self,base_key,sql,args=()):
        for key in (base_key+'_read',base_key):
            path=self.paths.get(key)
            if path is not None and path.exists():
                try:
                    return self.read(key,sql,args)
                except (sqlite3.Error,OSError):
                    continue
        return []
    def _shadow_health(self):
        now = self.clock()
        try:
            status = json.loads(self.paths['shadow_status'].read_text(encoding='utf-8'))
            if not isinstance(status, dict) or status.get('symbol') != 'SOLUSDT':
                raise ValueError('invalid shadow status')
        except (OSError, ValueError):
            return {'synced': False, 'engine_status': 'UNKNOWN', 'reason': 'ENGINE_STATUS_UNAVAILABLE'}
        heartbeat = status.get('updated_at')
        account = status.get('shadow')
        quote = account.get('last_quote_ms') if isinstance(account, dict) else None
        recent = lambda value, age: type(value) is int and 0 <= now - value <= age
        reason = None
        if not recent(heartbeat, 10000):
            reason = 'ENGINE_HEARTBEAT_STALE'
        elif status.get('error'):
            reason = 'ENGINE_ERROR'
        elif status.get('status') != 'ready':
            reason = 'ENGINE_NOT_READY'
        elif not recent(quote, 15000):
            reason = 'SHADOW_QUOTE_STALE'
        return {'synced': reason is None, 'engine_status': status.get('status', 'UNKNOWN'),
                'reason': reason, 'heartbeat_at': heartbeat, 'last_market_at': quote,
                'last_error': status.get('error')}

    def shadow_status(self):
        def load():
            self._shadow_identity()
            account=self.read('shadow','SELECT * FROM shadow_account')[0]
            initial=Decimal('2000')
            equity=Decimal(account['last_equity'])
            r2_acct=self._read_r2('r2_shadow','SELECT * FROM shadow_account')
            if r2_acct:
                try:
                    equity+=Decimal(str(r2_acct[0].get('last_equity') or '2000'))-Decimal('2000')
                except Exception:
                    pass
            pnl=equity-initial
            closed=self.read('shadow',"SELECT realized_pnl FROM shadow_positions WHERE status='CLOSED'")+self._read_r2('r2_shadow',"SELECT realized_pnl FROM shadow_positions WHERE status='CLOSED'")
            wins=sum(1 for row in closed if Decimal(row['realized_pnl'] or '0')>0)
            r2_trades_rows=self._read_r2('r2_shadow',"SELECT COUNT(*) AS n FROM shadow_orders WHERE status='FILLED'")
            r2_trades=int(r2_trades_rows[0]['n']) if r2_trades_rows else 0
            trades=int(self.read('shadow',"SELECT COUNT(*) AS n FROM shadow_orders WHERE status='FILLED'")[0]['n'])+r2_trades
            r2_open_positions=self._read_r2('r2_shadow',"SELECT * FROM shadow_positions WHERE status='OPEN' LIMIT 500")
            rate=(Decimal(wins)/Decimal(len(closed))*Decimal('100')) if closed else Decimal('0')
            prot_rows=self._optional('shadow','SELECT * FROM shadow_protection LIMIT 500')+self._read_r2('r2_shadow','SELECT * FROM shadow_protection LIMIT 500')
            prot_map={}
            for pr in prot_rows:
                try:
                    prot_map[pr['position_id']]=json.loads(pr['state_json'])
                except (KeyError,TypeError,ValueError):
                    pass
            mark=None
            try:
                if account.get('bid') is not None and Decimal(str(account['bid']))>0:
                    mark=Decimal(str(account['bid']))
            except Exception:
                mark=None
            if mark is None:
                mark=self._mark('SOLUSDT')
            raw_positions=self.read('shadow',"SELECT * FROM shadow_positions WHERE status='OPEN' LIMIT 500")+r2_open_positions
            pending_buy_pids={'p-'+str(r['order_id']) for r in self.read('shadow',"SELECT order_id FROM shadow_orders WHERE status='PENDING' AND side='BUY'")+self._read_r2('r2_shadow',"SELECT order_id FROM shadow_orders WHERE status='PENDING' AND side='BUY'")}
            filtered_positions=[]
            for pos in raw_positions:
                pid=pos.get('position_id')
                if pid in pending_buy_pids:
                    filtered_positions.append(pos)
                    continue
                try:
                    qty_dec=Decimal(str(pos.get('quantity') or '0'))
                    entry_dec=Decimal(str(pos.get('entry_price') or '0'))
                    ref_px=mark if (mark is not None and mark>0) else entry_dec
                    if ref_px>0 and qty_dec*ref_px<Decimal('5'):
                        continue
                except Exception:
                    pass
                filtered_positions.append(pos)
            opens=len(filtered_positions)
            positions=[]
            for pos in filtered_positions:
                enriched=dict(pos)
                st=prot_map.get(pos.get('position_id')) or {}
                stage=APTS_STAGE_SHORT.get(str(st.get('stage') or ''),'S0')
                stop_price=st.get('stop_price')
                reversal_hits=int(st.get('reversal_streak') or st.get('reversal_hits') or 0)
                max_hits=int(st.get('reversal_confirm_ticks') or 2)
                entry_ms=st.get('entry_time_ms') or pos.get('opened_at')
                try:
                    entry_dec=Decimal(str(pos.get('entry_price') or '0'))
                    qty_dec=Decimal(str(pos.get('quantity') or '0'))
                    if mark is not None and entry_dec>0 and qty_dec>0:
                        perf=self._performance(entry_dec,qty_dec,mark,'SHADOW_BID')
                        enriched.update(perf)
                        enriched['unrealized_pnl']=perf.get('pnl_usdt')
                        enriched['unrealized_pnl_pct']=perf.get('change_pct')
                except Exception:
                    pass
                if stop_price is not None:
                    enriched['stop_price']=str(stop_price)
                enriched['stage']=stage
                enriched['reversal_hits']=reversal_hits
                enriched['max_reversal_hits']=max_hits
                positions.append(enriched)
            return {'mode':'SIMULATED','symbol':'SOLUSDT','synced':True,'is_preview':False,'initial_capital':f'{initial:.2f}','equity':f'{equity:.2f}','pnl':f'{pnl:+.2f}','pnl_pct':f'{(pnl/initial*Decimal("100")):+.2f}%','win_rate':f'{rate:.1f}%','total_trades':trades,'active_positions':opens,'fee_asset':'BNB','fee_rate':'0.00075','fee_rate_label':'0.075%','bnb_cap_usdt':'20','bnb_refill_below_usdt':'3','bnb_refill_target_usdt':'10','ledger_fee_rate':account.get('fee_rate'),'bid':account.get('bid'),'ask':account.get('ask'),'account':account,'positions':positions,'protection':prot_rows,'rules':{'min_order_usdt':'500','max_order_usdt':'1000','t1_stop_distance':'0.010','stag_hours':0,'max_positions':2}}
        result = self._safe(load)
        health = self._shadow_health()
        if not result.get('available'):
            health.update(synced=False, reason='SOURCE_UNAVAILABLE')
        result = dict(result, **health)
        try:
            r2 = json.loads(self.paths['r2_status'].read_text(encoding='utf-8'))
            stamp = r2.get('updated_at')
            if r2.get('strategy_id') != 'R2' or r2.get('symbol') != 'SOLUSDT':
                raise ValueError('R2 source mismatch')
            r2['synced'] = (type(stamp) is int and 0 <= self.clock()-stamp <= 10000
                            and r2.get('status') == 'ready' and not r2.get('error'))
            result['r2'] = r2
        except (OSError, ValueError, AttributeError):
            result['r2'] = {'synced': False, 'status': 'unavailable'}
        return result
    def shadow_orders(self,limit=50,offset=0):
        self.bounds(limit,offset)
        def load():
            self._shadow_identity()
            cap=min(500,limit+offset)
            p3_rows=self.read('shadow','SELECT * FROM shadow_orders ORDER BY created_at DESC,order_id LIMIT ?',(cap,))
            r2_rows=self._read_r2('r2_shadow','SELECT * FROM shadow_orders ORDER BY created_at DESC,order_id LIMIT ?',(cap,))
            seen=set();merged=[]
            for row in sorted(p3_rows+r2_rows,key=lambda r:(int(r.get('created_at') or 0),str(r.get('order_id') or '')),reverse=True):
                oid=row.get('order_id')
                if oid in seen:continue
                seen.add(oid);merged.append(row)
            items=[]
            for row in merged[offset:offset+limit]:
                items.append({'symbol':row['symbol'],'side':row['side'],'status':row['status'],'price':row['fill_price'] or row['limit_price'],'quantity':row['quantity'],'time':row['created_at'],'order_id':row['order_id'],'fee':row['fee'],'strategy_id':row.get('strategy_id') or 'T1'})
            return {'mode':'SIMULATED','is_preview':False,'items':items,'offset':offset,'limit':limit}
        return self._safe(load)
