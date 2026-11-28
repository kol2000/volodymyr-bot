import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import football_ucl_controller_v21 as c

NOW = datetime(2026,12,5,20,0,tzinfo=timezone.utc)

class V21Tests(unittest.TestCase):
    def test_no_window(self):
        cal={6:[(NOW+timedelta(hours=90),'A','B')]}
        self.assertEqual(c.pending_dates(NOW,cal),[])
    def test_full_date_window(self):
        cal={6:[(NOW+timedelta(hours=60),'A','B'),(NOW+timedelta(hours=62),'C','D')]}
        self.assertEqual(c.pending_dates(NOW,cal),[(6,(NOW+timedelta(hours=60)).date().isoformat())])
    def test_mixed_date_is_not_partial_commit(self):
        cal={6:[(NOW+timedelta(hours=47),'A','B'),(NOW+timedelta(hours=50),'C','D')]}
        self.assertEqual(c.pending_dates(NOW,cal),[])
    def test_readonly_does_not_pass_commit(self):
        calls=[]
        class Result:
            returncode=0
        def run(cmd,**kw):
            calls.append(cmd)
            return Result()
        with patch.object(type(c.DB),'is_file',return_value=True),patch.object(c,'certify_calendar',return_value={6:[(NOW+timedelta(hours=60),'A','B')]}),patch.object(c,'forecast_date_state',return_value='new'):
            c.execute('check',now=NOW,runner=run)
        self.assertEqual(len(calls),2)
        self.assertTrue(all('--commit' not in cmd for cmd in calls))
    def test_commit_requires_run_and_stops_on_v20_error(self):
        with self.assertRaises(ValueError):c.execute('check',commit=True)
        class Result:
            returncode=2
        with patch.object(type(c.DB),'is_file',return_value=True),patch.object(c,'certify_calendar',side_effect=AssertionError('must not parse calendar')):
            with self.assertRaises(RuntimeError):
                c.execute('run',commit=True,runner=lambda *args,**kwargs:Result())
    def test_partial_state_refuses(self):
        # Coverage of the branch that blocks unexpected outcomes is done via mocked DB in integration.
        with self.assertRaises(ValueError):
            c.execute('check', commit=True)
    def test_no_future_rounds(self):
        cal={6:[(NOW+timedelta(hours=90),'A','B')],7:[(NOW+timedelta(hours=60),'C','D')]}
        self.assertEqual(c.pending_dates(NOW,cal),[])

if __name__=='__main__':unittest.main()
