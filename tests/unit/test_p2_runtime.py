import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from solbot.p2_runtime import load_cursor, atomic_json

class CursorTests(unittest.TestCase):
    def test_cursor_is_bound_to_source_and_survives_restore_without_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cursor.json'
            conn=sqlite3.connect(':memory:')
            try:
                conn.execute('CREATE TABLE decisions(id INTEGER,signal_id TEXT)')
                conn.execute("INSERT INTO decisions VALUES(7,'signal-a')")
                store=SimpleNamespace(connection=conn)
                binding={'symbol':'SOLUSDT','strategy_db':'a','account':'shadow'}
                self.assertEqual(load_cursor(path,binding,store),0)
                atomic_json(path,{'binding':binding,'last_decision_id':7,'signal_id':'signal-a'})
                self.assertEqual(load_cursor(path,binding,store),7)
                with self.assertRaises(ValueError):load_cursor(path,{**binding,'strategy_db':'b'},store)
                conn.execute('DELETE FROM decisions')
                self.assertEqual(load_cursor(path,binding,store),0)
                conn.execute("INSERT INTO decisions VALUES(7,'signal-b')")
                self.assertEqual(load_cursor(path,binding,store),0)
            finally:conn.close()
