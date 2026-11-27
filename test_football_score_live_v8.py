import unittest
from datetime import datetime, timezone
from football_score_live_v8 import score_rows

class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,24,tzinfo=timezone.utc)
        self.key=('EPL','2026-11-22','Liverpool','Man United')
        self.row=(*self.key,'V3','2026-11-22T16:30:00+00:00','2026-11-22T01:46:00+00:00',0.4,0.3,0.3)
    def test_score(self):
        scored,pending,invalid=score_rows([self.row],{self.key:('H',2,1)},self.now)
        self.assertEqual(len(scored),1)
        self.assertEqual(scored[0]['accuracy'],1)
        self.assertAlmostEqual(scored[0]['logloss'],0.9162907319)
        self.assertFalse(pending)
        self.assertFalse(invalid)
    def test_no_result_pending(self):
        scored,pending,invalid=score_rows([self.row],{},self.now)
        self.assertFalse(scored)
        self.assertEqual(pending['нет итогового счёта в локальных CSV'],1)
        self.assertFalse(invalid)
    def test_late_prediction_rejected(self):
        row=list(self.row)
        row[6]='2026-11-22T17:00:00+00:00'
        scored,pending,invalid=score_rows([tuple(row)],{self.key:('H',2,1)},self.now)
        self.assertFalse(scored)
        self.assertEqual(len(invalid),1)
    def test_too_early_to_score(self):
        now=datetime(2026,11,22,17,0,tzinfo=timezone.utc)
        scored,pending,invalid=score_rows([self.row],{self.key:('H',2,1)},now)
        self.assertFalse(scored)
        self.assertEqual(sum(pending.values()),1)

if __name__=='__main__': unittest.main()
