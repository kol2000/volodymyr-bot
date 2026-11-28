import unittest
from football_ablation_v5 import VARIANTS, metrics

class AblationTests(unittest.TestCase):
    def test_variant_ids_unique(self):
        ids = [k for k, _ in VARIANTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_perfect_prediction_has_best_score(self):
        good = {'V2_DC': (0.8, 0.1, 0.1), 'V1': (0.2, 0.4, 0.4)}
        rows = [('EPL', None, 'H', good)]
        self.assertGreater(metrics(rows, 'V1')[1], metrics(rows, 'V2_DC')[1])
        self.assertEqual(metrics(rows, 'V2_DC')[0], 1)

    def test_empty(self):
        self.assertIsNone(metrics([], 'V2_DC'))

if __name__ == '__main__':
    unittest.main()
