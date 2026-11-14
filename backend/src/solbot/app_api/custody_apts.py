"""Advance custodied spot & live bot positions using the 1s microstructure feed and APTS ladder."""
import logging
import time
from decimal import Decimal

from solbot.mathematics.trend_exhaustion import TrendEvidence, from_closed_inputs
from solbot.mathematics.volatility_regime import decimal_atr

from .micro_feed import read_micro_snapshot

logger = logging.getLogger(__name__)


def _fallback_evidence(candles, micro_features):
    five = candles.get('5m') or []
    buy_ratio = Decimal('0.5')
    if isinstance(micro_features, dict) and micro_features.get('ready') and micro_features.get('buy_ratio'):
        buy_ratio = Decimal(str(micro_features['buy_ratio']))
    elif five:
        quote = Decimal(str(five[-1].get('quote_volume') or '0'))
        bought = Decimal(str(five[-1].get('taker_buy_quote_volume') or '0'))
        if quote > 0:
            buy_ratio = bought / quote
    if len(five) > 16:
        return TrendEvidence(ready=True, atr=decimal_atr(five), previous_atr=decimal_atr(five[:-1]),
                             buy_ratio=buy_ratio, previous_buy_ratio=buy_ratio)
    return TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'),
                         buy_ratio=buy_ratio, previous_buy_ratio=buy_ratio)


def evidence_for(reader, now, micro_features=None):
    candles = {}
    if reader is not None:
        for interval in ('1m', '5m', '15m', '1h', '4h'):
            try:
                rows = reader.read('SOLUSDT_market', 'SELECT * FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 120', ('SOLUSDT', interval))
            except Exception:
                rows = []
            candles[interval] = list(reversed(rows))
    fallback = _fallback_evidence(candles, micro_features)
    features = {'symbol': 'SOLUSDT', 'ready': True, 'buy_ratio': format(fallback.buy_ratio, 'f')}
    if isinstance(micro_features, dict) and micro_features.get('ready'):
        for key in ('bid', 'ask', 'short_volatility', 'imbalance', 'spread_bps', 'exchange_time_ms'):
            if micro_features.get(key) is not None:
                features[key] = micro_features[key]
    try:
        evidence = from_closed_inputs('SOLUSDT', candles, features, now)
        if evidence.ready:
            return evidence
    except Exception:
        pass
    return fallback


def _resolve_bid_and_tick(vault, bid, tick, micro, now):
    ask = micro.get('ask') if isinstance(micro, dict) else None
    if bid is None:
        if isinstance(micro, dict) and micro.get('bid'):
            bid = micro['bid']
        else:
            book = vault.transport.request('/api/v3/ticker/bookTicker', 'symbol=SOLUSDT', {})
            bid = book.get('bidPrice')
            ask = ask or book.get('askPrice')
    if tick is None:
        tick = vault._get_sol_rules(now)['tick']
    return bid, tick, ask


def step(vault, reader=None, now=None, bid=None, evidence=None, tick=None, micro_path=None, sync_account=True):
    now = int(now if now is not None else time.time_ns() // 1_000_000)
    micro = read_micro_snapshot(micro_path, now_ms=now)
    if sync_account:
        try:
            vault.refresh_account()
        except Exception:
            pass
    result = {'advanced': False}
    has_manual = vault.protection() is not None
    live_on = vault.live_state() == 'LIVE_ON'
    has_bots = vault.has_open_bots(include_dust=True)
    ask = micro.get('ask') if isinstance(micro, dict) else None
    if has_manual:
        bid, tick, ask = _resolve_bid_and_tick(vault, bid, tick, micro, now)
        step_ev = evidence or evidence_for(reader, now, micro_features=micro)
        result = vault.advance(bid, step_ev, tick, now, micro_features=micro)
    if reader is not None and live_on:
        bid, tick, ask = _resolve_bid_and_tick(vault, bid, tick, micro, now)
        result['entry'] = vault.consider_entry(reader, bid, tick, now, ask=ask)
        has_bots = has_bots or bool(result['entry'].get('placed'))
    if has_bots:
        bid, tick, ask = _resolve_bid_and_tick(vault, bid, tick, micro, now)
        bot_ev = evidence or evidence_for(reader, now, micro_features=micro)
        try:
            result['bot_stops'] = vault.sync_bot_stops(bid, tick, now, evidence=bot_ev, micro_features=micro)
        except Exception as exc:
            result['bot_stops'] = {'error': type(exc).__name__}
    return result


def loop(vault, reader, *, micro_path=None, step_seconds=1.0, account_sync_seconds=15.0):
    last_sync = 0.0
    last_err = None
    while True:
        now_mono = time.monotonic()
        sync_due = (now_mono - last_sync) >= account_sync_seconds
        try:
            step(vault, reader, micro_path=micro_path, sync_account=sync_due)
            if sync_due:
                last_sync = now_mono
            last_err = None
        except Exception as exc:
            key = (type(exc).__name__, str(exc))
            if key != last_err:
                logger.warning('custody_apts step failed: %s', key[0])
                last_err = key
        time.sleep(step_seconds)
