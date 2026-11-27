import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import football_ucl_shadow_v28 as v28
import football_forward_journal_v14 as journal

UTC = timezone.utc


class V28Tests(unittest.TestCase):
    def test_clock_is_60_hours_before_earliest_kickoff(self):
        t = datetime(2026, 12, 8, 17, 45, tzinfo=UTC)
        now = v28.clock_for_day([(t, 'A', 'B'), (t + timedelta(hours=2, minutes=15), 'C', 'D')])
        self.assertEqual(t-now, timedelta(hours=60))

    def test_clock_rejects_spread_exceeding_window(self):
        t = datetime(2026, 12, 8, 17, 45, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, '48-72h'):
            v28.clock_for_day([(t,'A','B'), (t + timedelta(hours=13),'C','D')])

    def test_round6_grouped_and_count_checked(self):
        t = datetime(2026, 12, 8, 20, tzinfo=UTC)
        games = [(t+timedelta(days=(i//9)),f'H{i}',f'A{i}') for i in range(18)]
        dates = v28.select_round6({6: games})
        self.assertEqual([len(x) for x in dates.values()], [9,9])
        with self.assertRaisesRegex(ValueError, '18'):
            v28.select_round6({6: games[:-1]})

    def test_sqlite_backup_preserves_original_and_never_writes_live(self):
        with tempfile.TemporaryDirectory() as d:
            live, clone = Path(d)/'live.db', Path(d)/'shadow.db'
            with journal.open_db(live) as conn:
                conn.execute('INSERT INTO fixtures(competition,kickoff_utc,home,away,fixture_source,registered_utc) VALUES(?,?,?,?,?,?)',
                             ('UCL','2026-11-24T20:00:00+00:00','A','B','test','2026-11-22T00:00:00+00:00'))
                conn.commit()
            before=v28.snapshot(live)
            v28.backup(live, clone)
            self.assertEqual(v28.snapshot(clone),before)
            with sqlite3.connect(clone) as conn:
                conn.execute("INSERT INTO results VALUES(1,2,0,'SYNTHETIC','2026-11-25T09:00:00+00:00')")
            self.assertEqual(v28.snapshot(live),before)
            self.assertEqual(v28.snapshot(clone)['results'][0],1)

    def test_synthetic_result_rejected_when_real_result_exists(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'shadow.db'
            t=datetime(2026,11,24,20,tzinfo=UTC)
            with journal.open_db(db) as conn:
                conn.execute('INSERT INTO fixtures(competition,kickoff_utc,home,away,fixture_source,registered_utc) VALUES(?,?,?,?,?,?)',
                             ('UCL',t.isoformat(timespec='seconds'),'Home','Away','test','2026-11-22T00:00:00+00:00'))
                conn.execute('INSERT INTO results VALUES(1,1,0,?,?)',('REAL', (t+timedelta(hours=13)).isoformat()))
                conn.commit()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                v28.synthetic_results(db,{5:[(t,'Home','Away')]},{'Home':'Home','Away':'Away'})

    def test_signature_detects_score_dependency(self):
        base=[{'kickoff_utc':'2026-12-08T20:00:00+00:00','home':'A','away':'B','model':'Elo','p_home':'0.5','p_draw':'0.3','p_away':'0.2'}]
        other=[dict(base[0],p_home='0.4',p_away='0.3')]
        self.assertNotEqual(v28.prediction_signature(base),v28.prediction_signature(other))


if __name__ == '__main__':
    unittest.main()
