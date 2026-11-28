import unittest
import tempfile
from pathlib import Path
import datetime as dt
from football_mega_backtest_v11 import Match, parse_ucl_txt,parse_rus_csv,run_experiment,clean_matches,poisson_probs

class MegaTests(unittest.TestCase):
    def test_ucl(self):
        text='''= UEFA Champions League 2024/25
  Tue Sep 17 2024
    18:45  BSC Young Boys (SUI)    v Aston Villa FC (ENG)     0-3 (0-2)
           Juventus FC (ITA)       v PSV (NED)                3-1 (2-0)
  Wed Jan 22 2025
    21:00  Arsenal FC (ENG)        v Manchester City FC (ENG)  1-0 (0-0)
'''
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'cl.txt';path.write_text(text)
            matches,bad=parse_ucl_txt(path,'2024-25')
            self.assertEqual(len(matches),3)
            self.assertEqual(str(matches[-1].day),'2025-01-22')
            self.assertFalse(bad)
    def test_rus(self):
        text='Country,League,Season,Date,Home,Away,HG,AG\nRussia,Premier League,2025/26,12/10/2025,Zenit,CSKA,1,0\nRussia,Premier League,2025/26,13/10/2025,CSKA,Spartak,2,2\n'
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'r.csv';p.write_text(text)
            self.assertEqual(len(parse_rus_csv(p)),2)
    def test_probs(self):
        p=poisson_probs(1.5,.8)
        self.assertAlmostEqual(sum(p),1,places=9)
        self.assertTrue(all(x>=0 for x in p))
    def test_no_same_day_prediction_leak(self):
        games=[]
        for i in range(110):
            day=dt.date(2024,1,1)+dt.timedelta(days=i*2)
            games.append(Match(day,'RPL','A','B',1,0,'test'))
        rows=run_experiment(games,'RPL',dt.date(2024,2,1),dt.date(2024,9,1))
        self.assertTrue(rows)
        self.assertEqual(len(clean_matches(games)),110)

if __name__=='__main__':unittest.main()
