"""Advance custodied spot & live bot positions using the 1s microstructure feed and APTS ladder."""
import logging
import time
from decimal import Decimal

from solbot.mathematics.trend_exhaustion import (
    FlowSmoother,
    TrendEvidence,
    blend_flow_ratio,
    from_closed_inputs,
    smooth_micro_features,
)
from solbot.mathematics.volatility_regime import decimal_atr

from .micro_feed import read_micro_snapshot

logger = logging.getLogger(__name__)


def _fallback_evidence(candles, micro_features, *, smoothed_buy=None):
    five = candles.get('5m') or []
    one = candles.get('1m') or []
    buy_ratio = Decimal('0.5')
    prev_buy_ratio = Decimal('0.5')
    if five:
        quote = Decimal(str(five[-1].get('quote_volume') or '0'))
        bought = Decimal(str(five[-1].get('taker_buy_quote_volume') or '0'))
        if quote > 0:
            buy_ratio = bought / quote
            prev_buy_ratio = buy_ratio
        if len(five) >= 2:
            p_quote = Decimal(str(five[-2].get('quote_volume') or '0'))
            p_bought = Decimal(str(five[-2].get('taker_buy_quote_volume') or '0'))
            if p_quote > 0:
                prev_buy_ratio = p_bought / p_quote
    if len(one) >= 3:
        r_quote = sum((Decimal(str(r.get('quote_volume') or '0')) for r in one[-5:]), Decimal(0))
        r_bought = sum((Decimal(str(r.get('taker_buy_quote_volume') or '0')) for r in one[-5:]), Decimal(0))
        if r_quote > 0:
            buy_ratio = max(buy_ratio, r_bought / r_quote)
    candle_br = buy_ratio
    m_buy = None
    if isinstance(micro_features, dict) and micro_features.get('ready') and micro_features.get('buy_ratio'):
        m_buy = Decimal(str(micro_features['buy_ratio']))
    has_smoothed = smoothed_buy is not None
    flow = Decimal(str(smoothed_buy)) if has_smoothed else (m_buy if m_buy is not None else candle_br)
    hl_5m = len(five) >= 3 and Decimal(str(five[-1]['low'])) >= Decimal(str(five[-2]['low'])) >= Decimal(str(five[-3]['low'])) and Decimal(str(five[-1]['low'])) > Decimal(str(five[-3]['low']))
    adverse_move = len(five) >= 2 and Decimal(str(five[-1]['close'])) < Decimal(str(five[-2]['close']))
    bar_time_ms = int(one[-1].get('close_time') or 0) if one else 0
    if len(five) > 16:
        hl_1m = len(one) >= 3 and Decimal(str(one[-1]['low'])) >= Decimal(str(one[-2]['low'])) >= Decimal(str(one[-3]['low'])) and Decimal(str(one[-1]['low'])) > Decimal(str(one[-3]['low']))
        eff_buy = blend_flow_ratio(flow, candle_br, higher_low_5m=hl_5m, adverse_move=adverse_move, has_smoothed=has_smoothed)
        avg_vol = sum((Decimal(str(r.get('volume') or '0')) for r in five[-21:-1]), Decimal(0)) / Decimal(min(20, max(1, len(five) - 1)))
        cur_vol = max(
            Decimal(str(five[-1].get('volume') or '0')),
            sum((Decimal(str(r.get('volume') or '0')) for r in one[-5:]), Decimal(0)) if len(one) >= 5 else Decimal(0),
        )
        rvol = cur_vol / avg_vol if avg_vol > 0 else Decimal(0)
        return TrendEvidence(
            ready=True,
            higher_low_1m=hl_1m,
            higher_low_5m=hl_5m,
            adverse_move=adverse_move,
            rvol=rvol,
            atr=decimal_atr(five),
            previous_atr=decimal_atr(five[:-1]),
            buy_ratio=eff_buy,
            previous_buy_ratio=prev_buy_ratio,
            raw_buy_ratio=m_buy,
            bar_time_ms=bar_time_ms,
        )
    eff_buy = blend_flow_ratio(flow, candle_br, higher_low_5m=False, adverse_move=adverse_move, has_smoothed=has_smoothed)
    return TrendEvidence(ready=True, atr=Decimal('0.4'), previous_atr=Decimal('0.4'),
                         buy_ratio=eff_buy, previous_buy_ratio=prev_buy_ratio,
                         raw_buy_ratio=m_buy, bar_time_ms=bar_time_ms)


def evidence_for(reader, now, micro_features=None, *, smoother=None):
    candles = {}
    if reader is not None:
        for interval in ('1m', '5m', '15m', '1h', '4h'):
            try:
                rows = reader.read(
                    'SOLUSDT_market',
                    'SELECT * FROM market_klines WHERE symbol=? AND interval=? AND is_closed=1 AND close_time<=? ORDER BY open_time DESC LIMIT 120',
                    ('SOLUSDT', interval, int(now)),
                )
            except Exception:
                try:
                    rows = reader.read('SOLUSDT_market', 'SELECT * FROM market_klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT 120', ('SOLUSDT', interval))
                except Exception:
                    rows = []
            candles[interval] = list(reversed(rows))
    micro_eff = smooth_micro_features(micro_features, smoother)
    smoothed_val = micro_eff.get('smoothed_buy_ratio') if isinstance(micro_eff, dict) else None
    fallback = _fallback_evidence(candles, micro_eff, smoothed_buy=smoothed_val)
    raw_ratio = (
        micro_eff.get('buy_ratio')
        if isinstance(micro_eff, dict) and micro_eff.get('ready') and micro_eff.get('buy_ratio') is not None
        else format(fallback.buy_ratio, 'f')
    )
    features = {'symbol': 'SOLUSDT', 'ready': True, 'buy_ratio': str(raw_ratio)}
    if smoothed_val is not None:
        features['smoothed_buy_ratio'] = str(smoothed_val)
    if isinstance(micro_features, dict) and micro_features.get('ready'):
        for key in ('bid', 'ask', 'short_volatility', 'imbalance', 'spread_bps', 'exchange_time_ms'):
            if micro_features.get(key) is not None:
                features[key] = micro_features[key]
    candidate_times = [int(now)]
    one = candles.get('1m') or []
    if one and one[-1].get('close_time') is not None:
        c1 = int(one[-1]['close_time'])
        if 0 <= int(now) - c1 <= 180_000 and c1 not in candidate_times:
            candidate_times.append(c1)
    if len(one) >= 2 and one[-2].get('close_time') is not None:
        c2 = int(one[-2]['close_time'])
        if 0 <= int(now) - c2 <= 240_000 and c2 not in candidate_times:
            candidate_times.append(c2)
    for as_of in candidate_times:
        try:
            evidence = from_closed_inputs('SOLUSDT', candles, features, as_of)
            if evidence.ready:
                return evidence
        except Exception:
            continue
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


def step(vault, reader=None, now=None, bid=None, evidence=None, tick=None, micro_path=None, sync_account=True, smoother=None):
    fixed_now = now is not None
    explicit_bid = bid
    explicit_tick = tick
    now = int(now if fixed_now else time.time_ns() // 1_000_000)
    flow_smoother = smoother if smoother is not None else getattr(vault, '_flow_smoother', None)
    micro = read_micro_snapshot(micro_path, now_ms=now)
    if isinstance(micro, dict) and micro.get('bid') and getattr(vault, 'db_path', None) is not None and vault.db_path.exists():
        try:
            vault._remember_mark(Decimal(str(micro['bid'])), now)
        except Exception:
            pass
    result = {'advanced': False}
    live_on = vault.live_state() == 'LIVE_ON'
    has_bots = vault.has_open_bots(include_dust=True)
    ask = micro.get('ask') if isinstance(micro, dict) else None
    if reader is not None and live_on:
        cur_bid, cur_tick, cur_ask = _resolve_bid_and_tick(vault, explicit_bid, explicit_tick, micro, now)
        result['entry'] = vault.consider_entry(reader, cur_bid, cur_tick, now, ask=cur_ask)
        has_bots = has_bots or bool(result['entry'].get('placed'))
    if has_bots:
        bot_now = now if fixed_now else time.time_ns() // 1_000_000
        bot_micro = micro if fixed_now else (read_micro_snapshot(micro_path, now_ms=bot_now) or micro)
        cur_bid, cur_tick, cur_ask = _resolve_bid_and_tick(vault, explicit_bid, explicit_tick, bot_micro, bot_now)
        bot_ev = evidence or evidence_for(reader, bot_now, micro_features=bot_micro, smoother=flow_smoother)
        try:
            result['bot_stops'] = vault.sync_bot_stops(cur_bid, cur_tick, bot_now, evidence=bot_ev, micro_features=bot_micro, ask=cur_ask)
        except Exception as exc:
            result['bot_stops'] = {'error': type(exc).__name__}
    if sync_account:
        try:
            vault.refresh_account()
        except Exception:
            pass
    if vault.protection() is not None:
        adv_now = now if fixed_now else time.time_ns() // 1_000_000
        adv_micro = micro if fixed_now else (read_micro_snapshot(micro_path, now_ms=adv_now) or micro)
        cur_bid, cur_tick, _ = _resolve_bid_and_tick(vault, explicit_bid, explicit_tick, adv_micro, adv_now)
        step_ev = evidence or evidence_for(reader, adv_now, micro_features=adv_micro, smoother=flow_smoother)
        adv = vault.advance(cur_bid, step_ev, cur_tick, adv_now, micro_features=adv_micro)
        if 'entry' in result:
            adv['entry'] = result['entry']
        if 'bot_stops' in result:
            adv['bot_stops'] = result['bot_stops']
        result = adv
    return result


def loop(vault, reader, *, micro_path=None, step_seconds=1.0, account_sync_seconds=15.0):
    last_sync = 0.0
    last_err = None
    last_entry_skip = None
    flow_smoother = getattr(vault, '_flow_smoother', None) or FlowSmoother()
    while True:
        now_mono = time.monotonic()
        sync_due = (now_mono - last_sync) >= account_sync_seconds
        try:
            res = step(vault, reader, micro_path=micro_path, sync_account=sync_due, smoother=flow_smoother)
            entry = (res or {}).get('entry') if isinstance(res, dict) else None
            if isinstance(entry, dict) and not entry.get('placed') and entry.get('reason'):
                skip_key = str(entry.get('reason'))
                if skip_key != last_entry_skip:
                    logger.warning('live entry skipped: %s', skip_key)
                    last_entry_skip = skip_key
            elif isinstance(entry, dict) and entry.get('placed'):
                last_entry_skip = None
            if sync_due:
                last_sync = now_mono
            last_err = None
        except Exception as exc:
            key = (type(exc).__name__, str(exc))
            if key != last_err:
                logger.warning('custody_apts step failed: %s', key[0])
                last_err = key
        time.sleep(step_seconds)
