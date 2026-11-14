"""Versioned, closed-candle rebound state machine for isolated research."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json

VERSION = 'r2-rebound-2'
PARAMETERS = {'candidate_ms': 7200000, 'recovery_ms': 1800000,
              'oversold': 20, 'recovery': 48, 'order_ms': 60000,
              'atr_buffer': '0.15', 'chase': '0.003', 'maximum_candidate_ms': 14400000}
PARAMETER_HASH = sha256(json.dumps(PARAMETERS, sort_keys=True).encode()).hexdigest()
TERMINAL = {'IDLE', 'INVALIDATED', 'EXPIRED', 'CONSUMED'}


def _climax_exhaustion(state, five, metrics):
    rsi_series = [float(v) for v in (metrics.get('5m', {}).get('rsi6') or []) if v is not None]
    rvol_series = [float(v) for v in (metrics.get('5m', {}).get('rvol20') or []) if v is not None]
    if len(rsi_series) < 2 or not rvol_series or len(five) < 2:
        return False
    cur_rsi, prev_rsi = rsi_series[-1], rsi_series[-2]
    min_rsi = min(rsi_series[-6:] + [float(state.get('min_rsi', cur_rsi))])
    cur_rvol = rvol_series[-1]
    max_rvol = max(rvol_series[-6:] + [float(state.get('max_rvol', cur_rvol))])
    rsi15 = [float(v) for v in ((metrics.get('15m') or {}).get('rsi6') or []) if v is not None]
    rsi1h = [float(v) for v in ((metrics.get('1h') or {}).get('rsi6') or []) if v is not None]
    mtf_oversold = (
        min_rsi <= 18.0
        and (
            bool(rsi15 and min(rsi15[-3:]) <= 24.0)
            or bool(rsi1h and min(rsi1h[-2:]) <= 28.0)
            or min_rsi <= 14.0
        )
    )
    bar, prev = five[-1], five[-2]
    low = Decimal(str(state['candidate_low']))
    bar_low, bar_open, bar_close = Decimal(str(bar['low'])), Decimal(str(bar['open'])), Decimal(str(bar['close']))
    prev_low, prev_high = Decimal(str(prev['low'])), Decimal(str(prev['high']))
    prev_body_top = max(Decimal(str(prev['open'])), Decimal(str(prev['close'])))
    quote_vol = float(bar.get('quote_volume') or 0.0)
    taker_buy = float(bar.get('taker_buy_quote_volume') or 0.0)
    buy_ratio = (taker_buy / quote_vol) if quote_vol > 0 else 0.0
    green = bar_close > bar_open
    floor_ok = bar['close_time'] > state['origin_time'] and bar_low >= low
    rsi_turn = cur_rsi > prev_rsi and cur_rsi >= 22.0 and (cur_rsi - min_rsi) >= 10.0
    step_up_breakout = bar_low > prev_low >= low and bar_close > prev_high and buy_ratio >= 0.50
    climax_reclaim = (
        bar_low >= prev_low
        and bar_close >= prev_body_top
        and cur_rvol < max_rvol
        and buy_ratio >= 0.52
        and cur_rsi >= 24.0
    )
    return bool(mtf_oversold and max_rvol >= 1.6 and green and floor_ok and rsi_turn and (step_up_breakout or climax_reclaim))


def advance(previous, candles, metrics, regime, now):
    """Return next state and optional durable intent; caller commits atomically."""
    state = deepcopy(previous) if previous else {'state': 'IDLE', 'last_5m': 0}
    for _k in ('min_rsi', 'max_rvol'):
        if _k in state and isinstance(state[_k], float):
            state[_k] = str(state[_k])
    five, one = candles['5m'], candles['1m']
    bar, minute = five[-1], one[-1]
    if minute['close_time'] != now or bar['close_time'] > now:
        raise ValueError('R2 requires current finalized candles')
    fresh_five = bar['close_time'] > state.get('last_5m', 0)
    state['last_5m'] = bar['close_time']
    state['as_of_ms'] = now
    intent = None
    if state['state'] not in TERMINAL and now >= state['expires_at']:
        state.update(state='EXPIRED', reason='CANDIDATE_EXPIRED')
        return state, intent
    if state['state'] in TERMINAL:
        cur_rsi5 = float(metrics['5m']['rsi6'][-1])
        rvol_list = (metrics.get('5m') or {}).get('rvol20') or []
        cur_rvol = float(rvol_list[-1]) if rvol_list else 1.0
        rsi15_list = (metrics.get('15m') or {}).get('rsi6') or []
        mtf_entry = bool(rvol_list and cur_rsi5 <= 24.0 and cur_rvol >= 1.5 and rsi15_list and float(rsi15_list[-1]) <= 22.0)
        new_event = fresh_five and (cur_rsi5 <= PARAMETERS['oversold'] or mtf_entry)
        if new_event and (regime in ('DOWNTREND', 'UNCERTAIN') or (mtf_entry and regime == 'RANGE')):
            identity = f"{VERSION}:SOLUSDT:{bar['close_time']}"
            state.update(state='WATCHING', candidate_id=sha256(identity.encode()).hexdigest(),
                         origin_time=bar['close_time'], expires_at=bar['close_time'] + PARAMETERS['candidate_ms'],
                         candidate_low=str(bar['low']), recovery_time=None, climax_turn_time=None,
                         min_rsi=str(cur_rsi5), max_rvol=str(cur_rvol), reason='WAITING_FOR_FLOOR',
                         parameter_hash=PARAMETER_HASH)
        return state, intent
    low = Decimal(state['candidate_low'])
    if state['state'] == 'ARMED' and Decimal(minute['close']) < low:
        state.update(state='INVALIDATED', reason='CANDIDATE_LOW_BROKEN')
        return state, intent
    if fresh_five:
        cur_rsi5 = float(metrics['5m']['rsi6'][-1])
        state['min_rsi'] = str(min(float(state.get('min_rsi', cur_rsi5)), cur_rsi5))
        rvol_list = (metrics.get('5m') or {}).get('rvol20') or []
        if rvol_list:
            state['max_rvol'] = str(max(float(state.get('max_rvol', 1.0)), float(rvol_list[-1])))
        if Decimal(bar['low']) < low:
            state.update(state='WATCHING', candidate_low=str(bar['low']),
                         expires_at=min(state['origin_time'] + PARAMETERS['maximum_candidate_ms'],
                                        bar['close_time'] + PARAMETERS['candidate_ms']),
                         recovery_time=None, climax_turn_time=None, reason='NEW_LOW_WAITING_FOR_FLOOR')
            return state, intent
        rsi = metrics['5m']['rsi6']
        if rsi[-2] <= PARAMETERS['recovery'] < rsi[-1]:
            state['recovery_time'] = bar['close_time']
        if _climax_exhaustion(state, five, metrics):
            state['climax_turn_time'] = bar['close_time']
    recovery = state.get('recovery_time')
    recovered = recovery is not None and 0 <= now - recovery < PARAMETERS['recovery_ms']
    floor = all(row['close_time'] > state['origin_time'] and Decimal(row['low']) > low
                for row in five[-2:])
    reclaim = Decimal(bar['close']) >= Decimal(str(metrics['5m']['vwap20'][-1]))
    climax_time = state.get('climax_turn_time')
    climax_ready = (
        climax_time is not None
        and 0 <= now - climax_time < PARAMETERS['recovery_ms']
        and Decimal(str(bar['low'])) >= low
    )
    if not ((recovered and floor and reclaim) or climax_ready):
        state.update(state='WATCHING', reason='WAITING_FOR_RECOVERY_FLOOR_VWAP')
        return state, intent
    state.update(state='ARMED', reason='WAITING_FOR_BREAKOUT')
    classic_breakout = (Decimal(minute['close']) > max(Decimal(row['high']) for row in one[-3:-1])
                        and Decimal(minute['close']) > Decimal(minute['open'])
                        and minute['close_time'] > bar['close_time'])
    climax_breakout = (
        climax_ready
        and Decimal(str(minute['low'])) >= low
        and (
            (fresh_five and climax_time == bar['close_time'] and now == bar['close_time'])
            or (
                minute['close_time'] > bar['close_time']
                and (Decimal(str(minute['close'])) > Decimal(str(minute['open'])) or Decimal(str(minute['close'])) >= Decimal(str(bar['close'])))
            )
        )
    )
    if classic_breakout or climax_breakout:
        price = Decimal(minute['close'])
        atr_buf = Decimal(PARAMETERS['atr_buffer']) * Decimal(str(metrics['5m']['atr14'][-1]))
        stop = low - atr_buf
        if climax_ready and price > 0 and (price - stop) / price > Decimal('0.018'):
            swing_low = min(Decimal(str(row['low'])) for row in five[-3:])
            tight_stop = swing_low - atr_buf
            if 0 < tight_stop < price:
                stop = max(stop, tight_stop)
        if stop <= 0 or stop >= price:
            state.update(state='INVALIDATED', reason='INVALID_STRUCTURE_STOP')
            return state, None
        intent = {'symbol': 'SOLUSDT', 'strategy_id': 'R2', 'strategy_version': VERSION,
                  'parameter_hash': PARAMETER_HASH, 'candidate_id': state['candidate_id'],
                  'signal_id': state['candidate_id'], 'signal_time': now,
                  'expires_at': min(now + PARAMETERS['order_ms'], state['expires_at']),
                  'entry_low': str(price), 'entry_high': str(price * (1 + Decimal(PARAMETERS['chase']))),
                  'stop_price': str(stop), 'stop_distance': str((price - stop) / price),
                  'candidate_low': state['candidate_low'],
                  'accepted': True, 'reasons': [], 'regime': regime, 'side': 'BUY'}
        state.update(state='TRIGGERED', reason='INTENT_PENDING', trigger_time=now)
    return state, intent
