import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from football_mega_backtest_v12 import Match,canonicalize_current_ucl,run_experiment

DAY=dt.date(2026,9,1)
def game(h,a,day=DAY):return Match(day,'UCL',h,a,2,1,'fixture')

class AliasV12Tests(unittest.TestCase):
    def test_alias_joins_and_none_retained(self):
        historical=[game('Arsenal FC','Borussia Dortmund',dt.date(2025,9,1))]
        current=[game('Arsenal','B. Dortmund'),game('Newcomer','Arsenal')]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'map.json'
            p.write_text(json.dumps({'Arsenal':'Arsenal FC','B. Dortmund':'Borussia Dortmund','Newcomer':None}))
            result=canonicalize_current_ucl(current,historical,p)
        self.assertEqual(result[0].home,'Arsenal FC')
        self.assertEqual(result[0].away,'Borussia Dortmund')
        self.assertEqual(result[1].home,'Newcomer')
        self.assertEqual(result[1].away,'Arsenal FC')
        self.assertEqual(current[0].home,'Arsenal')
    def test_unknown_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'map.json';p.write_text('{"Arsenal":"Arsenal FC"}')
            with self.assertRaisesRegex(ValueError,'missing'):
                canonicalize_current_ucl([game('Arsenal','Another')],[game('Arsenal FC','Other')],p)
    def test_nonhistorical_alias_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'map.json';p.write_text('{"Arsenal":"Wrong FC","Newcomer":null}')
            with self.assertRaisesRegex(ValueError,'Unverified'):
                canonicalize_current_ucl([game('Arsenal','Newcomer')],[game('Arsenal FC','Other')],p)
    def test_collision_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'map.json';p.write_text('{"Arsenal":"Arsenal FC","Arsenal duplicate":"Arsenal FC"}')
            with self.assertRaisesRegex(ValueError,'Ambiguous'):
                canonicalize_current_ucl([game('Arsenal','Arsenal duplicate')],[game('Arsenal FC','Other')],p)
    def test_same_day_no_leak_with_aliases(self):
        old=[game('Arsenal FC','Borussia Dortmund',dt.date(2025,9,1))]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'map.json';p.write_text('{"Arsenal":"Arsenal FC","B. Dortmund":"Borussia Dortmund"}')
            new=canonicalize_current_ucl([game('Arsenal','B. Dortmund')],old,p)
        self.assertEqual(new[0].home,old[0].home)
        self.assertEqual(new[0].away,old[0].away)
if __name__=='__main__':unittest.main()
