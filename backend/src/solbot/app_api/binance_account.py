"""User-entered Binance credentials. Live buys follow the user switch; withdrawals stay refused."""
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from solbot.mathematics.decimal_utils import floor_step
from solbot.strategy.t2_swing import risk_notional

HOST = 'https://api.binance.com'
READ_PATHS = frozenset((
    '/api/v3/time', '/api/v3/account', '/api/v3/openOrders', '/api/v3/allOrders',
    '/api/v3/order', '/api/v3/myTrades', '/api/v3/exchangeInfo',
    '/api/v3/ticker/bookTicker', '/sapi/v1/account/apiRestrictions',
))
ASSETS = ('SOL', 'USDT', 'BNB')
STOP_TYPES = frozenset(('STOP_LOSS', 'STOP_LOSS_LIMIT'))
BOT_ORDER_PREFIXES = ('sb5-', 'p5p-', 'p5x-', 'cbotby', 'cbotsl', 'cbotex', 'cbot2s', 'cbot2x')
STOP_PREFIXES = ('cbotsl', 'cbot2s', 'p5p-')
EXIT_LIMIT_PREFIXES = ('cbot2x', 'cbotex', 'p5x-')
EXIT_MARKET_PREFIXES = ('cbot2x', 'cbotex', 'p5x-')
DELETE_PREFIXES = ('cbotby', 'cbotsl', 'cbotex', 'cbot2s', 'cbot2x', 'p5p-', 'p5x-')
DEFAULT_LIVE_STRATEGIES = tuple(
    item.strip() for item in os.environ.get('SOLBOT_LIVE_STRATEGIES', 'T1').split(',') if item.strip()
) or ('T1',)
APTS_STAGE_SHORT = {
    'S0_INITIAL_PROTECTION': 'S0',
    'S1_PROFIT_PROTECTION': 'S1',
    'S2_ADAPTIVE_TRAILING': 'S2',
    'S3_TREND_EXHAUSTION': 'S3',
}
DEFAULT_FEE_RATE = '0.00075'
DEFAULT_SLIPPAGE_RATE = '0.00075'
EXIT_PENDING_TIMEOUT_MS = 5000
STOP_REPLACE_MIN_INTERVAL_MS = 15000
STOP_REPLACE_MIN_STEP_PCT = Decimal('0.0015')
CROSS_STRATEGY_COOLDOWN_MS = 900_000
MIN_ORDER_USDT = Decimal('500')
MAX_ORDER_USDT = Decimal('1000')
DUST_NOTIONAL_USDT = Decimal('5')


def _fields(query):
    return dict(parse_qsl(query, keep_blank_values=True))


class UrlTransport:
    def request(self, path, query, headers, method='GET'):
        method = method.upper()
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError('BINANCE_REJECTED')
        fields = _fields(query)
        if method == 'GET':
            if path not in READ_PATHS:
                raise ValueError('BINANCE_REJECTED')
            url = HOST + path + ('?' + query if query else '')
            body = None
        elif method == 'POST' and path == '/api/v3/order/cancelReplace':
            if fields.get('symbol') != 'SOLUSDT':
                raise ValueError('BINANCE_REJECTED')
            if fields.get('cancelReplaceMode') != 'STOP_ON_FAILURE':
                raise ValueError('BINANCE_REJECTED')
            if fields.get('side') != 'SELL' or fields.get('type') not in STOP_TYPES | {'LIMIT'}:
                raise ValueError('BINANCE_REJECTED')
            new_cid = str(fields.get('newClientOrderId', ''))
            cancel_cid = str(fields.get('cancelOrigClientOrderId', ''))
            if not new_cid.startswith(STOP_PREFIXES + EXIT_LIMIT_PREFIXES):
                raise ValueError('BINANCE_REJECTED')
            if cancel_cid and not cancel_cid.startswith(DELETE_PREFIXES):
                raise ValueError('BINANCE_REJECTED')
            if not cancel_cid and not fields.get('cancelOrderId'):
                raise ValueError('BINANCE_REJECTED')
            url, body = HOST + path, query.encode()
        elif method in ('POST', 'DELETE') and path == '/api/v3/order':
            if fields.get('symbol') != 'SOLUSDT':
                raise ValueError('BINANCE_REJECTED')
            if method == 'POST' and fields.get('side') == 'BUY':
                if fields.get('type') != 'LIMIT' or not str(fields.get('newClientOrderId', '')).startswith('cbotby'):
                    raise ValueError('BINANCE_REJECTED')
            elif method == 'POST':
                if fields.get('side') != 'SELL':
                    raise ValueError('BINANCE_REJECTED')
                kind = fields.get('type')
                new_cid = str(fields.get('newClientOrderId', ''))
                if kind == 'MARKET':
                    if not new_cid.startswith(EXIT_MARKET_PREFIXES):
                        raise ValueError('BINANCE_REJECTED')
                elif kind not in STOP_TYPES | {'LIMIT'}:
                    raise ValueError('BINANCE_REJECTED')
            if method == 'DELETE' and not str(fields.get('origClientOrderId', '')).startswith(DELETE_PREFIXES):
                raise ValueError('BINANCE_REJECTED')
            if method == 'POST':
                url, body = HOST + path, query.encode()
            else:
                url, body = HOST + path + '?' + query, None
        else:
            raise ValueError('BINANCE_REJECTED')
        try:
            request = Request(url, data=body, headers={**headers, 'Content-Type': 'application/x-www-form-urlencoded'}, method=method)
            with urlopen(request, timeout=12) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError('BINANCE_REJECTED')
                return json.loads(raw)
        except HTTPError as error:
            try:
                payload = json.loads(error.read(100000))
            except (ValueError, OSError):
                payload = {}
            code = payload.get('code') if isinstance(payload, dict) else None
            if code == -2011:
                raise ValueError('ORDER_GONE') from None
            if code == -2022 and isinstance(payload.get('data'), dict):
                data = payload['data']
                cancel_code = (data.get('cancelResponse') or {}).get('code') if isinstance(data.get('cancelResponse'), dict) else None
                new_code = (data.get('newOrderResponse') or {}).get('code') if isinstance(data.get('newOrderResponse'), dict) else None
                if data.get('cancelResult') == 'FAILURE' or cancel_code == -2011:
                    raise ValueError('ORDER_GONE') from None
                if new_code == -2010:
                    raise ValueError('INSUFFICIENT_FREE_SOL') from None
            if code in (-2010, -2015):
                raise ValueError('TRADE_PERMISSION_REQUIRED' if code == -2015 else 'INSUFFICIENT_FREE_SOL') from None
            raise ValueError('PROTECTION_REJECTED') from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('BINANCE_UNREACHABLE') from None


class AccountVault:
    def __init__(self, directory, clock=None, transport=None):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: __import__('time').time_ns() // 1_000_000)
        self.transport = transport or UrlTransport()
        self.key_path = self.root / 'binance-vault.key'
        self.db_path = self.root / 'binance-vault.sqlite'
        self.snapshot_path = self.root / 'binance-snapshot.sqlite'
        self._rules_cache = None

    def status(self):
        row = self._meta()
        if row is None:
            return {'configured': False, 'withdrawals_enabled': False, 'orders_enabled': False, 'spot_trading_enabled': False}
        orders_enabled = bool(row.get('orders_enabled'))
        return {'configured': True, 'key_hint': row['key_hint'], 'saved_at': row['saved_at'],
                'last_sync_at': row['last_sync_at'], 'last_error': row['last_error'] or None,
                'withdrawals_enabled': False, 'orders_enabled': orders_enabled, 'spot_trading_enabled': orders_enabled}

    def save(self, api_key, secret):
        api_key, secret = self._shape(api_key, secret)
        try:
            spot_enabled = self._permissions(api_key, secret)
            self._store(api_key, secret, orders_enabled=spot_enabled)
            summary = self._sync(api_key, secret, spot_hint=spot_enabled)
        finally:
            api_key = secret = None
        summary.update(self.status())
        return summary

    def sync(self):
        api_key, secret = self._load()
        try:
            summary = self._sync(api_key, secret)
        finally:
            api_key = secret = None
        summary.update(self.status())
        return summary

    def refresh_account(self):
        """Pull balances, fills, and the bid even when no stop is open."""
        if not self.status().get('configured'):
            return {'synced': False}
        api_key, secret = self._load()
        try:
            summary = self._sync(api_key, secret)
            try:
                book = self.transport.request('/api/v3/ticker/bookTicker', 'symbol=SOLUSDT', {})
                bid = Decimal(str((book or {}).get('bidPrice') or '0'))
                if bid > 0:
                    self._remember_mark(bid, self.clock())
                    summary['mark_price'] = format(bid, 'f')
            except Exception:
                pass
            return summary
        finally:
            api_key = secret = None

    def clear(self):
        try:
            self.cancel_manual()
        except ValueError:
            pass
        with self._db() as db:
            db.execute('DELETE FROM credentials')
            db.execute('DELETE FROM sync_state')
            db.execute('DELETE FROM protections')
            db.execute('DELETE FROM custody_apts')
        if self.snapshot_path.exists():
            self.snapshot_path.unlink()
        return {'configured': False, 'cleared': True, 'withdrawals_enabled': False, 'orders_enabled': False}

    def protection(self):
        if not self.db_path.exists():
            return None
        with self._db() as db:
            row = db.execute("SELECT intent_id,client_order_id,exchange_order_id,quantity,stop_price,limit_price,entry_price,status,updated_at,placed_at FROM protections WHERE status IN ('OPEN','EXITING') ORDER BY updated_at DESC LIMIT 1").fetchone()
            stage = db.execute('SELECT stage,note,state_json FROM custody_apts WHERE intent_id=?', (row[0],)).fetchone() if row else None
        if row is None:
            return None
        parsed_apts = {}
        if stage and len(stage) > 2 and stage[2]:
            try:
                parsed_apts = json.loads(stage[2])
            except (TypeError, ValueError):
                parsed_apts = {}
        entry_ms = int(parsed_apts.get('entry_time_ms') or row[9] or 0)
        apts_info = {
            'stage': stage[0] if stage else 'S0',
            'initial_stop_pct': str(parsed_apts.get('stop_distance') or '0.008'),
            'stop_price': str(parsed_apts.get('stop_price') or row[4] or ''),
            'peak_price': str(parsed_apts.get('peak_price') or ''),
            'reversal_hits': int(parsed_apts.get('reversal_streak') or 0),
            'max_reversal_hits': int(parsed_apts.get('reversal_confirm_ticks') or 2),
            'entry_time_ms': entry_ms or None,
            'stagnation_deadline_ms': None,
        }
        return {'intent_id': row[0], 'client_order_id': row[1], 'exchange_order_id': row[2], 'quantity': row[3],
                'stop_price': row[4], 'limit_price': row[5], 'entry_price': row[6], 'status': row[7],
                'updated_at': row[8], 'placed_at': row[9], 'stage': apts_info['stage'],
                'hold_note': stage[1] if stage and len(stage) > 1 else '', 'apts': apts_info}

    def bot_orders(self, limit=50, offset=0):
        if not self.db_path.exists():
            return []
        with self._db() as db:
            rows = db.execute(
                "SELECT b.signal_id,b.client_order_id,b.exchange_order_id,b.price,b.quantity,b.quote,b.status,b.created_at,"
                "b.stop_distance,b.stop_client_id,b.filled_at,b.high_price,b.exit_client_id,b.stop_price,b.stop_updated_at,b.exit_started_at,"
                "b.strategy_id,b.expires_at,c.stage,c.note,c.state_json,b.exit_price,b.closed_at,b.realized_pnl "
                "FROM bot_orders b LEFT JOIN custody_apts c ON c.intent_id=('bot:' || b.signal_id) "
                "ORDER BY COALESCE(b.filled_at,b.created_at) DESC, b.signal_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        items = []
        for r in rows:
            parsed = {}
            if r[20]:
                try:
                    parsed = json.loads(r[20])
                except (TypeError, ValueError):
                    parsed = {}
            entry_ms = int(parsed.get('entry_time_ms') or r[10] or r[7] or 0)
            apts_info = {
                'stage': r[18] or 'S0',
                'initial_stop_pct': str(r[8] or parsed.get('stop_distance') or '0.010'),
                'stop_price': str(r[13] or parsed.get('stop_price') or ''),
                'peak_price': str(r[11] or parsed.get('peak_price') or ''),
                'reversal_hits': int(parsed.get('reversal_streak') or 0),
                'max_reversal_hits': int(parsed.get('reversal_confirm_ticks') or 2),
                'entry_time_ms': entry_ms or None,
                'stagnation_deadline_ms': None,
            }
            items.append({
                'source': 'BOT', 'symbol': 'SOLUSDT', 'side': 'BUY',
                'strategy_id': r[16] or parsed.get('strategy_id') or 'T1',
                'signal_id': r[0], 'order_id': r[2] or r[1], 'client_order_id': r[1],
                'price': r[3], 'quantity': r[4], 'executed_qty': r[4] if r[6] in ('FILLED', 'EXIT_PENDING', 'CLOSED') else '0',
                'quote': r[5], 'status': r[6], 'time': r[10] or r[7], 'created_at': r[7],
                'filled_at': r[10], 'expires_at': r[17], 'stop_distance': r[8] or '0.010', 'stop_client_id': r[9] or '',
                'stop_price': r[13] or '', 'high_price': r[11] or '', 'exit_client_id': r[12] or '',
                'exit_price': r[21] or '', 'closed_at': r[22], 'realized_pnl': r[23] or '',
                'stage': apts_info['stage'], 'hold_note': r[19] or '', 'apts': apts_info,
            })
        return items

    @staticmethod
    def _is_dust_order(qty_raw, price_raw, mark_dec=None, status=None):
        """Return True if a filled/exiting SOL bot order is residual dust worth < 5 USDT."""
        try:
            qty = Decimal(str(qty_raw or '0'))
            ref = mark_dec if (mark_dec is not None and mark_dec > 0) else Decimal(str(price_raw or '0'))
        except Exception:
            return False
        if status == 'NEW':
            return False
        if qty <= 0:
            return True
        if ref <= 0:
            return False
        if status == 'PARTIALLY_FILLED':
            return False
        return qty * ref < DUST_NOTIONAL_USDT

    def _current_mark_dec(self):
        mark = self.mark()
        if isinstance(mark, dict) and mark.get('mark_price'):
            try:
                val = Decimal(str(mark['mark_price']))
                if val > 0:
                    return val
            except Exception:
                pass
        return None

    def open_bot_slots(self, mark_price=None):
        mark_dec = Decimal(str(mark_price)) if mark_price is not None else self._current_mark_dec()
        rows = [
            row for row in self.bot_orders(limit=50, offset=0)
            if row.get('status') in ('NEW', 'PARTIALLY_FILLED', 'FILLED', 'EXIT_PENDING')
            and not self._is_dust_order(row.get('quantity'), row.get('price'), mark_dec, row.get('status'))
        ]
        slots = []
        for idx, row in enumerate(rows[:2], start=1):
            qty = row.get('quantity') or '0'
            price = row.get('price') or '0'
            slots.append({
                'source': 'ROBOT', 'slot': idx, 'intent_id': f"bot:{row['signal_id']}",
                'signal_id': row['signal_id'], 'strategy_id': row.get('strategy_id') or 'T1',
                'asset': 'SOL', 'symbol': 'SOLUSDT', 'quantity': qty,
                'entry_price': price, 'cost': price, 'cost_usdt': row.get('quote') or '',
                'status': row.get('status'), 'stage': row.get('stage') or 'S0',
                'protection': row.get('stage') or 'S0', 'stop_price': row.get('stop_price') or '',
                'stop_distance': row.get('stop_distance') or '0.010',
                'stop_order_id': row.get('stop_client_id') or '',
                'hold_note': row.get('hold_note') or '', 'apts': row.get('apts'),
            })
        return slots

    def bot_daily_risk(self, mark_price=None, now_ms=None, reference_cap=Decimal('2000')):
        """Compute daily realized + unrealized loss strictly from bot_orders (excludes user manual trades)."""
        now = int(now_ms if now_ms is not None else self.clock())
        day_start_ms = (now // 86_400_000) * 86_400_000
        mark_dec = Decimal(str(mark_price)) if mark_price is not None else self._current_mark_dec()
        realized_today = Decimal('0')
        unrealized = Decimal('0')
        if self.db_path.exists():
            with self._db() as db:
                closed_rows = db.execute(
                    "SELECT price,quantity,exit_price,realized_pnl,COALESCE(closed_at,stop_updated_at,exit_started_at,filled_at,created_at) AS close_ts "
                    "FROM bot_orders WHERE status='CLOSED'"
                ).fetchall()
                open_rows = db.execute(
                    "SELECT price,quantity,status FROM bot_orders WHERE status IN ('PARTIALLY_FILLED','FILLED','EXIT_PENDING')"
                ).fetchall()
            for entry_raw, qty_raw, exit_raw, pnl_raw, close_ts in closed_rows:
                if not close_ts or int(close_ts) < day_start_ms:
                    continue
                if pnl_raw not in (None, ''):
                    try:
                        realized_today += Decimal(str(pnl_raw))
                        continue
                    except Exception:
                        pass
                try:
                    entry = Decimal(str(entry_raw or '0'))
                    qty = Decimal(str(qty_raw or '0'))
                    exit_px = Decimal(str(exit_raw or '0'))
                    if entry > 0 and qty > 0 and exit_px > 0:
                        realized_today += (exit_px - entry) * qty
                except Exception:
                    continue
            if mark_dec is not None and mark_dec > 0:
                for entry_raw, qty_raw, status in open_rows:
                    if self._is_dust_order(qty_raw, entry_raw, mark_dec, status):
                        continue
                    try:
                        entry = Decimal(str(entry_raw or '0'))
                        qty = Decimal(str(qty_raw or '0'))
                        if entry > 0 and qty > 0:
                            unrealized += (mark_dec - entry) * qty
                    except Exception:
                        continue
        net_pnl = realized_today + unrealized
        daily_loss_usdt = max(Decimal('0'), -net_pnl)
        try:
            cap = Decimal(str(reference_cap or '2000'))
        except Exception:
            cap = Decimal('2000')
        if cap <= 0:
            cap = Decimal('2000')
        drawdown = min(Decimal('1'), daily_loss_usdt / cap)
        drawdown_pct = f'{(drawdown * Decimal("100")):.2f}'
        return {
            'realized_pnl_today': f'{realized_today:.2f}',
            'unrealized_pnl': f'{unrealized:.2f}',
            'daily_net_pnl_usdt': f'{net_pnl:.2f}',
            'daily_loss_usdt': f'{daily_loss_usdt:.2f}',
            'drawdown': format(drawdown.quantize(Decimal('0.0001')), 'f'),
            'drawdown_pct': drawdown_pct,
            'loss_scope': 'BOT_ONLY_EXCLUDES_MANUAL',
            'day_start_ms': day_start_ms,
        }

    def live_state(self):
        if not self.db_path.exists():
            return 'LIVE_OFF'
        with self._db() as db:
            row = db.execute('SELECT state FROM user_live WHERE singleton=1').fetchone()
        return row[0] if row and row[0] in ('LIVE_ON', 'LIVE_OFF', 'PAUSED', 'HARD_LOCKED') else 'LIVE_OFF'

    def enable_live(self):
        if self.live_state() == 'HARD_LOCKED':
            return {'accepted': False, 'state': 'HARD_LOCKED', 'code': 'DENY_HARD_LOCKED'}
        if not self.status().get('configured'):
            raise ValueError('NOT_CONFIGURED')
        api_key, secret = self._load()
        try:
            self._require_spot(api_key, secret)
        finally:
            api_key = secret = None
        now = self.clock()
        with self._db() as db:
            db.execute('INSERT INTO user_live VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,reason=excluded.reason', ('LIVE_ON', now, 'USER_SWITCH'))
        return {'accepted': True, 'state': 'LIVE_ON', 'code': 'LIVE_ON', 'buy_orders': 0}

    def disable_live(self):
        if self.live_state() == 'HARD_LOCKED':
            return {'accepted': False, 'state': 'HARD_LOCKED', 'code': 'DENY_HARD_LOCKED'}
        now = self.clock()
        with self._db() as db:
            db.execute('INSERT INTO user_live VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,reason=excluded.reason', ('LIVE_OFF', now, 'USER_SWITCH'))
        return {'accepted': True, 'state': 'LIVE_OFF', 'code': 'LIVE_OFF', 'protection': 'RETAINED', 'buy_orders': 0}

    @staticmethod
    def _r2_notional(equity, stop_distance, fee_rate, score='85'):
        try:
            equity = Decimal(str(equity))
            stop = Decimal(str(stop_distance))
            fee = Decimal(str(fee_rate))
            score_dec = Decimal(str(score))
        except Exception:
            return None
        if equity <= 0 or stop < Decimal('0.005') or stop > Decimal('0.050'):
            return None
        if fee < 0 or fee > Decimal('0.01'):
            return None
        loss = stop + fee + fee + Decimal('0.002')
        if loss <= 0:
            return None
        span = min(Decimal('1'), max(Decimal('0'), (score_dec - Decimal('75')) / Decimal('25')))
        score_cap = MIN_ORDER_USDT + span * (MAX_ORDER_USDT - MIN_ORDER_USDT)
        risk_cap = equity * Decimal('0.010') / loss
        if risk_cap < MIN_ORDER_USDT:
            return None
        return min(MAX_ORDER_USDT, score_cap, risk_cap)

    def consider_entry(self, reader, bid, tick, now, allowed_strategies=None, ask=None):
        """Place at most one SOL limit buy from an accepted live strategy signal."""
        if self.live_state() != 'LIVE_ON':
            return {'placed': False, 'buy_orders': 0}
        try:
            if reader is not None and hasattr(reader, 'live_status') and (reader.live_status() or {}).get('state') == 'HARD_LOCKED':
                return {'placed': False, 'reason': 'HARD_LOCKED', 'buy_orders': 0}
        except Exception:
            pass
        bid_dec = Decimal(str(bid))
        open_slots = len(self.open_bot_slots(mark_price=bid_dec if bid_dec > 0 else None))
        if open_slots >= 2:
            return {'placed': False, 'reason': 'SLOTS_FULL', 'buy_orders': 0}
        signal = self._next_signal(reader, now, allowed_strategies=allowed_strategies, mark_price=bid_dec)
        if signal is None:
            return {'placed': False, 'buy_orders': 0}
        ask_dec = Decimal(str(ask)) if ask is not None else bid_dec
        if ask_dec <= 0:
            ask_dec = bid_dec
        entry_high = signal.get('entry_high')
        if signal['strategy_id'] in ('T2', 'R2') and entry_high is not None and entry_high > 0:
            if bid_dec > entry_high:
                return {'placed': False, 'reason': 'CHASE_EXCEEDED', 'buy_orders': 0}
            raw_price = min(entry_high, ask_dec)
        else:
            raw_price = min(signal['limit'], bid_dec)
        tick_dec = Decimal(str(tick))
        price = self._floor(raw_price, tick_dec) if tick_dec > 0 else raw_price
        if price <= 0:
            return {'placed': False, 'reason': 'INVALID_PRICE', 'buy_orders': 0}
        api_key, secret = self._load()
        try:
            self._require_spot(api_key, secret)
            account = self._signed('/api/v3/account', {}, api_key, secret, now)
            free = usdt_total = sol_total = Decimal('0')
            for row in account.get('balances') or []:
                amount = Decimal(str(row.get('free') or '0')) + Decimal(str(row.get('locked') or '0'))
                if row.get('asset') == 'USDT':
                    free = Decimal(str(row.get('free') or '0'))
                    usdt_total = amount
                elif row.get('asset') == 'SOL':
                    sol_total = amount
            if free < MIN_ORDER_USDT or price <= 0:
                return {'placed': False, 'reason': 'USDT_BELOW_500', 'buy_orders': 0}
            ref_equity = max(Decimal('2000'), usdt_total + sol_total * bid_dec)
            if signal['strategy_id'] == 'T2':
                target_notional = risk_notional(ref_equity, signal['stop_distance'], DEFAULT_FEE_RATE, signal['score'])
                if target_notional is None:
                    return {'placed': False, 'reason': 'T2_SIZE', 'buy_orders': 0}
            elif signal['strategy_id'] == 'R2':
                target_notional = self._r2_notional(ref_equity, signal['stop_distance'], DEFAULT_FEE_RATE, signal.get('score') or '85')
                if target_notional is None:
                    return {'placed': False, 'reason': 'R2_SIZE', 'buy_orders': 0}
            else:
                target_notional = signal['notional']
            if free < MAX_ORDER_USDT:
                notional = free
            else:
                notional = min(MAX_ORDER_USDT, max(MIN_ORDER_USDT, target_notional), free)
            if notional < MIN_ORDER_USDT:
                return {'placed': False, 'reason': 'USDT_BELOW_500', 'buy_orders': 0}
            rules = self._get_sol_rules(now)
            qty = self._floor(notional / price, rules['step'])
            step_tolerance = price * rules['step']
            min_notional = max(MIN_ORDER_USDT - step_tolerance, rules.get('min_notional') or Decimal('5'))
            if qty <= 0 or qty < rules['min_qty'] or qty * price < min_notional:
                return {'placed': False, 'reason': 'BELOW_MIN_NOTIONAL', 'buy_orders': 0}
            client_id = 'cbotby' + hashlib.sha256(signal['signal_id'].encode()).hexdigest()[:16]
            placed = self._signed('/api/v3/order', {
                'symbol': 'SOLUSDT', 'side': 'BUY', 'type': 'LIMIT', 'timeInForce': 'GTC',
                'quantity': format(qty, 'f'), 'price': format(price, 'f'), 'newClientOrderId': client_id,
            }, api_key, secret, now, method='POST')
        finally:
            api_key = secret = None
        stop_dist = str(signal.get('stop_distance') or '0.010')
        with self._db() as db:
            db.execute(
                'INSERT OR IGNORE INTO bot_orders(signal_id,client_order_id,exchange_order_id,price,quantity,quote,status,created_at,stop_distance,strategy_id,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    signal['signal_id'], client_id, str(placed.get('orderId') or ''),
                    format(price, 'f'), format(qty, 'f'), format(qty * price, 'f'),
                    'NEW', now, stop_dist, signal['strategy_id'], signal.get('expires_at'),
                ),
            )
        return {'placed': True, 'signal_id': signal['signal_id'], 'strategy_id': signal['strategy_id'], 'quantity': format(qty, 'f'), 'price': format(price, 'f'), 'buy_orders': 1}

    def has_open_bots(self, include_dust=False):
        if not self.db_path.exists():
            return False
        if include_dust:
            with self._db() as db:
                count = db.execute("SELECT COUNT(*) FROM bot_orders WHERE status IN ('NEW','PARTIALLY_FILLED','FILLED','EXIT_PENDING')").fetchone()[0]
            return bool(count)
        return bool(self.open_bot_slots())

    def sync_bot_stops(self, bid, tick, now, evidence=None, micro_features=None):
        """Protect all live bot positions through APTS + atomic cancelReplace, and enforce 24h & 5s exit fallbacks."""
        bid = Decimal(str(bid))
        tick = Decimal(str(tick))
        now = int(now)
        with self._db() as db:
            rows = [tuple(row) for row in db.execute(
                "SELECT signal_id,client_order_id,price,quantity,status,stop_distance,stop_client_id,filled_at,high_price,exit_client_id,stop_price,stop_updated_at,exit_started_at,strategy_id,expires_at "
                "FROM bot_orders WHERE status IN ('NEW','PARTIALLY_FILLED','FILLED','EXIT_PENDING')")]
        if not rows:
            return {'synced': True, 'open': 0}
        try:
            step = self._get_sol_rules(now)['step']
        except Exception:
            step = Decimal('0.001')
        if evidence is None:
            from solbot.mathematics.trend_exhaustion import TrendEvidence
            ratio = Decimal(str((micro_features or {}).get('buy_ratio') or '0.5'))
            evidence = TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'), buy_ratio=ratio, previous_buy_ratio=ratio)
        api_key, secret = self._load()
        actions = []
        try:
            self._require_spot(api_key, secret)
            for row in rows:
                try:
                    actions.append(self._sync_one_bot(api_key, secret, bid, tick, step, now, evidence, micro_features, *row))
                except Exception as exc:
                    actions.append({'signal_id': row[0], 'error': type(exc).__name__})
        finally:
            api_key = secret = None
        return {'synced': True, 'open': len(rows), 'actions': actions}

    def apply_user_stream_event(self, event):
        """Immediately apply a UserStream SOLUSDT executionReport to bot_orders or protections."""
        if not isinstance(event, dict):
            return {'handled': False}
        symbol = str(event.get('symbol') or event.get('s') or '')
        if symbol and symbol != 'SOLUSDT':
            return {'handled': False, 'reason': 'WRONG_SYMBOL'}
        exch_status = str(event.get('status') or event.get('X') or '').upper()
        if not exch_status:
            return {'handled': False}
        raw = event.get('raw') if isinstance(event.get('raw'), dict) else {}
        client_id = str(
            event.get('orig_client_order_id')
            or (
                (event.get('C') or raw.get('C'))
                if exch_status in ('CANCELED', 'EXPIRED', 'REJECTED') and (event.get('C') or raw.get('C'))
                else ''
            )
            or event.get('client_order_id')
            or event.get('c')
            or ''
        )
        if not client_id or not client_id.startswith(BOT_ORDER_PREFIXES):
            return {'handled': False, 'reason': 'NON_BOT_ORDER'}
        event_time = int(
            event.get('event_time_ms')
            or event.get('transaction_time')
            or event.get('event_time')
            or event.get('T')
            or event.get('E')
            or self.clock()
        )
        executed = Decimal(str(event.get('cumulative_quantity') or event.get('executed_qty') or event.get('z') or '0'))
        quote_qty = Decimal(str(event.get('cumulative_quote') or event.get('cummulative_quote_qty') or event.get('Z') or '0'))
        fill_px = self._fill_price({
            'price': event.get('price') or event.get('last_price') or event.get('L') or event.get('p') or '0',
            'executedQty': format(executed, 'f'),
            'cummulativeQuoteQty': format(quote_qty, 'f'),
        })
        with self._db() as db:
            prot = db.execute(
                "SELECT intent_id,status FROM protections WHERE client_order_id=? AND status IN ('OPEN','EXITING')",
                (client_id,),
            ).fetchone()
            if prot:
                intent_id, prot_status = prot
                if exch_status == 'FILLED':
                    db.execute("UPDATE protections SET status='CLOSED',updated_at=? WHERE intent_id=?", (event_time, intent_id))
                    return {'handled': True, 'target': 'protections', 'intent_id': intent_id, 'status': 'CLOSED'}
                if exch_status in ('CANCELED', 'EXPIRED', 'REJECTED') and prot_status != 'EXITING':
                    db.execute("UPDATE protections SET status='CANCELLED_ON_EXCHANGE',updated_at=? WHERE intent_id=?", (event_time, intent_id))
                    return {'handled': True, 'target': 'protections', 'intent_id': intent_id, 'status': 'CANCELLED_ON_EXCHANGE'}
                return {'handled': True, 'target': 'protections', 'intent_id': intent_id, 'status': prot_status}

            row = db.execute(
                "SELECT signal_id,client_order_id,stop_client_id,exit_client_id,price,quantity,filled_at,status,stop_price "
                "FROM bot_orders WHERE client_order_id=? OR stop_client_id=? OR exit_client_id=?",
                (client_id, client_id, client_id),
            ).fetchone()
            if row is None:
                return {'handled': False, 'reason': 'UNKNOWN_ORDER'}
            signal_id, entry_cid, stop_cid, exit_cid, cur_px, cur_qty, cur_filled_at, cur_status, cur_stop_px = row
            if client_id == entry_cid:
                new_px = fill_px if Decimal(fill_px) > 0 else cur_px
                new_qty = format(executed, 'f') if executed > 0 else cur_qty
                if exch_status == 'FILLED' or (exch_status in ('CANCELED', 'EXPIRED', 'REJECTED') and executed > 0):
                    new_status = 'FILLED'
                    new_filled_at = cur_filled_at or event_time
                else:
                    new_status = exch_status
                    new_filled_at = cur_filled_at
                db.execute(
                    'UPDATE bot_orders SET status=?,price=?,quantity=?,filled_at=? WHERE signal_id=?',
                    (new_status, new_px, new_qty, new_filled_at, signal_id),
                )
                return {'handled': True, 'target': 'bot_entry', 'signal_id': signal_id, 'status': new_status}
            if client_id == stop_cid:
                if exch_status == 'FILLED':
                    exit_px = fill_px if Decimal(fill_px) > 0 else (cur_stop_px or cur_px)
                    self._record_bot_close(db, signal_id, cur_px, executed if executed > 0 else cur_qty, exit_px, event_time, exit_client_id=client_id)
                    return {'handled': True, 'target': 'bot_stop', 'signal_id': signal_id, 'status': 'CLOSED'}
                if exch_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                    db.execute("UPDATE bot_orders SET stop_client_id='',stop_price='' WHERE signal_id=?", (signal_id,))
                    return {'handled': True, 'target': 'bot_stop', 'signal_id': signal_id, 'status': exch_status}
            if client_id == exit_cid and exch_status == 'FILLED':
                exit_px = fill_px if Decimal(fill_px) > 0 else cur_px
                self._record_bot_close(db, signal_id, cur_px, executed if executed > 0 else cur_qty, exit_px, event_time, exit_client_id=client_id)
                return {'handled': True, 'target': 'bot_exit', 'signal_id': signal_id, 'status': 'CLOSED'}
            return {'handled': True, 'target': 'bot_orders', 'signal_id': signal_id, 'status': cur_status}

    @staticmethod
    def _record_bot_close(db, signal_id, entry_px, qty_raw, exit_px, closed_at, exit_client_id=None):
        try:
            entry = Decimal(str(entry_px or '0'))
            qty = Decimal(str(qty_raw or '0'))
            exit_d = Decimal(str(exit_px or '0'))
            pnl = (exit_d - entry) * qty if (entry > 0 and qty > 0 and exit_d > 0) else Decimal('0')
        except Exception:
            exit_d = Decimal('0')
            pnl = Decimal('0')
        exit_str = format(exit_d, 'f') if exit_d > 0 else str(exit_px or '')
        if exit_client_id is not None:
            db.execute(
                "UPDATE bot_orders SET status='CLOSED', exit_price=?, closed_at=?, realized_pnl=?, exit_client_id=? WHERE signal_id=?",
                (exit_str, int(closed_at), format(pnl, 'f'), exit_client_id, signal_id),
            )
        else:
            db.execute(
                "UPDATE bot_orders SET status='CLOSED', exit_price=?, closed_at=?, realized_pnl=? WHERE signal_id=?",
                (exit_str, int(closed_at), format(pnl, 'f'), signal_id),
            )

    def _close_dust_bot_order(self, api_key, secret, signal_id, quantity, entry_price, prev_client_id, step, bid_dec, now):
        """Attempt market close for a < 5 USDT SOL residual bot order and always mark it CLOSED to free slots."""
        mkt_cid = 'cbot2x' + hashlib.sha256(f'{signal_id}:dust:{now}'.encode()).hexdigest()[:16]
        exit_px = bid_dec
        final_cid = prev_client_id or mkt_cid
        if prev_client_id:
            try:
                self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': prev_client_id}, api_key, secret, now, method='DELETE')
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    pass
        try:
            res = self._market_exit_order(
                api_key, secret, prev_client_id=None, exit_client_id=mkt_cid,
                desired_qty=quantity, step=step, now=now,
            )
            placed = res.get('placed') if isinstance(res.get('placed'), dict) else None
            if placed:
                fp = self._fill_price(placed)
                if Decimal(fp) > 0:
                    exit_px = Decimal(fp)
            final_cid = res.get('client_id') or mkt_cid
        except ValueError:
            pass
        with self._db() as db:
            self._record_bot_close(db, signal_id, entry_price, quantity, exit_px, now, exit_client_id=final_cid)
        return {'signal_id': signal_id, 'status': 'CLOSED', 'reason': 'DUST_BELOW_5_USDT'}

    def _sync_one_bot(self, api_key, secret, bid, tick, step, now, evidence, micro_features,
                      signal_id, client_id, price, qty, status, stop_distance, stop_client,
                      filled_at, high_price, exit_client, stored_stop_price, stop_updated_at, exit_started_at,
                      strategy_id='T1', expires_at=None):
        bid_dec, tick_dec = Decimal(str(bid)), Decimal(str(tick))
        strategy_id = strategy_id or 'T1'
        if status in ('NEW', 'PARTIALLY_FILLED'):
            found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': client_id}, api_key, secret, now)
            exch_status = str(found.get('status') or status)
            executed = Decimal(str(found.get('executedQty') or '0'))
            fill_px = self._fill_price(found)
            if Decimal(fill_px) > 0:
                price = fill_px
            if exch_status == 'FILLED' or (exch_status in ('CANCELED', 'EXPIRED', 'REJECTED') and executed > 0):
                status = 'FILLED'
                filled_at = int(found.get('updateTime') or found.get('time') or now)
                qty = format(executed, 'f') if executed > 0 else qty
            elif exch_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                with self._db() as db:
                    db.execute('UPDATE bot_orders SET status=? WHERE signal_id=?', (exch_status, signal_id))
                return {'signal_id': signal_id, 'status': exch_status}
            elif exch_status in ('NEW', 'PARTIALLY_FILLED') and expires_at is not None and now >= int(expires_at):
                try:
                    cancelled = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': client_id}, api_key, secret, now, method='DELETE')
                    cancel_exec = Decimal(str(cancelled.get('executedQty') or executed))
                    cancel_px = self._fill_price(cancelled)
                except ValueError as exc:
                    if str(exc) != 'ORDER_GONE':
                        raise
                    refound = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': client_id}, api_key, secret, now)
                    cancel_exec = Decimal(str(refound.get('executedQty') or executed))
                    cancel_px = self._fill_price(refound)
                if cancel_exec > 0:
                    status = 'FILLED'
                    filled_at = now
                    qty = format(cancel_exec, 'f')
                    if Decimal(cancel_px) > 0:
                        price = cancel_px
                else:
                    with self._db() as db:
                        db.execute("UPDATE bot_orders SET status='EXPIRED' WHERE signal_id=?", (signal_id,))
                    return {'signal_id': signal_id, 'status': 'EXPIRED'}
            else:
                status = exch_status
            with self._db() as db:
                db.execute('UPDATE bot_orders SET status=?,price=?,quantity=?,filled_at=? WHERE signal_id=?', (status, price, qty, filled_at, signal_id))
        if status == 'EXIT_PENDING':
            if bid_dec > 0 and self._is_dust_order(qty, price, bid_dec, status):
                return self._close_dust_bot_order(api_key, secret, signal_id, Decimal(str(qty)), price, exit_client or stop_client, step, bid_dec, now)
            return self._escalate_bot_exit(api_key, secret, signal_id, price, qty, exit_client, exit_started_at, step, bid_dec, now)
        if status != 'FILLED':
            return {'signal_id': signal_id, 'status': status}
        entry = Decimal(str(price))
        quantity = Decimal(str(qty))
        distance = Decimal(str(stop_distance or '0.010'))
        if distance < Decimal('0.005') or distance > Decimal('0.050') or entry <= 0 or quantity <= 0:
            return {'signal_id': signal_id, 'status': 'IGNORED'}
        if bid_dec > 0 and self._is_dust_order(quantity, entry, bid_dec, status):
            return self._close_dust_bot_order(api_key, secret, signal_id, quantity, entry, stop_client or exit_client, step, bid_dec, now)
        high = max(Decimal(str(high_price or entry)), bid_dec)
        with self._db() as db:
            db.execute('UPDATE bot_orders SET high_price=? WHERE signal_id=?', (format(high, 'f'), signal_id))

        current_stop = Decimal(str(stored_stop_price or '0'))
        if stop_client:
            found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': stop_client}, api_key, secret, now)
            stop_status = str(found.get('status') or '')
            if stop_status == 'FILLED':
                stop_fill_px = self._fill_price(found)
                exit_px = stop_fill_px if Decimal(stop_fill_px) > 0 else (stored_stop_price or format(bid_dec, 'f'))
                with self._db() as db:
                    self._record_bot_close(db, signal_id, entry, quantity, exit_px, now, exit_client_id=stop_client)
                return {'signal_id': signal_id, 'status': 'CLOSED'}
            if stop_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                stop_client = ''
                current_stop = Decimal('0')
            else:
                found_stop = Decimal(str(found.get('stopPrice') or '0'))
                if found_stop > 0:
                    current_stop = found_stop

        decision, stage, prev_stage = self._advance_apts_step(
            f'bot:{signal_id}', entry=entry, qty=quantity, current_stop=current_stop,
            current_stage='S0', bid=bid_dec, evidence=evidence, tick=tick_dec, now=now,
            micro_features=micro_features, strategy_id=strategy_id, stop_distance=distance,
            entry_time_ms=int(filled_at or now), reversal_confirm_ticks=2,
        )
        if decision.exit_required:
            return self._place_bot_exit_limit(api_key, secret, signal_id, entry, stop_client, decision, tick_dec, step, bid_dec, now)

        target_stop = decision.stop_price
        if target_stop <= 0 or target_stop >= entry * Decimal('2'):
            return {'signal_id': signal_id, 'status': 'STOP_REJECTED'}
        if not stop_client:
            return self._update_bot_stop(api_key, secret, signal_id, entry, None, quantity, target_stop, tick_dec, step, bid_dec, now, stage, 'STOP_PLACED')
        if self._should_replace_stop(current_stop, target_stop, quantity, decision.quantity, stop_updated_at, now, stage != prev_stage):
            return self._update_bot_stop(api_key, secret, signal_id, entry, stop_client, decision.quantity, target_stop, tick_dec, step, bid_dec, now, stage, 'TRAIL_RAISED')
        return {'signal_id': signal_id, 'status': 'STOP_RESTING', 'stage': stage, 'stop': format(current_stop or target_stop, 'f')}

    def _update_bot_stop(self, api_key, secret, signal_id, entry_price, prev_stop_client, quantity, stop_price, tick, step, bid_dec, now, stage, label):
        from solbot.mathematics.protective_stop import stop_limit_price
        new_cid = 'cbot2s' + hashlib.sha256(f'{signal_id}:{stop_price}:{now}'.encode()).hexdigest()[:16]
        limit = stop_limit_price(stop_price, tick)
        if quantity * stop_price < DUST_NOTIONAL_USDT:
            return self._close_dust_bot_order(api_key, secret, signal_id, quantity, entry_price, prev_stop_client, step, bid_dec, now)
        res = self._atomic_replace_or_place_stop(
            api_key, secret, prev_client_id=prev_stop_client, new_client_id=new_cid,
            quantity=quantity, stop_price=stop_price, limit_price=limit,
            order_type='STOP_LOSS_LIMIT', step=step, now=now,
        )
        if res.get('closed_by_prev_stop') or res.get('zero_balance'):
            prev_order = res.get('order') if isinstance(res.get('order'), dict) else {}
            prev_px = self._fill_price(prev_order) if prev_order else '0'
            exit_px = prev_px if Decimal(prev_px) > 0 else format(bid_dec, 'f')
            with self._db() as db:
                self._record_bot_close(db, signal_id, entry_price, quantity, exit_px, now, exit_client_id=prev_stop_client)
            return {'signal_id': signal_id, 'status': 'CLOSED'}
        final_qty = res['quantity']
        if final_qty * stop_price < DUST_NOTIONAL_USDT:
            return self._close_dust_bot_order(api_key, secret, signal_id, final_qty, entry_price, res['client_id'], step, bid_dec, now)
        with self._db() as db:
            db.execute('UPDATE bot_orders SET stop_client_id=?,stop_price=?,stop_updated_at=?,quantity=? WHERE signal_id=?',
                       (res['client_id'], format(stop_price, 'f'), now, format(final_qty, 'f'), signal_id))
        return {'signal_id': signal_id, 'status': label, 'stage': stage, 'stop': format(stop_price, 'f')}

    def _place_bot_exit_limit(self, api_key, secret, signal_id, entry_price, stop_client, decision, tick, step, bid_dec, now):
        exit_cid = 'cbot2x' + hashlib.sha256(f'{signal_id}:exit:{now}'.encode()).hexdigest()[:16]
        price = floor_step(decision.limit_price, tick)
        if decision.quantity * price < DUST_NOTIONAL_USDT:
            return self._close_dust_bot_order(api_key, secret, signal_id, decision.quantity, entry_price, stop_client, step, bid_dec, now)
        res = self._atomic_replace_or_place_stop(
            api_key, secret, prev_client_id=stop_client or None, new_client_id=exit_cid,
            quantity=decision.quantity, stop_price=price, limit_price=price,
            order_type='LIMIT', step=step, now=now,
        )
        if res.get('closed_by_prev_stop') or res.get('zero_balance') or str((res.get('placed') or {}).get('status') or '') == 'FILLED':
            placed = res.get('placed') if isinstance(res.get('placed'), dict) else {}
            prev_order = res.get('order') if isinstance(res.get('order'), dict) else {}
            fp = self._fill_price(placed or prev_order) if (placed or prev_order) else '0'
            exit_px = fp if Decimal(fp) > 0 else format(price, 'f')
            with self._db() as db:
                self._record_bot_close(db, signal_id, entry_price, decision.quantity, exit_px, now, exit_client_id=res.get('client_id') or exit_cid)
            return {'signal_id': signal_id, 'status': 'CLOSED'}
        with self._db() as db:
            db.execute("UPDATE bot_orders SET status='EXIT_PENDING', exit_client_id=?, exit_started_at=?, quantity=? WHERE signal_id=?",
                       (res['client_id'], now, format(res['quantity'], 'f'), signal_id))
        return {'signal_id': signal_id, 'status': 'EXIT_PENDING'}

    def _escalate_bot_exit(self, api_key, secret, signal_id, entry_price, qty, exit_client, exit_started_at, step, bid_dec, now):
        desired = Decimal(str(qty))
        if bid_dec > 0 and desired * bid_dec < DUST_NOTIONAL_USDT:
            return self._close_dust_bot_order(api_key, secret, signal_id, desired, entry_price, exit_client, step, bid_dec, now)
        exch_status = ''
        if exit_client:
            try:
                found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': exit_client}, api_key, secret, now)
                exch_status = str(found.get('status') or '')
                if exch_status == 'FILLED':
                    fp = self._fill_price(found)
                    exit_px = fp if Decimal(fp) > 0 else format(bid_dec, 'f')
                    with self._db() as db:
                        self._record_bot_close(db, signal_id, entry_price, desired, exit_px, now, exit_client_id=exit_client)
                    return {'signal_id': signal_id, 'status': 'CLOSED'}
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    raise
                exch_status = 'CANCELED'
        if exit_started_at and now - int(exit_started_at) < EXIT_PENDING_TIMEOUT_MS and exch_status not in ('CANCELED', 'EXPIRED', 'REJECTED'):
            return {'signal_id': signal_id, 'status': 'EXIT_PENDING'}
        mkt_cid = 'cbot2x' + hashlib.sha256(f'{signal_id}:mkt:{now}'.encode()).hexdigest()[:16]
        res = self._market_exit_order(api_key, secret, prev_client_id=exit_client, exit_client_id=mkt_cid, desired_qty=desired, step=step, now=now)
        closed = res.get('status') == 'FILLED' or res.get('closed_by_prev_stop') or res.get('already_closed')
        if closed:
            placed = res.get('placed') if isinstance(res.get('placed'), dict) else {}
            prev_order = res.get('order') if isinstance(res.get('order'), dict) else {}
            fp = self._fill_price(placed or prev_order) if (placed or prev_order) else '0'
            exit_px = fp if Decimal(fp) > 0 else format(bid_dec, 'f')
            with self._db() as db:
                self._record_bot_close(db, signal_id, entry_price, desired, exit_px, now, exit_client_id=res.get('client_id') or mkt_cid)
        else:
            with self._db() as db:
                db.execute("UPDATE bot_orders SET exit_client_id=?, exit_started_at=?, status='EXIT_PENDING' WHERE signal_id=?",
                           (res.get('client_id') or mkt_cid, now, signal_id))
        return {'signal_id': signal_id, 'status': 'CLOSED' if closed else 'EXIT_ESCALATED'}

    def _exit_bot_quantity(self, api_key, secret, signal_id, entry_price, quantity, step, bid_dec, now, stop_client):
        client_id = 'cbot2x' + hashlib.sha256(f'{signal_id}:time:{now}'.encode()).hexdigest()[:16]
        res = self._market_exit_order(api_key, secret, prev_client_id=stop_client, exit_client_id=client_id, desired_qty=quantity, step=step, now=now)
        closed = res.get('status') == 'FILLED' or res.get('closed_by_prev_stop') or res.get('already_closed')
        if closed:
            placed = res.get('placed') if isinstance(res.get('placed'), dict) else {}
            prev_order = res.get('order') if isinstance(res.get('order'), dict) else {}
            fp = self._fill_price(placed or prev_order) if (placed or prev_order) else '0'
            exit_px = fp if Decimal(fp) > 0 else format(bid_dec, 'f')
            with self._db() as db:
                self._record_bot_close(db, signal_id, entry_price, quantity, exit_px, now, exit_client_id=res.get('client_id') or client_id)
        else:
            with self._db() as db:
                db.execute("UPDATE bot_orders SET exit_client_id=?, exit_started_at=?, status='EXIT_PENDING' WHERE signal_id=?",
                           (res.get('client_id') or client_id, now, signal_id))
        return {'closed': closed}

    def _require_spot(self, api_key, secret):
        clock = self._server_time()
        data = self._signed('/sapi/v1/account/apiRestrictions', {}, api_key, secret, clock)
        if data.get('enableWithdrawals') is True or data.get('enableInternalTransfer') is True or data.get('permitsUniversalTransfer') is True:
            raise ValueError('WITHDRAWALS_ENABLED')
        if data.get('enableReading') is not True:
            raise ValueError('READ_PERMISSION_REQUIRED')
        if data.get('enableSpotAndMarginTrading') is not True:
            raise ValueError('TRADE_PERMISSION_REQUIRED')

    @staticmethod
    def _parse_signal_payload(payload, used, allowed, now):
        if not isinstance(payload, dict):
            return None
        strategy_id = payload.get('strategy_id')
        if payload.get('symbol') != 'SOLUSDT' or strategy_id not in allowed or not payload.get('accepted'):
            return None
        signal_id = str(payload.get('signal_id') or '')
        if not signal_id or signal_id in used:
            return None
        expires = int(payload.get('expires_at') or 0)
        if expires and expires < now:
            return None
        try:
            limit = Decimal(str(payload.get('entry_low') or '0'))
            entry_high = Decimal(str(payload.get('entry_high') or '0'))
            score = Decimal(str(payload.get('score') or '75'))
        except Exception:
            return None
        if limit <= 0:
            return None
        span = min(Decimal('1'), max(Decimal('0'), (score - Decimal('75')) / Decimal('25')))
        notional = MIN_ORDER_USDT + span * (MAX_ORDER_USDT - MIN_ORDER_USDT)
        default_stop = '0.010' if strategy_id == 'T1' else ('0.012' if strategy_id == 'R2' else None)
        stop_dist = payload.get('stop_distance') or default_stop
        return {
            'signal_id': signal_id, 'limit': limit, 'entry_high': entry_high if entry_high > 0 else None,
            'notional': notional, 'score': score, 'strategy_id': strategy_id,
            'stop_distance': stop_dist, 'expires_at': expires or None,
            'signal_time': int(payload.get('signal_time') or 0),
        }

    def _next_signal(self, reader, now, allowed_strategies=None, mark_price=None):
        if allowed_strategies is not None:
            allowed = tuple(allowed_strategies)
        else:
            allowed = tuple(
                item.strip() for item in os.environ.get('SOLBOT_LIVE_STRATEGIES', '').split(',') if item.strip()
            ) or DEFAULT_LIVE_STRATEGIES
        mark_dec = Decimal(str(mark_price)) if mark_price is not None and Decimal(str(mark_price)) > 0 else self._current_mark_dec()
        with self._db() as db:
            used = {row[0] for row in db.execute('SELECT signal_id FROM bot_orders')}
            recent_active = [
                int(row[0] or 0)
                for row in db.execute(
                    "SELECT created_at,quantity,price,status FROM bot_orders WHERE status IN ('NEW','PARTIALLY_FILLED','FILLED','EXIT_PENDING')"
                )
                if not self._is_dust_order(row[1], row[2], mark_dec, row[3])
            ]
        if any(0 <= now - ts < CROSS_STRATEGY_COOLDOWN_MS for ts in recent_active if ts > 0):
            return None
        candidates = []
        try:
            rows = reader.read('SOLUSDT_strategy', 'SELECT payload_json FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 30', ('SOLUSDT',))
        except Exception:
            rows = []
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
            except Exception:
                continue
            parsed = self._parse_signal_payload(payload, used, allowed, now)
            if parsed is not None:
                candidates.append(parsed)
        if 'R2' in allowed:
            try:
                if hasattr(reader, '_read_r2'):
                    r2_rows = reader._read_r2('r2_state', 'SELECT payload FROM r2_outbox ORDER BY rowid DESC LIMIT 10') or reader.read('r2_state', 'SELECT payload FROM r2_outbox ORDER BY rowid DESC LIMIT 10')
                else:
                    r2_rows = reader.read('r2_state', 'SELECT payload FROM r2_outbox ORDER BY rowid DESC LIMIT 10')
            except Exception:
                r2_rows = []
            for row in r2_rows:
                raw = row.get('payload') if isinstance(row, dict) else None
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                parsed = self._parse_signal_payload(payload, used, allowed, now)
                if parsed is not None:
                    candidates.append(parsed)
        if not candidates:
            return None
        priority_rank = {'T1': 2, 'T2': 1, 'R2': 0}
        picked = max(
            candidates,
            key=lambda item: (
                item['signal_time'],
                priority_rank.get(item['strategy_id'], 0),
                item['score'],
            ),
        )
        if picked['signal_time'] > 0 and any(
            abs(picked['signal_time'] - ts) < CROSS_STRATEGY_COOLDOWN_MS for ts in recent_active if ts > 0
        ):
            return None
        return picked

    def protect_manual(self, quantity):
        api_key, secret = self._load()
        try:
            return self._protect(api_key, secret, quantity)
        finally:
            api_key = secret = None

    def cancel_manual(self):
        current = self.protection()
        if current is None:
            return {'cancelled': False, 'buy_orders': 0}
        api_key, secret = self._load()
        try:
            clock = self._server_time()
            try:
                self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': current['client_order_id']}, api_key, secret, clock, method='DELETE')
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    raise
        finally:
            api_key = secret = None
        with self._db() as db:
            db.execute('UPDATE protections SET status=?,updated_at=? WHERE client_order_id=?', ('CANCELLED', self.clock(), current['client_order_id']))
            db.execute('DELETE FROM custody_apts WHERE intent_id=?', ('manual:SOL',))
        return {'cancelled': True, 'stop_order_id': current['exchange_order_id'], 'buy_orders': 0}

    def advance(self, bid, evidence, tick, now, micro_features=None):
        """Move one custodied SOL position through the shared APTS ladder with atomic cancelReplace & exit fallback."""
        current = self.protection()
        if current is None:
            return {'advanced': False}
        held = self._held_sol()
        if held <= 0:
            with self._db() as db:
                db.execute('UPDATE protections SET status=?,updated_at=? WHERE intent_id=?', ('CLOSED', now, current['intent_id']))
            return {'advanced': True, 'stage': 'CLOSED', 'buy_orders': 0}
        if current['status'] == 'EXITING':
            return self._escalate_manual_exit(current, held, now)
        bid, tick = Decimal(str(bid)), Decimal(str(tick))
        self._remember_mark(bid, now)
        entry, qty = Decimal(current['entry_price']), min(Decimal(current['quantity']), held)
        if bid > 0 and qty * bid < DUST_NOTIONAL_USDT:
            api_key, secret = self._load()
            try:
                try:
                    step = self._get_sol_rules(now)['step']
                except Exception:
                    step = Decimal('0.001')
                mkt_cid = 'cbotex' + hashlib.sha256(f'manual:SOL:dust:{now}'.encode()).hexdigest()[:16]
                try:
                    self._market_exit_order(api_key, secret, prev_client_id=current['client_order_id'], exit_client_id=mkt_cid, desired_qty=qty, step=step, now=now)
                except ValueError:
                    pass
            finally:
                api_key = secret = None
            with self._db() as db:
                db.execute('UPDATE protections SET status=?,updated_at=? WHERE intent_id=?', ('CLOSED', now, current['intent_id']))
            return {'advanced': True, 'stage': 'CLOSED', 'reason': 'DUST_BELOW_5_USDT', 'buy_orders': 0}
        current_stop = Decimal(current['stop_price'])
        decision, stage, prev_stage = self._advance_apts_step(
            'manual:SOL', entry=entry, qty=qty, current_stop=current_stop,
            current_stage=current.get('stage') or 'S0', bid=bid, evidence=evidence,
            tick=tick, now=now, micro_features=micro_features, strategy_id='T1',
            stop_distance=Decimal('0.008'),
            entry_time_ms=int(current.get('placed_at') or 0), reversal_confirm_ticks=2,
        )
        if decision.exit_required:
            self._exit_position(decision, current, tick, now)
        elif self._should_replace_stop(current_stop, decision.stop_price, Decimal(current['quantity']), decision.quantity, current.get('updated_at'), now, stage != prev_stage):
            self._raise_stop(decision, current, tick, now)
        view = self.protection()
        return {'advanced': True, 'stage': view['stage'] if view else 'CLOSED',
                'stop_price': view.get('stop_price', format(decision.stop_price, 'f')) if view else format(decision.stop_price, 'f'),
                'exit_required': decision.exit_required, 'buy_orders': 0}

    def _get_sol_rules(self, now=None):
        now_ms = int(now if now is not None else self.clock())
        if self._rules_cache is not None and now_ms < self._rules_cache[0]:
            return self._rules_cache[1]
        info = self.transport.request('/api/v3/exchangeInfo', 'symbol=SOLUSDT', {})
        rules = self._sol_rules(info)
        self._rules_cache = (now_ms + 60_000, rules)
        return rules

    @staticmethod
    def _should_replace_stop(current_stop, new_stop, current_qty, new_qty, last_updated_at, now, stage_changed):
        if new_stop <= 0 or new_qty <= 0:
            return False
        if current_stop <= 0 or new_qty < current_qty:
            return True
        if new_stop <= current_stop:
            return False
        if stage_changed:
            return True
        if (new_stop - current_stop) / current_stop >= STOP_REPLACE_MIN_STEP_PCT:
            return True
        if last_updated_at is not None and now - int(last_updated_at) >= STOP_REPLACE_MIN_INTERVAL_MS:
            return True
        return False

    @staticmethod
    def _extract_placed_order(response, fallback_client_id):
        if not isinstance(response, dict):
            return {'clientOrderId': fallback_client_id, 'orderId': ''}
        if 'newOrderResponse' in response:
            if response.get('cancelResult') == 'FAILURE':
                raise ValueError('ORDER_GONE')
            if response.get('newOrderResult') == 'FAILURE':
                code = (response.get('newOrderResponse') or {}).get('code') if isinstance(response.get('newOrderResponse'), dict) else None
                raise ValueError('INSUFFICIENT_FREE_SOL' if code == -2010 else 'PROTECTION_REJECTED')
            inner = response.get('newOrderResponse') or {}
            if isinstance(inner, dict):
                return inner
        return response

    def _sellable_sol_qty(self, api_key, secret, prev_client_id, desired_qty, step, now):
        account = self._signed('/api/v3/account', {}, api_key, secret, now)
        free_sol = Decimal('0')
        for row in account.get('balances') or []:
            if row.get('asset') == 'SOL':
                free_sol = Decimal(str(row.get('free') or '0'))
        locked_prev = Decimal('0')
        prev_active = False
        prev_order = None
        if prev_client_id:
            try:
                found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': prev_client_id}, api_key, secret, now)
                prev_order = found
                status = str(found.get('status') or '')
                if status == 'FILLED':
                    return Decimal('0'), {'closed_by_prev_stop': True, 'prev_active': False, 'order': found}
                if status in ('NEW', 'PARTIALLY_FILLED'):
                    prev_active = True
                    orig = Decimal(str(found.get('origQty') or '0'))
                    executed = Decimal(str(found.get('executedQty') or '0'))
                    locked_prev = max(Decimal('0'), orig - executed)
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    raise
        available = min(desired_qty, free_sol + locked_prev)
        clamped = self._floor(available, step) if step > 0 else available
        return clamped, {'closed_by_prev_stop': False, 'prev_active': prev_active, 'order': prev_order}

    def _atomic_replace_or_place_stop(self, api_key, secret, *, prev_client_id, new_client_id,
                                      quantity, stop_price, limit_price, order_type, step, now):
        def build_params(qty, cancel_cid=None):
            params = {
                'symbol': 'SOLUSDT', 'side': 'SELL', 'type': order_type,
                'quantity': format(qty, 'f'), 'newClientOrderId': new_client_id,
            }
            if order_type in STOP_TYPES:
                params['stopPrice'] = format(stop_price, 'f')
            if order_type in ('STOP_LOSS_LIMIT', 'LIMIT'):
                params['price'] = format(limit_price, 'f')
                params['timeInForce'] = 'GTC'
            if cancel_cid:
                params['cancelReplaceMode'] = 'STOP_ON_FAILURE'
                params['cancelOrigClientOrderId'] = cancel_cid
            return params

        def send(qty, cancel_cid):
            if cancel_cid:
                resp = self._signed('/api/v3/order/cancelReplace', build_params(qty, cancel_cid), api_key, secret, now, method='POST')
            else:
                resp = self._signed('/api/v3/order', build_params(qty), api_key, secret, now, method='POST')
            return self._extract_placed_order(resp, new_client_id)

        used_qty = self._floor(quantity, step) if step > 0 else quantity
        if used_qty <= 0:
            return {'zero_balance': True, 'quantity': Decimal('0'), 'client_id': new_client_id}
        cancel_cid = prev_client_id or None
        for attempt in range(2):
            try:
                placed = send(used_qty, cancel_cid)
                return {
                    'placed': placed,
                    'order_id': str(placed.get('orderId') or ''),
                    'client_id': str(placed.get('clientOrderId') or new_client_id),
                    'quantity': used_qty,
                    'closed_by_prev_stop': False,
                }
            except ValueError as exc:
                reason = str(exc)
                if reason == 'ORDER_GONE' and cancel_cid:
                    clamped, meta = self._sellable_sol_qty(api_key, secret, cancel_cid, used_qty, step, now)
                    if meta.get('closed_by_prev_stop'):
                        return {'closed_by_prev_stop': True, 'order': meta.get('order')}
                    cancel_cid = None
                    if clamped > 0:
                        used_qty = clamped
                        continue
                    return {'zero_balance': True, 'quantity': Decimal('0'), 'client_id': new_client_id}
                if reason == 'INSUFFICIENT_FREE_SOL' and attempt == 0:
                    clamped, meta = self._sellable_sol_qty(api_key, secret, cancel_cid, used_qty, step, now)
                    if meta.get('closed_by_prev_stop'):
                        return {'closed_by_prev_stop': True, 'order': meta.get('order')}
                    cancel_cid = cancel_cid if meta.get('prev_active') else None
                    if clamped <= 0:
                        return {'zero_balance': True, 'quantity': Decimal('0'), 'client_id': new_client_id}
                    used_qty = clamped
                    continue
                raise

    def _market_exit_order(self, api_key, secret, *, prev_client_id, exit_client_id, desired_qty, step, now):
        already_executed = Decimal('0')
        if prev_client_id:
            try:
                found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': prev_client_id}, api_key, secret, now)
                status = str(found.get('status') or '')
                if status == 'FILLED':
                    return {'closed_by_prev_stop': True, 'order': found, 'status': 'FILLED', 'client_id': prev_client_id}
                already_executed = max(Decimal('0'), Decimal(str(found.get('executedQty') or '0')))
                if status in ('NEW', 'PARTIALLY_FILLED'):
                    try:
                        cancelled = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': prev_client_id}, api_key, secret, now, method='DELETE')
                        if isinstance(cancelled, dict) and cancelled.get('executedQty') is not None:
                            already_executed = max(already_executed, Decimal(str(cancelled.get('executedQty') or '0')))
                    except ValueError as exc:
                        if str(exc) != 'ORDER_GONE':
                            raise
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    raise
        remaining_desired = max(Decimal('0'), desired_qty - already_executed)
        if remaining_desired <= 0:
            return {'status': 'FILLED', 'executedQty': format(already_executed, 'f'), 'already_closed': True, 'client_id': prev_client_id or exit_client_id}
        account = self._signed('/api/v3/account', {}, api_key, secret, now)
        free_sol = Decimal('0')
        for row in account.get('balances') or []:
            if row.get('asset') == 'SOL':
                free_sol = Decimal(str(row.get('free') or '0'))
        qty = self._floor(min(remaining_desired, free_sol), step) if step > 0 else min(remaining_desired, free_sol)
        if qty <= 0:
            return {'status': 'FILLED', 'executedQty': '0', 'already_closed': True, 'client_id': exit_client_id}
        placed = self._signed('/api/v3/order', {
            'symbol': 'SOLUSDT', 'side': 'SELL', 'type': 'MARKET',
            'quantity': format(qty, 'f'), 'newClientOrderId': exit_client_id,
        }, api_key, secret, now, method='POST')
        return {
            'placed': placed,
            'status': str(placed.get('status') or ''),
            'order_id': str(placed.get('orderId') or ''),
            'client_id': str(placed.get('clientOrderId') or exit_client_id),
            'quantity': qty,
        }

    def _escalate_manual_exit(self, current, held, now):
        api_key, secret = self._load()
        try:
            self._require_spot(api_key, secret)
            exch_status = ''
            try:
                found = self._signed('/api/v3/order', {'symbol': 'SOLUSDT', 'origClientOrderId': current['client_order_id']}, api_key, secret, now)
                exch_status = str(found.get('status') or '')
                if exch_status == 'FILLED':
                    with self._db() as db:
                        db.execute('UPDATE protections SET status=?,updated_at=? WHERE intent_id=?', ('CLOSED', now, 'manual:SOL'))
                    return {'advanced': True, 'stage': 'CLOSED', 'buy_orders': 0}
            except ValueError as exc:
                if str(exc) != 'ORDER_GONE':
                    raise
                exch_status = 'CANCELED'
            updated_at = current.get('updated_at') or 0
            if now - int(updated_at) < EXIT_PENDING_TIMEOUT_MS and exch_status not in ('CANCELED', 'EXPIRED', 'REJECTED'):
                return {'advanced': False, 'stage': current.get('stage') or 'S3', 'buy_orders': 0}
            try:
                step = self._get_sol_rules(now)['step']
            except Exception:
                step = Decimal('0.001')
            mkt_cid = 'cbotex' + hashlib.sha256(f'manual:SOL:mkt:{now}'.encode()).hexdigest()[:16]
            desired = min(Decimal(current['quantity']), held)
            res = self._market_exit_order(api_key, secret, prev_client_id=current['client_order_id'], exit_client_id=mkt_cid, desired_qty=desired, step=step, now=now)
            closed = res.get('status') == 'FILLED' or res.get('closed_by_prev_stop') or res.get('already_closed')
            with self._db() as db:
                db.execute('UPDATE protections SET client_order_id=?,exchange_order_id=?,status=?,updated_at=? WHERE intent_id=?',
                           (res.get('client_id') or mkt_cid, res.get('order_id') or current['exchange_order_id'],
                            'CLOSED' if closed else 'EXITING', now, 'manual:SOL'))
            return {'advanced': True, 'stage': 'CLOSED' if closed else (current.get('stage') or 'S3'), 'escalated': True, 'buy_orders': 0}
        finally:
            api_key = secret = None

    def _advance_apts_step(self, intent_id, *, entry, qty, current_stop, current_stage, bid, evidence, tick, now,
                           micro_features=None, strategy_id='T1', stop_distance=None,
                           entry_time_ms=0, reversal_confirm_ticks=1):
        from dataclasses import replace
        from solbot.mathematics.apts_exit import APTSState, update_protection
        from solbot.mathematics.contracts import make_context
        buy_ratio = None
        if isinstance(micro_features, dict) and micro_features.get('ready'):
            buy_ratio = micro_features.get('buy_ratio')
        if not buy_ratio:
            buy_ratio = format(getattr(evidence, 'buy_ratio', Decimal('0.5')), 'f')
        features = {
            'symbol': 'SOLUSDT', 'bid': format(bid, 'f'), 'ready': True,
            'buy_ratio': str(buy_ratio), 'exchange_time_ms': now,
        }
        context = make_context(
            symbol='SOLUSDT', account_id='SOL-shadow', strategy_id=strategy_id,
            strategy_version='p2-research-1', signal_id=intent_id.replace(':', '.'),
            decision_time_ms=now, features=features, inputs={'intent': intent_id},
            parameter_version='SOLUSDT-apts-research-1', ttl_ms=5000,
        )
        state = self._load_state(intent_id)
        if state is not None and (
            state.trigger_latched
            or Decimal(str(state.entry_price)) != Decimal(str(entry))
            or (entry_time_ms and state.entry_time_ms and int(state.entry_time_ms) != int(entry_time_ms))
        ):
            state = None
        prev_stage = current_stage or 'S0'
        if state is None:
            state = APTSState.create(
                context, entry, qty, tick,
                entry_fee_rate=DEFAULT_FEE_RATE, exit_fee_rate=DEFAULT_FEE_RATE,
                slippage_rate=DEFAULT_SLIPPAGE_RATE, stop_distance=stop_distance,
                entry_time_ms=entry_time_ms, reversal_confirm_ticks=reversal_confirm_ticks,
            )
            best_stop = max(state.stop_price, current_stop) if current_stop and current_stop > 0 else state.stop_price
            state = replace(state, stop_price=best_stop, last_decision_time_ms=now - 1)
        else:
            prev_stage = APTS_STAGE_SHORT.get(state.stage, prev_stage)
            updates = {}
            if entry_time_ms and not state.entry_time_ms:
                updates['entry_time_ms'] = int(entry_time_ms)
            if reversal_confirm_ticks and state.reversal_confirm_ticks != max(1, int(reversal_confirm_ticks)):
                updates['reversal_confirm_ticks'] = max(1, int(reversal_confirm_ticks))
            if updates:
                state = replace(state, **updates)
        decision = update_protection(state, context, bid, evidence, tick_size=tick, quantity=qty, native_supported=True)
        stage = APTS_STAGE_SHORT.get(decision.state.stage, 'S0')
        note = 'REVERSAL_OBSERVED_S0_STOP_RETAINED' if 'REVERSAL_OBSERVED_S0_STOP_RETAINED' in decision.reasons else ''
        self._save_state(decision.state, stage, decision.status, now, note, intent_id=intent_id)
        return decision, stage, prev_stage

    def mark(self):
        if not self.db_path.exists():
            return None
        with self._db() as db:
            row = db.execute("SELECT price,observed_at FROM marks WHERE symbol='SOLUSDT'").fetchone()
        if row is None:
            return None
        return {'mark_price': row[0], 'observed_at': row[1], 'price_source': 'BID'}

    def _remember_mark(self, bid, now):
        with self._db() as db:
            db.execute('INSERT INTO marks VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,observed_at=excluded.observed_at', ('SOLUSDT', format(bid, 'f'), now))

    def _held_sol(self):
        if not self.snapshot_path.exists():
            return Decimal(0)
        with closing(sqlite3.connect(self.snapshot_path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            row = db.execute("SELECT free,locked FROM balances WHERE asset='SOL'").fetchone()
        if row is None:
            return Decimal(0)
        return Decimal(str(row[0] or '0')) + Decimal(str(row[1] or '0'))

    def _load_state(self, intent_id='manual:SOL'):
        from solbot.mathematics.apts_exit import APTSState
        with self._db() as db:
            row = db.execute('SELECT state_json FROM custody_apts WHERE intent_id=?', (intent_id,)).fetchone()
        return APTSState.from_dict(json.loads(row[0])) if row else None

    def _save_state(self, state, stage, status, now, note='', intent_id='manual:SOL'):
        body = json.dumps(state.asdict(), sort_keys=True)
        with self._db() as db:
            db.execute('INSERT INTO custody_apts(intent_id,state_json,stage,status,updated_at,note) VALUES(?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET state_json=excluded.state_json,stage=excluded.stage,status=excluded.status,updated_at=excluded.updated_at,note=excluded.note',
                       (intent_id, body, stage, status, now, note))

    def _raise_stop(self, decision, current, tick, now):
        from solbot.mathematics.protective_stop import stop_limit_price
        try:
            step = self._get_sol_rules(now)['step']
        except Exception:
            step = Decimal('0.001')
        client_id = 'cbotsl' + hashlib.sha256(f"manual:SOL:{now}".encode()).hexdigest()[:16]
        limit = stop_limit_price(decision.stop_price, tick)
        api_key, secret = self._load()
        try:
            res = self._atomic_replace_or_place_stop(
                api_key, secret, prev_client_id=current['client_order_id'], new_client_id=client_id,
                quantity=decision.quantity, stop_price=decision.stop_price, limit_price=limit,
                order_type='STOP_LOSS_LIMIT', step=step, now=now,
            )
        finally:
            api_key = secret = None
        if res.get('closed_by_prev_stop') or res.get('zero_balance'):
            with self._db() as db:
                db.execute('UPDATE protections SET status=?,updated_at=? WHERE intent_id=?', ('CLOSED', now, 'manual:SOL'))
            return
        with self._db() as db:
            db.execute('UPDATE protections SET client_order_id=?,exchange_order_id=?,stop_price=?,limit_price=?,quantity=?,updated_at=? WHERE intent_id=?',
                       (res['client_id'], res['order_id'], format(decision.stop_price, 'f'), format(limit, 'f'), format(res['quantity'], 'f'), now, 'manual:SOL'))

    def _exit_position(self, decision, current, tick, now):
        try:
            step = self._get_sol_rules(now)['step']
        except Exception:
            step = Decimal('0.001')
        client_id = 'cbotex' + hashlib.sha256(f"manual:SOL:{now}".encode()).hexdigest()[:16]
        price = floor_step(decision.limit_price, tick)
        api_key, secret = self._load()
        try:
            res = self._atomic_replace_or_place_stop(
                api_key, secret, prev_client_id=current['client_order_id'], new_client_id=client_id,
                quantity=decision.quantity, stop_price=price, limit_price=price,
                order_type='LIMIT', step=step, now=now,
            )
        finally:
            api_key = secret = None
        if res.get('closed_by_prev_stop') or res.get('zero_balance') or str((res.get('placed') or {}).get('status') or '') == 'FILLED':
            with self._db() as db:
                db.execute('UPDATE protections SET status=?,updated_at=? WHERE intent_id=?', ('CLOSED', now, 'manual:SOL'))
            return
        with self._db() as db:
            db.execute('UPDATE protections SET client_order_id=?,exchange_order_id=?,limit_price=?,quantity=?,status=?,updated_at=? WHERE intent_id=?',
                       (res['client_id'], res['order_id'], format(price, 'f'), format(res['quantity'], 'f'), 'EXITING', now, 'manual:SOL'))

    def _shape(self, api_key, secret):
        if not isinstance(api_key, str) or not isinstance(secret, str):
            raise ValueError('INVALID_KEY')
        api_key, secret = api_key.strip(), secret.strip()
        if not 16 <= len(api_key) <= 128 or not 16 <= len(secret) <= 128:
            raise ValueError('INVALID_KEY')
        if any(not item.isascii() or not item.isalnum() for item in (api_key, secret)):
            raise ValueError('INVALID_KEY')
        return api_key, secret

    def _permissions(self, api_key, secret):
        clock = self._server_time()
        data = self._signed('/sapi/v1/account/apiRestrictions', {}, api_key, secret, clock)
        blocked = ('enableWithdrawals', 'enableInternalTransfer', 'permitsUniversalTransfer')
        if any(data.get(key) is not False for key in blocked) or data.get('enableReading') is not True:
            raise ValueError('WITHDRAWALS_ENABLED' if data.get('enableWithdrawals') is True else 'READ_PERMISSION_REQUIRED')
        return data.get('enableSpotAndMarginTrading') is True

    def _sync(self, api_key, secret, spot_hint=None):
        clock = self._server_time()
        account = self._signed('/api/v3/account', {}, api_key, secret, clock)
        orders = self._signed('/api/v3/allOrders', {'symbol': 'SOLUSDT', 'limit': 100}, api_key, secret, clock)
        if spot_hint is not None:
            spot_enabled = bool(spot_hint) and (account.get('canTrade', True) is not False)
        else:
            try:
                rights = self._signed('/sapi/v1/account/apiRestrictions', {}, api_key, secret, clock)
                blocked = ('enableWithdrawals', 'enableInternalTransfer', 'permitsUniversalTransfer')
                spot_enabled = (
                    rights.get('enableSpotAndMarginTrading') is True
                    and all(rights.get(k) is False for k in blocked)
                    and account.get('canTrade', True) is not False
                )
            except Exception:
                meta = self._meta()
                spot_enabled = bool(meta.get('orders_enabled')) if meta else False
        balances = []
        for row in account.get('balances') or []:
            asset = row.get('asset')
            if asset not in ASSETS:
                continue
            free, locked = str(row.get('free', '0')), str(row.get('locked', '0'))
            try:
                Decimal(free); Decimal(locked)
            except Exception:
                raise ValueError('BINANCE_REJECTED') from None
            balances.append((asset, free, locked, clock))
        manual = []
        for row in orders if isinstance(orders, list) else []:
            client_id = str(row.get('clientOrderId') or '')
            if row.get('symbol') != 'SOLUSDT' or client_id.startswith(BOT_ORDER_PREFIXES):
                continue
            if row.get('status') not in ('NEW', 'PARTIALLY_FILLED', 'FILLED'):
                continue
            manual.append((str(row.get('orderId')), 'SOLUSDT', row.get('side') or '', row.get('status') or '',
                           self._fill_price(row), str(row.get('origQty') or '0'), str(row.get('executedQty') or '0'),
                           int(row.get('time') or clock), client_id))
        self._write_snapshot(balances, manual, clock)
        with self._db() as db:
            db.execute('UPDATE sync_state SET last_sync_at=?,last_error=?,orders_enabled=? WHERE singleton=1', (clock, '', int(bool(spot_enabled))))
        return {'synced': True, 'balances': {asset: {'free': free, 'locked': locked} for asset, free, locked, _ in balances},
                'manual_order_count': len(manual), 'orders_enabled': bool(spot_enabled), 'spot_trading_enabled': bool(spot_enabled), 'private_order_calls': 0}

    def _server_time(self):
        data = self.transport.request('/api/v3/time', '', {})
        value = data.get('serverTime') if isinstance(data, dict) else None
        if type(value) is not int:
            raise ValueError('BINANCE_UNREACHABLE')
        return value

    def _signed(self, path, params, api_key, secret, clock, method='GET'):
        query = dict(params, timestamp=clock, recvWindow=5000)
        encoded = urlencode(sorted((key, value) for key, value in query.items()))
        signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return self.transport.request(path, encoded + '&signature=' + signature, {'X-MBX-APIKEY': api_key}, method)

    def _protect(self, api_key, secret, quantity):
        current = self.protection()
        if current is not None:
            return self._custody_view(current, 0)
        clock = self._server_time()
        rights = self._signed('/sapi/v1/account/apiRestrictions', {}, api_key, secret, clock)
        if rights.get('enableWithdrawals') is True or rights.get('enableInternalTransfer') is True or rights.get('permitsUniversalTransfer') is True:
            raise ValueError('WITHDRAWALS_ENABLED')
        if rights.get('enableSpotAndMarginTrading') is not True:
            raise ValueError('TRADE_PERMISSION_REQUIRED')
        account = self._signed('/api/v3/account', {}, api_key, secret, clock)
        free, locked = Decimal('0'), Decimal('0')
        for row in account.get('balances') or []:
            if row.get('asset') == 'SOL':
                free = Decimal(str(row.get('free') or '0'))
                locked = Decimal(str(row.get('locked') or '0'))
        open_orders = self._order_rows('/api/v3/openOrders', {'symbol': 'SOLUSDT'}, api_key, secret, clock)
        entry = self._custody_entry_price(api_key, secret, clock)
        resting = self._our_stop(open_orders)
        if resting is not None:
            self._adopt(resting, entry, clock)
            return self._custody_view(self.protection(), 0)
        cap = min(Decimal(str(quantity)), free)
        if cap <= 0:
            self._locked_or_short(locked, open_orders)
        info = self.transport.request('/api/v3/exchangeInfo', 'symbol=SOLUSDT', {})
        rules = self._sol_rules(info)
        kind = 'STOP_LOSS_LIMIT' if 'STOP_LOSS_LIMIT' in rules['types'] else 'STOP_LOSS' if 'STOP_LOSS' in rules['types'] else None
        if kind is None:
            raise ValueError('PROTECTION_REJECTED')
        from solbot.mathematics.protective_stop import initial_stop, stop_limit_price
        qty = self._floor(cap, rules['step'])
        stop = initial_stop(entry, rules['tick'], stop_distance=Decimal('0.008'))
        if qty < rules['min_qty']:
            self._locked_or_short(locked, open_orders)
        if qty * stop < rules['min_notional']:
            raise ValueError('PROTECTION_REJECTED')
        client_id = 'cbotsl' + hashlib.sha256(f'manual:SOL:{clock}'.encode()).hexdigest()[:16]
        params = {'symbol': 'SOLUSDT', 'side': 'SELL', 'type': kind, 'quantity': format(qty, 'f'), 'stopPrice': format(stop, 'f'), 'newClientOrderId': client_id}
        limit = None
        if kind == 'STOP_LOSS_LIMIT':
            limit = stop_limit_price(stop, rules['tick'])
            if limit <= 0:
                raise ValueError('PROTECTION_REJECTED')
            params.update(price=format(limit, 'f'), timeInForce='GTC')
        try:
            placed = self._signed('/api/v3/order', params, api_key, secret, clock, method='POST')
        except ValueError as exc:
            if str(exc) not in ('PROTECTION_REJECTED', 'BINANCE_UNREACHABLE'):
                raise
            placed = self._find_client(client_id, api_key, secret, clock)
            if placed is None:
                raise
        if not str(placed.get('orderId') or ''):
            found = self._find_client(client_id, api_key, secret, clock)
            if found is not None:
                placed = found
        self._remember(placed, client_id, qty, stop, limit, entry, clock)
        return self._custody_view(self.protection(), 1)

    @staticmethod
    def _custody_view(current, calls):
        return {'active_custody': True, 'stop_order_id': current['exchange_order_id'], 'stop_price': current['stop_price'],
                'entry_price': current['entry_price'], 'quantity': current['quantity'], 'buy_orders': 0, 'private_order_calls': calls}

    def _order_rows(self, path, params, api_key, secret, clock):
        data = self._signed(path, params, api_key, secret, clock)
        return data if isinstance(data, list) else []

    @staticmethod
    def _our_stop(orders):
        rows = []
        for row in orders:
            client_id = str(row.get('clientOrderId') or '')
            if not client_id.startswith(('cbotsl', 'cbotex')) or row.get('side') != 'SELL':
                continue
            if row.get('symbol') not in (None, 'SOLUSDT') or row.get('status') not in ('NEW', 'PARTIALLY_FILLED'):
                continue
            try:
                stop = Decimal(str(row.get('stopPrice') or '0'))
                price = Decimal(str(row.get('price') or '0'))
            except Exception:
                continue
            if stop <= 0 and price <= 0:
                continue
            rows.append(row)
        if not rows:
            return None
        return max(rows, key=lambda row: int(row.get('time') or 0))

    def _locked_or_short(self, locked, orders):
        if locked > 0 and any(str(row.get('clientOrderId') or '').startswith(('cbotsl', 'cbotex')) for row in orders):
            raise ValueError('STOP_LOCKING_SOL')
        raise ValueError('INSUFFICIENT_FREE_SOL')

    def _custody_entry_price(self, api_key, secret, clock):
        """Anchor manual custody TP/SL baseline to the live market price when custody is clicked."""
        try:
            book = self.transport.request('/api/v3/ticker/bookTicker', 'symbol=SOLUSDT', {})
            if isinstance(book, dict):
                bid = Decimal(str(book.get('bidPrice') or book.get('askPrice') or '0'))
                if bid > 0:
                    self._remember_mark(bid, clock)
                    return bid
        except Exception:
            pass
        mark = self.mark()
        if isinstance(mark, dict):
            try:
                price = Decimal(str(mark.get('mark_price') or '0'))
                if price > 0:
                    return price
            except Exception:
                pass
        return self._live_entry(api_key, secret, clock)

    def _live_entry(self, api_key, secret, clock):
        best = None
        try:
            fills = self._order_rows('/api/v3/allOrders', {'symbol': 'SOLUSDT', 'limit': 100}, api_key, secret, clock)
        except ValueError:
            fills = []
        for row in fills:
            client_id = str(row.get('clientOrderId') or '')
            if row.get('side') != 'BUY' or row.get('status') != 'FILLED' or client_id.startswith(BOT_ORDER_PREFIXES):
                continue
            try:
                price = Decimal(self._fill_price(row))
                when = int(row.get('time') or 0)
            except Exception:
                continue
            if price <= 0:
                continue
            if best is None or when >= best[0]:
                best = (when, price)
        if best is not None:
            return best[1]
        return self._entry_price()

    def _find_client(self, client_id, api_key, secret, clock):
        try:
            sources = (
                self._order_rows('/api/v3/openOrders', {'symbol': 'SOLUSDT'}, api_key, secret, clock),
                self._order_rows('/api/v3/allOrders', {'symbol': 'SOLUSDT', 'limit': 100}, api_key, secret, clock),
            )
        except ValueError:
            return None
        for rows in sources:
            for row in rows:
                if str(row.get('clientOrderId') or '') == client_id and row.get('status') in ('NEW', 'PARTIALLY_FILLED'):
                    return row
        return None

    def _adopt(self, row, entry, clock):
        stop = Decimal(str(row.get('stopPrice') or '0'))
        price = Decimal(str(row.get('price') or '0'))
        if stop <= 0:
            stop = price
        limit = price if price > 0 else None
        self._remember(row, str(row.get('clientOrderId') or ''), Decimal(str(row.get('origQty') or '0')), stop, limit, entry, int(row.get('time') or clock))

    def _remember(self, placed, client_id, quantity, stop, limit, entry, clock):
        cid = str((placed or {}).get('clientOrderId') or client_id)
        order_id = str((placed or {}).get('orderId') or '')
        raw_qty = (placed or {}).get('origQty')
        qty = format(quantity, 'f') if raw_qty in (None, '') else str(raw_qty)
        raw_stop = (placed or {}).get('stopPrice')
        try:
            stop_text = format(stop, 'f') if raw_stop in (None, '', '0', '0.00000000') or Decimal(str(raw_stop)) <= 0 else str(raw_stop)
        except Exception:
            stop_text = format(stop, 'f')
        raw_limit = (placed or {}).get('price')
        if limit is None and raw_limit in (None, ''):
            limit_text = None
        else:
            try:
                limit_text = format(limit, 'f') if limit is not None and (raw_limit in (None, '', '0', '0.00000000') or Decimal(str(raw_limit)) <= 0) else str(raw_limit)
            except Exception:
                limit_text = None if limit is None else format(limit, 'f')
        self._upsert_protection(cid, order_id, qty, stop_text, limit_text, format(entry, 'f'), 'EXITING' if cid.startswith('cbotex') else 'OPEN', clock)

    def _upsert_protection(self, client_id, order_id, quantity, stop, limit, entry, status, placed_at):
        with self._db() as db:
            db.execute(
                'INSERT INTO protections VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET '
                'client_order_id=excluded.client_order_id,exchange_order_id=excluded.exchange_order_id,quantity=excluded.quantity,'
                'stop_price=excluded.stop_price,limit_price=excluded.limit_price,entry_price=excluded.entry_price,status=excluded.status,'
                'placed_at=excluded.placed_at,updated_at=excluded.updated_at',
                ('manual:SOL', client_id, str(order_id), str(quantity), str(stop), None if limit in (None, '') else str(limit), str(entry), status, placed_at, self.clock()))
            db.execute('DELETE FROM custody_apts WHERE intent_id=?', ('manual:SOL',))

    @staticmethod
    def _floor(value, step):
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step

    def _entry_price(self):
        if not self.snapshot_path.exists():
            raise ValueError('ENTRY_PRICE_MISSING')
        with closing(sqlite3.connect(self.snapshot_path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            rows = db.execute("SELECT price,time FROM manual_orders WHERE side='BUY' AND status='FILLED' AND CAST(price AS REAL)>0").fetchall()
        if not rows:
            raise ValueError('ENTRY_PRICE_MISSING')
        return Decimal(str(max(rows, key=lambda row: int(row[1] or 0))[0]))

    @staticmethod
    def _sol_rules(info):
        rows = [row for row in info.get('symbols') or [] if row.get('symbol') == 'SOLUSDT']
        if len(rows) != 1:
            raise ValueError('PROTECTION_REJECTED')
        symbol = rows[0]
        filters = {row.get('filterType'): row for row in symbol.get('filters') or []}
        try:
            tick = Decimal(filters['PRICE_FILTER']['tickSize'])
            step = Decimal(filters['LOT_SIZE']['stepSize'])
            min_qty = Decimal(filters['LOT_SIZE']['minQty'])
            notional = filters.get('NOTIONAL') or filters.get('MIN_NOTIONAL') or {}
            min_notional = Decimal(notional.get('minNotional') or '0')
        except Exception:
            raise ValueError('PROTECTION_REJECTED') from None
        if tick <= 0 or step <= 0:
            raise ValueError('PROTECTION_REJECTED')
        return {'tick': tick, 'step': step, 'min_qty': min_qty, 'min_notional': min_notional, 'types': set(symbol.get('orderTypes') or [])}

    @staticmethod
    def _fill_price(row):
        try:
            price = Decimal(str(row.get('price') or '0'))
            executed = Decimal(str(row.get('executedQty') or '0'))
            quote = Decimal(str(row.get('cummulativeQuoteQty') or '0'))
        except Exception:
            return '0'
        if price <= 0 and executed > 0 and quote > 0:
            price = quote / executed
        return format(price, 'f')

    def _store(self, api_key, secret, orders_enabled=False):
        key_nonce, key_box = self._seal(api_key)
        secret_nonce, secret_box = self._seal(secret)
        hint = api_key[-4:]
        now = self.clock()
        with self._db() as db:
            db.execute('DELETE FROM credentials')
            db.execute('INSERT INTO credentials VALUES(1,?,?,?,?,?,?)', (hint, key_nonce, key_box, secret_nonce, secret_box, now))
            db.execute('INSERT INTO sync_state(singleton,key_hint,saved_at,last_sync_at,last_error,orders_enabled) VALUES(1,?,?,0,"",?) ON CONFLICT(singleton) DO UPDATE SET key_hint=excluded.key_hint,saved_at=excluded.saved_at,last_error="",orders_enabled=excluded.orders_enabled', (hint, now, int(bool(orders_enabled))))

    def _load(self):
        with self._db() as db:
            row = db.execute('SELECT key_nonce,key_box,secret_nonce,secret_box FROM credentials WHERE singleton=1').fetchone()
        if row is None:
            raise ValueError('NOT_CONFIGURED')
        return self._open(row[0], row[1]), self._open(row[2], row[3])

    def _meta(self):
        if not self.db_path.exists():
            return None
        with self._db() as db:
            row = db.execute('SELECT key_hint,saved_at,last_sync_at,last_error,orders_enabled FROM sync_state WHERE singleton=1').fetchone()
        if row is None:
            return None
        return {'key_hint': row[0], 'saved_at': row[1], 'last_sync_at': row[2], 'last_error': row[3], 'orders_enabled': bool(row[4])}

    def _write_snapshot(self, balances, orders, clock):
        with closing(sqlite3.connect(self.snapshot_path, isolation_level=None, timeout=10)) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS balances(asset TEXT PRIMARY KEY, free TEXT NOT NULL, locked TEXT NOT NULL, observed_at INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS manual_orders(order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL, price TEXT NOT NULL, quantity TEXT NOT NULL, executed_qty TEXT NOT NULL, time INTEGER NOT NULL, client_order_id TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('DELETE FROM balances')
                db.execute('DELETE FROM manual_orders')
                db.executemany('INSERT INTO balances VALUES(?,?,?,?)', balances)
                db.executemany('INSERT INTO manual_orders VALUES(?,?,?,?,?,?,?,?,?)', orders)
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        if os.name == 'posix':
            os.chmod(self.snapshot_path, 0o640)

    def _seal(self, text):
        nonce = os.urandom(12)
        return nonce, AESGCM(self._key()).encrypt(nonce, text.encode(), b'solbot-binance-read')

    def _open(self, nonce, box):
        return AESGCM(self._key()).decrypt(nonce, box, b'solbot-binance-read').decode()

    def _key(self):
        if not self.key_path.exists():
            self.key_path.write_bytes(AESGCM.generate_key(bit_length=256))
            if os.name == 'posix':
                os.chmod(self.key_path, 0o600)
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise ValueError('BINANCE_REJECTED')
        return key

    def _db(self):
        db = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS credentials(singleton INTEGER PRIMARY KEY CHECK(singleton=1), key_hint TEXT NOT NULL, key_nonce BLOB NOT NULL, key_box BLOB NOT NULL, secret_nonce BLOB NOT NULL, secret_box BLOB NOT NULL, saved_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sync_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), key_hint TEXT NOT NULL, saved_at INTEGER NOT NULL, last_sync_at INTEGER NOT NULL, last_error TEXT NOT NULL, orders_enabled INTEGER NOT NULL DEFAULT 0)')
        sync_columns = {row[1] for row in db.execute('PRAGMA table_info(sync_state)')}
        if 'orders_enabled' not in sync_columns:
            db.execute('ALTER TABLE sync_state ADD COLUMN orders_enabled INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE IF NOT EXISTS protections(intent_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, exchange_order_id TEXT NOT NULL, quantity TEXT NOT NULL, stop_price TEXT NOT NULL, limit_price TEXT, entry_price TEXT NOT NULL, status TEXT NOT NULL, placed_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS custody_apts(intent_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, updated_at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT "")')
        columns = {row[1] for row in db.execute('PRAGMA table_info(custody_apts)')}
        if 'note' not in columns:
            db.execute('ALTER TABLE custody_apts ADD COLUMN note TEXT NOT NULL DEFAULT ""')
        db.execute('CREATE TABLE IF NOT EXISTS marks(symbol TEXT PRIMARY KEY, price TEXT NOT NULL, observed_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS user_live(singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL, updated_at INTEGER NOT NULL, reason TEXT NOT NULL)')
        db.execute("CREATE TABLE IF NOT EXISTS bot_orders(signal_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, exchange_order_id TEXT NOT NULL, price TEXT NOT NULL, quantity TEXT NOT NULL, quote TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL)")
        bot_columns = {row[1] for row in db.execute('PRAGMA table_info(bot_orders)')}
        for name, kind in (
            ('stop_distance', 'TEXT'), ('stop_client_id', 'TEXT'), ('filled_at', 'INTEGER'),
            ('high_price', 'TEXT'), ('exit_client_id', 'TEXT'),
            ('stop_price', 'TEXT'), ('stop_updated_at', 'INTEGER'), ('exit_started_at', 'INTEGER'),
            ('strategy_id', 'TEXT'), ('expires_at', 'INTEGER'),
            ('exit_price', 'TEXT'), ('closed_at', 'INTEGER'), ('realized_pnl', 'TEXT'),
        ):
            if name not in bot_columns:
                db.execute(f'ALTER TABLE bot_orders ADD COLUMN {name} {kind}')
        db.execute('BEGIN IMMEDIATE')

        class _Tx:
            def __enter__(self_):
                return db

            def __exit__(self_, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        db.execute('COMMIT')
                    else:
                        db.execute('ROLLBACK')
                finally:
                    db.close()
                return False

        return _Tx()
