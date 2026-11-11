from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from solbot.storage.database import Database,DatabaseUnavailable
from solbot.strategy.scheduler import StrategyStore
from solbot.execution.shadow import ShadowExecutor
from solbot.execution.live import LiveControl

class ForeignStrategyDatabaseTest(unittest.TestCase):
    def test_execution_stores_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, factory in [('shadow', ShadowExecutor), ('live', LiveControl)]:
                path=Path(tmp)/(name+'.sqlite')
                factory(path)
                before=path.read_bytes()
                with self.assertRaises(ValueError):StrategyStore(path)
                self.assertEqual(path.read_bytes(),before)

class ForeignDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_database_rejected_before_wal_or_schema_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'unrelated.sqlite'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE unrelated(x TEXT)')
                conn.commit()
            before=path.read_bytes()
            db=Database(path)
            with self.assertRaises(DatabaseUnavailable):await db.start()
            self.assertEqual(path.read_bytes(),before)
