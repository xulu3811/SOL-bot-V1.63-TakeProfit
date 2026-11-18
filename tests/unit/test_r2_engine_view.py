"""Regression tests for R2 StrategyScreen projection after terminal/idle states."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from solbot.app_api.data import DataReader
from solbot.storage.schema import migrations_for


def _init_r2_db(path: Path, state_payload: dict | None, outbox_payload: dict | None) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            'CREATE TABLE r2_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
            'as_of_ms INTEGER NOT NULL, payload TEXT NOT NULL)'
        )
        db.execute(
            "CREATE TABLE r2_outbox (signal_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'PENDING', outcome TEXT)"
        )
        if state_payload is not None:
            as_of = int(state_payload.get('as_of_ms') or 0)
            db.execute(
                'INSERT INTO r2_state(singleton, as_of_ms, payload) VALUES(1, ?, ?)',
                (as_of, json.dumps(state_payload)),
            )
        if outbox_payload is not None:
            sig_id = str(outbox_payload.get('signal_id') or 'sig-1')
            db.execute(
                "INSERT INTO r2_outbox(signal_id, payload, status) VALUES(?, ?, 'ACKNOWLEDGED')",
                (sig_id, json.dumps(outbox_payload)),
            )


def _init_strategy_db(path: Path, *, regime: str = 'RANGE', ts: int = 1_794_909_000_000) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            'CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, '
            'strategy_id TEXT, signal_time INTEGER, accepted INTEGER, payload_json TEXT)'
        )
        for sid, score in (('T1', 50.0), ('T2', 65.7), ('R1', 33.7)):
            payload = {
                'symbol': 'SOLUSDT',
                'strategy_id': sid,
                'regime': regime,
                'score': score,
                'priority': 'REJECT',
                'accepted': False,
                'reasons': [],
                'score_factors': {
                    'trend': 10.0,
                    'momentum': 10.0,
                    'participation': 10.0,
                    'structure': 10.0,
                    'execution': 10.0,
                },
                'signal_time': ts,
            }
            db.execute(
                'INSERT INTO decisions(symbol, strategy_id, signal_time, accepted, payload_json) '
                'VALUES(?, ?, ?, 0, ?)',
                ('SOLUSDT', sid, ts, json.dumps(payload)),
            )


def _init_market_db(path: Path, closes: list[float], volumes: list[float], *, end_open_ms: int) -> None:
    assert len(closes) == len(volumes)
    with closing(sqlite3.connect(path)) as db, db:
        for version in sorted(migrations_for('SOLUSDT')):
            for stmt in migrations_for('SOLUSDT')[version]:
                db.execute(stmt)
        step = 300_000
        start_open = end_open_ms - (len(closes) - 1) * step
        for idx, (close, vol) in enumerate(zip(closes, volumes)):
            open_ms = start_open + idx * step
            close_ms = open_ms + step - 1
            open_p = closes[idx - 1] if idx > 0 else close
            high_p = max(open_p, close) + 0.15
            low_p = min(open_p, close) - 0.15
            db.execute(
                'INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'SOLUSDT', '5m', open_ms, close_ms,
                    f'{open_p:.4f}', f'{high_p:.4f}', f'{low_p:.4f}', f'{close:.4f}',
                    f'{vol:.4f}', f'{vol * close:.4f}', 100,
                    f'{vol * 0.5:.4f}', f'{vol * close * 0.5:.4f}',
                    1, 'ws', close_ms, close_ms,
                ),
            )


class R2EngineViewTests(unittest.TestCase):
    def _build_reader(
        self,
        folder: str,
        *,
        now_ms: int,
        state_payload: dict | None,
        outbox_payload: dict | None,
        regime: str = 'RANGE',
        closes: list[float] | None = None,
        volumes: list[float] | None = None,
    ) -> DataReader:
        root = Path(folder)
        r2_db = root / 'r2-state.sqlite'
        strat_db = root / 'sol-strategy.sqlite'
        market_db = root / 'sol-market.sqlite'
        _init_r2_db(r2_db, state_payload, outbox_payload)
        _init_strategy_db(strat_db, regime=regime, ts=now_ms - 60_000)
        bars = len(closes) if closes is not None else 30
        if closes is None:
            closes = [72.60 - 0.01 * i for i in range(bars)]
        if volumes is None:
            volumes = [1000.0] * bars
        _init_market_db(market_db, closes, volumes, end_open_ms=now_ms - 300_000)
        return DataReader(
            {
                'SOLUSDT_strategy': strat_db,
                'SOLUSDT_market': market_db,
                'r2_state': r2_db,
            },
            clock=lambda: now_ms,
        )

    def test_consumed_expired_intent_resets_score_gates_and_reasons(self):
        base = 1_794_909_239_999
        state_payload = {
            'state': 'CONSUMED',
            'reason': 'INTENT_ACKNOWLEDGED',
            'candidate_id': 'cand-1753',
            'candidate_low': '71.82',
            'origin_time': base - 600_000,
            'expires_at': base + 7_200_000,
            'as_of_ms': base,
            'recovery_time': base - 120_000,
            'climax_turn_time': None,
            'max_rvol': '1.6083',
        }
        outbox_payload = {
            'signal_id': 'r2-1753',
            'accepted': True,
            'regime': 'RANGE',
            'signal_time': base,
            'expires_at': base + 300_000,
            'entry_low': '72.30',
            'entry_high': '72.52',
            'stop_distance': '0.009',
            'stop_price': '71.82',
            'strategy_version': 'r2-rebound-2',
        }

        with tempfile.TemporaryDirectory() as folder:
            now = [base + 60_000]
            reader = DataReader(
                {
                    'SOLUSDT_strategy': Path(folder) / 'sol-strategy.sqlite',
                    'SOLUSDT_market': Path(folder) / 'sol-market.sqlite',
                    'r2_state': Path(folder) / 'r2-state.sqlite',
                },
                clock=lambda: now[0],
            )
            _init_r2_db(Path(folder) / 'r2-state.sqlite', state_payload, outbox_payload)
            _init_strategy_db(Path(folder) / 'sol-strategy.sqlite', regime='RANGE', ts=base)
            closes = [72.60 - 0.01 * i for i in range(30)]
            volumes = [1000.0] * 30
            _init_market_db(Path(folder) / 'sol-market.sqlite', closes, volumes, end_open_ms=base - 300_000)

            # 1) While the 5m intent is still valid (+60s), R2 remains actively triggered.
            active_res = reader.strategies('SOLUSDT')
            active_r2 = active_res['engines']['R2']
            self.assertTrue(active_r2['accepted'])
            self.assertEqual(active_r2['priority'], 'HIGH')
            self.assertEqual(active_r2['score'], 92.0)
            self.assertEqual(active_r2['reasons'], [])
            self.assertTrue(all(active_r2['gates'].values()))
            self.assertEqual(active_r2['stop_distance'], '0.009')

            # 2) Once time advances past the 5m intent expiry (+35m), R2 unlatches automatically.
            now[0] = base + 35 * 60_000
            expired_res = reader.strategies('SOLUSDT')
            expired_r2 = expired_res['engines']['R2']
            self.assertFalse(expired_r2['accepted'])
            self.assertEqual(expired_r2['priority'], 'REJECT')
            self.assertLess(expired_r2['score'], 55.0)
            self.assertEqual(sum(expired_r2['score_factors'].values()), expired_r2['score'])
            self.assertEqual(expired_r2['reasons'], ['NO_RECENT_R2_CANDIDATE'])
            self.assertFalse(any(expired_r2['gates'].values()))
            self.assertEqual(expired_r2['stop_distance'], '0.012')
            self.assertIsNone(expired_r2['stop_price'])
            self.assertIsNone(expired_r2['entry_low'])
            self.assertIsNone(expired_r2['entry_high'])
            self.assertIsNone(expired_r2['candidate_time'])
            self.assertIsNone(expired_r2['expires_at'])
            self.assertIsNone(expired_r2['signal_id'])
            self.assertEqual(expired_r2['regime'], 'RANGE')

    def test_idle_r2_score_tracks_live_5m_indicators_dynamically(self):
        now = 1_794_911_000_000
        # Flat/neutral market: moderate volume, price slightly above VWAP
        neutral_closes = [72.0] * 25 + [72.1, 72.2, 72.3, 72.4, 72.5]
        neutral_vols = [1000.0] * 30
        # Sharp selloff: low RSI(6), close well below VWAP, elevated RVOL
        oversold_closes = [75.0] * 24 + [74.5, 74.0, 73.5, 73.0, 72.5, 71.8]
        oversold_vols = [1000.0] * 29 + [2200.0]

        with tempfile.TemporaryDirectory() as f1, tempfile.TemporaryDirectory() as f2:
            r_neutral = self._build_reader(
                f1, now_ms=now, state_payload=None, outbox_payload=None,
                regime='DOWNTREND', closes=neutral_closes, volumes=neutral_vols,
            )
            r_oversold = self._build_reader(
                f2, now_ms=now, state_payload=None, outbox_payload=None,
                regime='DOWNTREND', closes=oversold_closes, volumes=oversold_vols,
            )
            e_neutral = r_neutral.strategies('SOLUSDT')['engines']['R2']
            e_oversold = r_oversold.strategies('SOLUSDT')['engines']['R2']

            self.assertEqual(e_neutral['reasons'], ['WAITING_OVERSOLD_CANDIDATE'])
            self.assertFalse(any(e_neutral['gates'].values()))
            self.assertFalse(any(e_oversold['gates'].values()))
            self.assertGreater(e_oversold['score'], e_neutral['score'])
            self.assertLess(e_oversold['score'], 55.0)
            self.assertLess(e_neutral['score'], 55.0)

    def test_watching_and_armed_progression_stays_below_buy_threshold(self):
        now = 1_794_910_000_000
        factor_caps = {'trend': 20.0, 'momentum': 25.0, 'participation': 20.0, 'structure': 15.0, 'execution': 20.0}
        watching_raw = {
            'state': 'WATCHING',
            'reason': 'WAITING_FOR_RECOVERY_FLOOR_VWAP',
            'candidate_id': 'c-1',
            'candidate_low': '71.50',
            'origin_time': now - 300_000,
            'expires_at': now + 3_600_000,
            'as_of_ms': now - 60_000,
            'max_rvol': '1.1',
        }
        armed_raw = dict(
            watching_raw,
            state='ARMED',
            reason='WAITING_FOR_BREAKOUT',
            recovery_time=now - 120_000,
            max_rvol='1.9',
        )
        stale_armed = dict(armed_raw, expires_at=now - 10_000, as_of_ms=now - 600_000)

        with tempfile.TemporaryDirectory() as fw, tempfile.TemporaryDirectory() as fa, tempfile.TemporaryDirectory() as fs:
            w = self._build_reader(fw, now_ms=now, state_payload=watching_raw, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']
            a = self._build_reader(fa, now_ms=now, state_payload=armed_raw, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']
            s = self._build_reader(fs, now_ms=now, state_payload=stale_armed, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']

            self.assertFalse(w['accepted'])
            self.assertTrue(w['gates']['oversold_event'])
            self.assertTrue(w['gates']['floor_held'])
            self.assertFalse(w['gates']['rsi_turn'])
            self.assertFalse(w['gates']['vwap_or_climax'])
            self.assertFalse(w['gates']['breakout_1m'])
            self.assertLess(w['score'], 75.0)

            self.assertFalse(a['accepted'])
            self.assertTrue(a['gates']['oversold_event'])
            self.assertTrue(a['gates']['floor_held'])
            self.assertTrue(a['gates']['rsi_turn'])
            self.assertTrue(a['gates']['vwap_or_climax'])
            self.assertFalse(a['gates']['breakout_1m'])
            self.assertGreater(a['score'], w['score'])
            self.assertLess(a['score'], 75.0)
            self.assertEqual(a['reasons'], ['WAITING_FOR_BREAKOUT'])
            for key, cap in factor_caps.items():
                self.assertLessEqual(a['score_factors'][key], cap)

            self.assertEqual(s['r2_stage'], 'EXPIRED')
            self.assertFalse(any(s['gates'].values()))
            self.assertLess(s['score'], 55.0)
            self.assertEqual(s['reasons'], ['NO_RECENT_R2_CANDIDATE'])

    def test_recent_vs_stale_invalidated_and_triggered_states(self):
        now = 1_794_910_000_000
        with tempfile.TemporaryDirectory() as f1, tempfile.TemporaryDirectory() as f2, tempfile.TemporaryDirectory() as f3, tempfile.TemporaryDirectory() as f4:
            recent_inv = {
                'state': 'INVALIDATED',
                'reason': 'CANDIDATE_LOW_BROKEN',
                'as_of_ms': now - 120_000,
                'max_rvol': '2.1',
                'recovery_time': now - 300_000,
            }
            old_exp = {
                'state': 'EXPIRED',
                'reason': 'CANDIDATE_EXPIRED',
                'as_of_ms': now - 900_000,
                'max_rvol': '1.8',
            }
            fresh_trig = {
                'state': 'TRIGGERED',
                'reason': 'BREAKOUT_TRIGGERED',
                'candidate_id': 'c-trig',
                'origin_time': now - 300_000,
                'expires_at': now + 3_000_000,
                'as_of_ms': now - 5_000,
                'max_rvol': '1.2',
            }
            stale_trig = dict(fresh_trig, expires_at=now - 5_000, as_of_ms=now - 600_000)

            e_inv = self._build_reader(f1, now_ms=now, state_payload=recent_inv, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']
            e_exp = self._build_reader(f2, now_ms=now, state_payload=old_exp, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']
            e_trig = self._build_reader(f3, now_ms=now, state_payload=fresh_trig, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']
            e_strig = self._build_reader(f4, now_ms=now, state_payload=stale_trig, outbox_payload=None).strategies('SOLUSDT')['engines']['R2']

            self.assertEqual(e_inv['reasons'], ['CANDIDATE_LOW_BROKEN'])
            self.assertFalse(any(e_inv['gates'].values()))
            self.assertLess(e_inv['score'], 55.0)

            self.assertEqual(e_exp['reasons'], ['NO_RECENT_R2_CANDIDATE'])
            self.assertFalse(any(e_exp['gates'].values()))
            self.assertLess(e_exp['score'], 55.0)

            self.assertTrue(e_trig['accepted'])
            self.assertGreaterEqual(e_trig['score'], 82.0)
            self.assertEqual(sum(e_trig['score_factors'].values()), e_trig['score'])
            self.assertTrue(all(e_trig['gates'].values()))

            self.assertFalse(e_strig['accepted'])
            self.assertFalse(any(e_strig['gates'].values()))
            self.assertLess(e_strig['score'], 55.0)


if __name__ == '__main__':
    unittest.main()
