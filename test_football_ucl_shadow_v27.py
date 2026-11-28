import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import football_ucl_shadow_v27 as s

class V27ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = self.folder / 'source.db'
        with sqlite3.connect(self.db) as c:
            for table in s.TABLES:
                c.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
            c.execute('INSERT INTO fixtures VALUES (1)')
            c.execute('INSERT INTO predictions VALUES (1)')
        self.program = self.folder / 'fake_v26.py'
        self.program.write_text('test', encoding='utf-8')

    def test_backup_identical_and_no_live_changes(self):
        clone = self.folder / 'clone.db'
        s.backup_to_shadow(self.db, clone)
        self.assertEqual(s.counts(self.db), s.counts(clone))
        with sqlite3.connect(clone) as c:
            c.execute('INSERT INTO results VALUES (1)')
        self.assertEqual(s.counts(self.db)['results'], 0)
        self.assertEqual(s.counts(clone)['results'], 1)

    def test_success_commit_only_shadow(self):
        def fake_runner(cmd, **kwargs):
            self.assertEqual(cmd[2:4], ['--commit', '--db'])
            self.assertNotEqual(Path(cmd[4]).resolve(), self.db.resolve())
            with sqlite3.connect(cmd[4]) as c:
                c.execute('INSERT INTO results VALUES (1)')
            return type('Completed', (), {'returncode': 0})()
        self.assertEqual(s.rehearse(self.db, self.program, fake_runner), 0)
        self.assertEqual(s.counts(self.db)['results'], 0)

    def test_failure_does_not_write_original(self):
        def fake_runner(cmd, **kwargs):
            with sqlite3.connect(cmd[4]) as c:
                c.execute('INSERT INTO results VALUES (1)')
            return type('Completed', (), {'returncode': 2})()
        self.assertEqual(s.rehearse(self.db, self.program, fake_runner), 2)
        self.assertEqual(s.counts(self.db)['results'], 0)

    def test_changes_to_forecasts_detected(self):
        def bad_runner(cmd, **kwargs):
            with sqlite3.connect(cmd[4]) as c:
                c.execute('INSERT INTO predictions VALUES (2)')
            return type('Completed', (), {'returncode': 0})()
        with self.assertRaisesRegex(RuntimeError, 'fixtures or predictions'):
            s.rehearse(self.db, self.program, bad_runner)
        self.assertEqual(s.counts(self.db)['predictions'], 1)

if __name__ == '__main__':
    unittest.main()
