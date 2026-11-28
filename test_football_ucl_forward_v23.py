import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import football_ucl_forward_v23 as v23

class V23ForecastTests(unittest.TestCase):
    def test_certifier_delegates_to_scored_aware_v22(self):
        with tempfile.TemporaryDirectory() as d:
            html = Path(d)/'uefa.html'; html.write_bytes(b'a'*12000)
            csv = Path(d)/'calendar.csv'; csv.write_text('')
            cal={n:[('match',n)]*18 for n in range(5,9)}
            with patch.object(v23.v22, 'verified_calendar',return_value=(cal,{('scored',): (2,1)})) as verified:
                got, sha=v23.certified_calendar(html,csv,6)
            self.assertEqual(got,cal)
            self.assertEqual(len(sha),64)
            verified.assert_called_once()

    def test_incomplete_round_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            html=Path(d)/'uefa.html';html.write_bytes(b'a'*12000)
            csv=Path(d)/'fixtures.csv'
            cal={n:[('match',n)]*18 for n in range(5,9)}
            cal[7]=[]
            with patch.object(v23.v22,'verified_calendar',return_value=(cal,{})):
                with self.assertRaisesRegex(ValueError,'incomplete'):
                    v23.certified_calendar(html,csv,6)

    def test_existing_predictions_block_across_versions(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.executescript('CREATE TABLE fixtures(id INTEGER PRIMARY KEY, competition TEXT, kickoff_utc TEXT, home TEXT, away TEXT, fixture_source TEXT); CREATE TABLE predictions(id INTEGER PRIMARY KEY,fixture_id INTEGER,model TEXT,version TEXT);')
        conn.execute("INSERT INTO fixtures VALUES(1,'UCL','2026-12-08T20:00:00+00:00','A','B','UEFA verified')")
        conn.execute("INSERT INTO predictions VALUES(1,1,'Elo','v19')")
        row={'competition':'UCL','kickoff_utc':'2026-12-08T20:00:00+00:00','home':'A','away':'B','fixture_source':'UEFA verified'}
        with self.assertRaisesRegex(ValueError,'existing forecasts'):
            v23.prevent_duplicate(conn,[row])
        conn.close()

if __name__ == '__main__': unittest.main()
