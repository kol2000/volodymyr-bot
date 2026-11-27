import tempfile
import unittest
from pathlib import Path
from football_ucl_schedule_audit_v17 import extract,load_csv

class V17Tests(unittest.TestCase):
    def test_fixture_extraction(self):
        lines=['Matchday 6','Tuesday 8 December 2026','Viking vs Feyenoord','(18:45)','Villarreal vs Sabah','Matchday 7']
        found,err=extract(lines)
        self.assertEqual(err,[])
        self.assertIn((6,'2026-12-08','Viking','Feyenoord'),found[6])
        self.assertEqual(len(found[6]),2)
    def test_reject_wrong_weekday(self):
        _,err=extract(['Matchday 6','Wednesday 8 December 2026','Viking vs Feyenoord'])
        self.assertTrue(err)
    def test_known_names(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'fixtures.csv'
            p.write_text('gw,date,time,home,away\n6,2026-12-08,18:45,Viking,Feyenoord\n',encoding='utf-8')
            rows=load_csv(p)
            self.assertEqual(rows[0][0],(6,'2026-12-08','Viking','Feyenoord'))
    def test_unknown_alias_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'fixtures.csv'
            p.write_text('gw,date,time,home,away\n6,2026-12-08,18:45,Unknown,Feyenoord\n',encoding='utf-8')
            with self.assertRaises(ValueError):load_csv(p)

if __name__=='__main__':unittest.main()
