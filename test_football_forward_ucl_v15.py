import unittest
from datetime import date, datetime, timezone
from football_mega_backtest_v12 import Match
from football_forward_ucl_v15 import OFFICIAL, ratings_before, make_predictions

class V15Tests(unittest.TestCase):
    def test_schedule_count(self):
        self.assertEqual(sum(map(len, OFFICIAL.values())), 18)
        self.assertEqual(len(OFFICIAL['2026-11-24']),9)
        self.assertEqual(len(OFFICIAL['2026-11-25']),9)

    def test_no_same_date_leak(self):
        a=Match(date(2026,11,24),'UCL','Arsenal','Dortmund',9,0,'test')
        b=Match(date(2026,11,23),'UCL','Arsenal','Dortmund',0,1,'test')
        r1=ratings_before([b],date(2026,11,24))
        r2=ratings_before([b,a],date(2026,11,24))
        self.assertEqual(dict(r1),dict(r2))

    def test_future_forecast_fallback(self):
        kickoff=datetime(2026,11,24,20,0,tzinfo=timezone.utc)
        now=datetime(2026,11,22,1,0,tzinfo=timezone.utc)
        rows=make_predictions([],[(kickoff,'Test home','Test away')],now,'a','b','c')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['model'],'Elo')
        self.assertEqual(rows[1]['model'],'Poisson12_or_Elo')
        self.assertEqual([rows[0][x] for x in ('p_home','p_draw','p_away')],
                         [rows[1][x] for x in ('p_home','p_draw','p_away')])

    def test_expired_skips(self):
        kickoff=datetime(2026,11,24,20,0,tzinfo=timezone.utc)
        rows=make_predictions([],[(kickoff,'Test home','Test away')],kickoff,'a','b','c')
        self.assertEqual(rows,[])

if __name__=='__main__':unittest.main()
