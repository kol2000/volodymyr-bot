import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import football_ucl_shadow_v29 as v29
import football_forward_journal_v14 as journal


class V29Tests(unittest.TestCase):
    def test_synthetic_result_writer_uses_unambiguous_tag(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'db.sqlite3'
            with journal.open_db(db) as con:
                con.execute('''INSERT INTO fixtures(competition,kickoff_utc,home,away,fixture_source,registered_utc)
                  VALUES(?,?,?,?,?,?)''', ('UCL','2026-11-24T20:00:00+00:00','A','B','TEST','2026-11-22T10:00:00+00:00'))
                con.commit()
            now=datetime(2026,12,6,tzinfo=timezone.utc)
            self.assertEqual(v29.synthetic_result_write(db, [(1,2,0,'FAKE',5)], now),1)
            with sqlite3.connect(db) as c:
                source=c.execute('SELECT result_source FROM results').fetchone()[0]
            self.assertIn('SYNTHETIC SHADOW ONLY',source)
            self.assertIn('NOT REAL',source)

    def test_empty_result_batch_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'no_db.sqlite3'
            self.assertEqual(v29.synthetic_result_write(db, [], datetime.now(timezone.utc)),0)
            self.assertFalse(db.exists())

    def test_rehearsal_refuses_missing_database(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                v29.rehearse(Path(d)/'no.sqlite3',Path(d)/'no.html')

    def test_adapter_error_stops_controller(self):
        now=datetime(2026,12,6,tzinfo=timezone.utc)
        class Runner:
            def __init__(self): self.n=0
            def __call__(self,command,**kw):
                self.n+=1
                return type('Result',(),{'returncode':2})()
        r=Runner()
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'test.sqlite3'
            db.touch()
            with patch.object(v29.controller,'DB',db):
                with self.assertRaisesRegex(RuntimeError,'returned 2'):
                    v29.controller.execute('run',commit=True,now=now,runner=r)
        self.assertEqual(r.n,1)

    def test_no_network_call_in_runner(self):
        """The dispatcher must have an explicit allowlist; no arbitrary subprocess."""
        with tempfile.TemporaryDirectory() as d:
            fake=v29.ShadowRunner.__new__(v29.ShadowRunner)
            fake.calls=[]
            outcome=fake(['python3','/tmp/unknown.py'],cwd=d)
            self.assertEqual(outcome.returncode,2)


if __name__ == '__main__':
    unittest.main()
