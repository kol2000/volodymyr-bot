import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import football_ucl_controller_v23 as c

NOW=datetime(2026,12,5,20,0,tzinfo=timezone.utc)

class V23ControllerTests(unittest.TestCase):
    def test_all_rounds_supported(self):
        cal={n:[] for n in (6,7,8)}
        for n in (6,7,8):
            cal[n]=[(NOW+timedelta(hours=60),'A','B')]
        got=c.pending_dates(NOW,cal)
        self.assertEqual([n for n,day in got],[6,7,8])

    def test_mixed_window_fails_closed(self):
        cal={6:[(NOW+timedelta(hours=47),'A','B'),(NOW+timedelta(hours=50),'C','D')],7:[],8:[]}
        self.assertEqual(c.pending_dates(NOW,cal),[])

    def test_eighth_round_one_day_can_be_18_matches(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE fixtures(id INTEGER PRIMARY KEY,competition TEXT,kickoff_utc TEXT);CREATE TABLE predictions(id INTEGER PRIMARY KEY,fixture_id INTEGER);')
                for fid in range(1,19):
                    db.execute("INSERT INTO fixtures VALUES(?,'UCL','2027-01-27T20:00:00+00:00')",(fid,))
                    db.execute('INSERT INTO predictions (fixture_id) VALUES(?)',(fid,))
                    db.execute('INSERT INTO predictions (fixture_id) VALUES(?)',(fid,))
            with patch.object(c,'DB',path):
                self.assertEqual(c.forecast_date_state('2027-01-27',18),'locked')
                with self.assertRaisesRegex(ValueError,'Partial'):
                    c.forecast_date_state('2027-01-27',9)

    def test_check_never_writes_observations_or_journal(self):
        commands=[]
        class R: returncode=0
        def runner(cmd,**kwargs): commands.append(cmd);return R()
        with patch.object(c.DB.__class__,'is_file',return_value=True),\
             patch.object(c,'certify_calendar',return_value={6:[],7:[],8:[]}):
            c.execute('check',now=NOW,runner=runner)
        self.assertEqual(len(commands),1)
        self.assertNotIn('--commit',commands[0])
        self.assertNotIn('--observe',commands[0])
        self.assertTrue(commands[0][1].endswith('football_ucl_results_v26.py'))

    def test_commit_runs_observe_before_commit(self):
        commands=[]
        class R:returncode=0
        def runner(cmd,**kwargs):commands.append(cmd);return R()
        with patch.object(c.DB.__class__,'is_file',return_value=True),\
             patch.object(c,'certify_calendar',return_value={6:[],7:[],8:[]}):
            c.execute('run',commit=True,now=NOW,runner=runner)
        self.assertEqual(commands[0][-1],'--observe')
        self.assertEqual(commands[1][-1],'--commit')

    def test_failed_result_gate_aborts_forecasts(self):
        class R:returncode=2
        with patch.object(c.DB.__class__,'is_file',return_value=True),\
             patch.object(c,'certify_calendar',side_effect=AssertionError('should not reach calendar')):
            with self.assertRaises(RuntimeError):
                c.execute('run',commit=True,now=NOW,runner=lambda *a,**k:R())

if __name__ == '__main__': unittest.main()
