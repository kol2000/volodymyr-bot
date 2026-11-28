import sqlite3
import tempfile
import unittest
from pathlib import Path
from football_expansion_v9 import parse_internationals, write_database

HEADER = b'date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n'
class ExpansionTests(unittest.TestCase):
    def test_valid(self):
        data = HEADER + b'2025-11-19,Russia,Chile,2,0,Friendly,Sochi,Russia,FALSE\n'
        rows = parse_internationals(data)
        self.assertEqual(rows[0][3:5], (2, 0))
        self.assertEqual(rows[0][-1], 0)
    def test_reject_future(self):
        with self.assertRaises(ValueError):
            parse_internationals(HEADER + b'2099-01-01,A,B,0,0,Friendly,X,X,TRUE\n')
    def test_reject_duplicates(self):
        line = b'2025-01-02,A,B,1,0,Friendly,X,X,TRUE\n'
        with self.assertRaises(ValueError):
            parse_internationals(HEADER + line + line)
    def test_same_day_same_teams_different_scores_allowed(self):
        data = (HEADER
                + b'1974-02-17,Tahiti,New Caledonia,1,0,Friendly,Papeete,Tahiti,FALSE\n'
                + b'1974-02-17,Tahiti,New Caledonia,2,1,Friendly,Papeete,Tahiti,FALSE\n')
        rows = parse_internationals(data)
        self.assertEqual(len(rows), 2)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'matches.sqlite3'
            write_database(path, rows, 'hash', '2026-01-01T00:00:00+00:00')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM matches').fetchone()[0], 2)
    def test_reject_html(self):
        with self.assertRaises(ValueError):
            parse_internationals(b'<html>page</html>')
    def test_database(self):
        rows = parse_internationals(HEADER + b'2025-01-02,A,B,1,0,Friendly,X,X,TRUE\n')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'x.sqlite3'
            write_database(path, rows, 'fakehash', '2025-01-03T00:00:00+00:00')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('select count(*) from matches').fetchone()[0], 1)
                self.assertEqual(db.execute('select count(*) from provenance').fetchone()[0], 1)
if __name__ == '__main__': unittest.main()
