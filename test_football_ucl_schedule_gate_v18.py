import tempfile
import unittest
from pathlib import Path
import football_ucl_schedule_gate_v18 as v18

class V18Tests(unittest.TestCase):
    def test_prose_not_a_match(self):
        lines = ['Matchday 6', 'Wednesday 9 December 2026',
                 'Paris vs Aston Villa: Their 2025 Champions League quarter-final',
                 'Paris Saint-Germain vs Aston Villa', '(21:00)']
        got=v18.extract_strict(lines)
        self.assertEqual(len(got),1)
        self.assertEqual(next(iter(got.values())),'21:00')
    def test_explicit_and_unknown_time(self):
        lines=['Matchday 6','Tuesday 8 December 2026',
               'Viking vs Feyenoord','(18:45)',
               'Villarreal vs Sabah']
        got=v18.extract_strict(lines)
        self.assertEqual(len(got),2)
        self.assertEqual(got[(6,'2026-12-08','Viking','Feyenoord')],'18:45')
        self.assertIsNone(got[(6,'2026-12-08','Villarreal','Sabah')])
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            v18.extract_strict(['Matchday 6','Tuesday 8 December 2026','Viking vs Feyenoord','Viking vs Feyenoord'])
    def test_weekday_rejected(self):
        with self.assertRaises(ValueError):
            v18.extract_strict(['Matchday 6','Monday 8 December 2026','Viking vs Feyenoord'])
    def test_csv_conflicting_time_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'test.csv'
            path.write_text('gw,date,time,home,away\n6,2026-12-08,21:00,Viking,Feyenoord\n',encoding='utf-8')
            k=(6,'2026-12-08','Viking','Feyenoord')
            checked,problems=v18.check_snapshot(path,{k:'18:45'},'2026-11-22')
            self.assertEqual(len(checked),1)
            self.assertTrue(any('TIME CONFLICT' in p for p in problems))

if __name__=='__main__': unittest.main()
