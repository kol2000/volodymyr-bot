import sys
import types
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from collections import defaultdict

if 'football_predictions' not in sys.modules:
    stub=types.ModuleType('football_predictions')
    stub.canonical=lambda s:s.strip().lower()
    stub.estimate=lambda h,a,hr,ar:{'probabilities':[40,30,30]}
    sys.modules['football_predictions']=stub

from football_backtest import Fixture
from football_forecast_live_v7 import get_date, get_time, forecast_at, create_db, record, fixtures_for

class LiveTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(get_date('22/11/2026'),date(2026,11,22))
        self.assertEqual(get_date('22/11/26'),date(2026,11,22))
        self.assertEqual(get_time('19:45').hour,19)
        with self.assertRaises(ValueError):get_time('')

    def test_immutable_db(self):
        with TemporaryDirectory() as d:
            db=Path(d)/'pred.sqlite3';create_db(db)
            m={'league':'EPL','day':date(2026,11,23),'home':'A','away':'B',
               'kickoff_utc':datetime(2026,11,23,20,tzinfo=timezone.utc)}
            p={'V1':(.4,.3,.3),'V2':(.5,.25,.25),'V3':(.45,.3,.25)}
            now=datetime(2026,11,22,tzinfo=timezone.utc)
            self.assertEqual(record(db,m,p,now,{},'Europe/London'),3)
            self.assertEqual(record(db,m,p,now,{},'Europe/London'),0)
            import sqlite3
            with sqlite3.connect(db) as cx:
                with self.assertRaises(sqlite3.DatabaseError):
                    cx.execute('UPDATE predictions SET p_home=0 WHERE model="V1"')

    def test_only_past_matches_enter_v2_and_v1(self):
        # Not enough league data -> abstain, even when fixture contains a score.
        m={'league':'EPL','day':date(2026,11,22),'home_id':'a','away_id':'b'}
        match=Fixture(date(2026,11,22),'EPL','a','b',(3,0))
        p,why=forecast_at(m,[match],defaultdict(lambda:1500.0))
        self.assertIsNone(p)
        self.assertIn('V2',why)

if __name__=='__main__':unittest.main()
