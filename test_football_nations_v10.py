import unittest
from football_nations_v10 import expected, probabilities, metric, run

class NationsTests(unittest.TestCase):
    def sample(self, date, h, a, hg, ag, ident, neutral=0):
        return {'match_date':date,'home':h,'away':a,'home_goals':hg,'away_goals':ag,
                'competition':'Friendly','neutral':neutral,'id':ident}

    def test_probs(self):
        for neutral in (0,1):
            p=probabilities(1500,1500,neutral)
            self.assertAlmostEqual(sum(p),1)
            self.assertTrue(all(0 < v < 1 for v in p))
        self.assertAlmostEqual(probabilities(1500,1500,1)[0], probabilities(1500,1500,1)[2])

    def test_home_advantage(self):
        self.assertGreater(probabilities(1500,1500,0)[0], probabilities(1500,1500,1)[0])

    def test_no_same_day_leakage(self):
        train=[self.sample('2023-01-01','A','B',2,0,1)]
        batch=[self.sample('2024-01-01','A','B',9,0,2), self.sample('2024-01-01','A','B',0,1,3)]
        r,_,_=run(train+batch,2024,2024)
        self.assertEqual(r[0]['elo'],r[1]['elo'])
        self.assertEqual(len(r),2)

    def test_no_future_leakage(self):
        train=[self.sample('2023-01-01','A','B',2,0,1)]
        target=self.sample('2024-01-01','A','B',1,1,2)
        future=self.sample('2025-01-01','A','B',20,0,3)
        first=run(train+[target],2024,2024)[0][0]['elo']
        second=run(train+[target,future],2024,2024)[0][0]['elo']
        self.assertEqual(first,second)

    def test_metric(self):
        self.assertLess(metric((.8,.1,.1),0)[0], metric((.1,.8,.1),0)[0])

if __name__=='__main__':unittest.main()
