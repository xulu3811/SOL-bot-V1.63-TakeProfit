"""Atomic R2 checkpoint/state/outbox, kept separate from legacy strategy stores."""
import json
import sqlite3
from pathlib import Path
from solbot.strategy.r2_rebound import VERSION, PARAMETER_HASH, advance


class R2Store:
    def __init__(self, path, *, mode='research'):
        if mode not in ('research', 'replay'):
            raise ValueError('unsupported R2 mode')
        self.connection = sqlite3.connect(Path(path), isolation_level=None, timeout=5)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS r2_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS r2_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                as_of_ms INTEGER NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS r2_outbox(signal_id TEXT PRIMARY KEY,payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',outcome TEXT);
            CREATE TABLE IF NOT EXISTS r2_events(as_of_ms INTEGER PRIMARY KEY,payload TEXT NOT NULL);
        ''')
        for key, value in [('version', VERSION), ('parameters', PARAMETER_HASH), ('mode', mode)]:
            self.connection.execute('INSERT OR IGNORE INTO r2_metadata VALUES(?,?)', (key, value))
            actual = self.connection.execute('SELECT value FROM r2_metadata WHERE key=?', (key,)).fetchone()[0]
            if actual != value:
                self.connection.close()
                raise ValueError('R2 store binding mismatch')

    def close(self):
        self.connection.close()

    def step(self, candles, metrics, regime, now):
        db = self.connection
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT as_of_ms,payload FROM r2_state WHERE singleton=1').fetchone()
            if row and row[0] >= now:
                db.execute('COMMIT')
                return json.loads(row[1]), None
            previous = json.loads(row[1]) if row else None
            # Delivery is handled by the durable outbox. A triggered candidate
            # cannot create a second intent while waiting for acknowledgement.
            if previous and previous['state'] == 'TRIGGERED':
                state, intent = dict(previous, as_of_ms=now), None
                if now >= state['expires_at']:
                    state.update(state='EXPIRED', reason='CANDIDATE_EXPIRED')
            else:
                state, intent = advance(previous, candles, metrics, regime, now)
            payload = json.dumps(state, sort_keys=True)
            db.execute('INSERT OR REPLACE INTO r2_state VALUES(1,?,?)', (now, payload))
            db.execute('INSERT INTO r2_events VALUES(?,?)', (now, payload))
            if intent:
                db.execute('INSERT INTO r2_outbox(signal_id,payload) VALUES(?,?)',
                           (intent['signal_id'], json.dumps(intent, sort_keys=True)))
            db.execute('COMMIT')
            return state, intent
        except BaseException:
            db.execute('ROLLBACK')
            raise

    def acknowledge(self, signal_id, outcome):
        db = self.connection
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT status FROM r2_outbox WHERE signal_id=?', (signal_id,)).fetchone()
            if row is None:
                raise ValueError('unknown R2 intent')
            if row[0] == 'PENDING':
                db.execute("UPDATE r2_outbox SET status='DONE',outcome=? WHERE signal_id=?",
                           (json.dumps(outcome, sort_keys=True), signal_id))
                row = db.execute('SELECT payload FROM r2_state WHERE singleton=1').fetchone()
                state = json.loads(row[0])
                if state.get('candidate_id') == signal_id and state['state'] == 'TRIGGERED':
                    state.update(state='CONSUMED', reason='INTENT_ACKNOWLEDGED')
                    db.execute('UPDATE r2_state SET payload=? WHERE singleton=1', (json.dumps(state, sort_keys=True),))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
