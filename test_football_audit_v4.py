import unittest
from football_audit_v4 import delta, bootstrap

class AuditTests(unittest.TestCase):
    def test_perfect_improves(self):
        rows=[('EPL','H',(0.2,0.3,0.5),(0.2,0.3,0.5),(0.9,0.05,0.05))]
        ll, acc=delta(rows,4,3)
        self.assertGreater(ll,0)
        self.assertEqual(acc,100)
    def test_deterministic_ci(self):
        rows=[('EPL','H',None,(.5,.3,.2),(.6,.25,.15)),('SerieA','D',None,(.4,.3,.3),(.3,.4,.3))]
        self.assertEqual(bootstrap(rows,4,3,samples=100,seed=2),bootstrap(rows,4,3,samples=100,seed=2))

if __name__=='__main__':unittest.main()
