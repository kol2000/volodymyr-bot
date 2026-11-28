import tempfile
import csv
import unittest
from pathlib import Path
from football_ucl_hybrid_v13 import read_v12,predict,score,compute,summarize,HYBRIDS
class HybridTests(unittest.TestCase):
    def test_fallback(self):
        game={'Elo':((.4,.25,.35),'A')}
        self.assertEqual(predict(game,'V3',1.0),((.4,.25,.35),'Elo'))
    def test_blend(self):
        game={'Elo':((.4,.3,.3),'H'),'V3':((.6,.2,.2),'H')}
        self.assertEqual(predict(game,'V3',.5)[0],(.5,.25,.25))
    def test_all_coverage(self):
        games={('2026/27','2026-11-04','A','B'):{'Elo':((.4,.3,.3),'H')},
               ('2026/27','2026-11-05','C','D'):{'Elo':((.3,.3,.4),'A'),'V3':((.3,.2,.5),'A')}}
        details=compute(games)
        self.assertEqual(len(details),2*len(HYBRIDS))
        self.assertTrue(all(r['matches']==2 for r in summarize(details)))
    def test_conflicting_outcome_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'p.csv'
            cols=['tournament','season','date','home','away','model','actual','p_home','p_draw','p_away']
            with p.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
                common=dict(tournament='UCL',season='2026/27',date='2026-11-01',home='A',away='B',p_home=.4,p_draw=.3,p_away=.3)
                w.writerow(dict(common,model='Elo',actual='H'))
                w.writerow(dict(common,model='V3',actual='A'))
            with self.assertRaises(ValueError):read_v12(p)
    def test_scoring(self):
        acc,ll,brier=score((.8,.1,.1),'H')
        self.assertEqual(acc,1)
        self.assertGreater(ll,0)
        self.assertAlmostEqual(brier,.06)
if __name__=='__main__':unittest.main()
