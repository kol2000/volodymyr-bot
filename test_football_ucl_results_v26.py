import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import football_forward_journal_v14 as journal
import football_ucl_results_v22 as v22
import football_ucl_results_v26 as v26

UTC = timezone.utc
KICKOFF = datetime(2026, 11, 24, 17, 45, tzinfo=UTC)
T0 = KICKOFF + timedelta(hours=13)
TOKEN = '2026-11-24|FK Bodø/Glimt|LASK'
KEY = (5, '2026-11-24', 'Bodø/Glimt', 'LASK')
ESPN_KEY = ('2026-11-24', 'Bodø/Glimt', 'LASK')
FIXTURE = (1, 5, KICKOFF, 'FK Bodø/Glimt', 'LASK', 'Bodø/Glimt', 'LASK', False)


def sources(score=(2, 1)):
    return {KEY: score}, {ESPN_KEY: (KICKOFF, score, 'test-event')}


class V26Tests(unittest.TestCase):
    def setUp(self):
        self.pub, self.espn = sources()

    def evaluate(self, obs=None, now=T0, pub=None, espn=None, fixture=FIXTURE):
        return v26.evaluate([fixture],
                            self.pub if pub is None else pub,
                            self.espn if espn is None else espn,
                            {} if obs is None else obs, now)

    def test_first_observation_never_ready(self):
        ready, obs, _ = self.evaluate()
        self.assertFalse(ready)
        self.assertEqual(obs[TOKEN]['score'], [2, 1])

    def test_29_minutes_not_ready_30_minutes_ready(self):
        _, obs, _ = self.evaluate()
        self.assertFalse(self.evaluate(obs, T0 + timedelta(minutes=29))[0])
        self.assertEqual(self.evaluate(obs, T0 + timedelta(minutes=30))[0],
                         [(1, 2, 1, 'test-event', 5)])

    def test_changed_score_resets_timer(self):
        _, obs, _ = self.evaluate()
        changed_pub, changed_espn = sources((3, 1))
        ready, changed, _ = self.evaluate(obs, T0 + timedelta(minutes=20), changed_pub, changed_espn)
        self.assertFalse(ready)
        self.assertEqual(changed[TOKEN]['score'], [3, 1])
        self.assertEqual(changed[TOKEN]['first_seen_utc'], (T0 + timedelta(minutes=20)).isoformat())
        self.assertFalse(self.evaluate(changed, T0 + timedelta(minutes=31), changed_pub, changed_espn)[0])

    def test_espn_disappearance_resets_timer(self):
        _, obs, _ = self.evaluate()
        ready, cleared, msg = self.evaluate(obs, T0 + timedelta(minutes=20), espn={})
        self.assertFalse(ready)
        self.assertNotIn(TOKEN, cleared)
        self.assertTrue(any('RESET' in x for x in msg))
        self.assertFalse(self.evaluate(cleared, T0 + timedelta(minutes=35))[0])

    def test_uefa_disappearance_resets_timer(self):
        _, obs, _ = self.evaluate()
        ready, cleared, _ = self.evaluate(obs, T0 + timedelta(minutes=15), pub={})
        self.assertFalse(ready)
        self.assertNotIn(TOKEN, cleared)
        self.assertFalse(self.evaluate(cleared, T0 + timedelta(minutes=31))[0])

    def test_score_disagreement_resets_timer(self):
        _, obs, _ = self.evaluate()
        _, mismatched, _ = self.evaluate(obs, T0 + timedelta(minutes=20), espn=sources((9, 1))[1])
        self.assertNotIn(TOKEN, mismatched)
        self.assertFalse(self.evaluate(mismatched, T0 + timedelta(minutes=40))[0])

    def test_wrong_kickoff_resets_timer(self):
        _, obs, _ = self.evaluate()
        bad = {ESPN_KEY: (KICKOFF + timedelta(hours=2), (2, 1), 'test-event')}
        _, cleared, _ = self.evaluate(obs, T0 + timedelta(minutes=20), espn=bad)
        self.assertNotIn(TOKEN, cleared)

    def test_completed_fixture_discards_observation(self):
        _, obs, _ = self.evaluate()
        completed = FIXTURE[:-1] + (True,)
        self.assertFalse(self.evaluate(obs, T0 + timedelta(minutes=31), fixture=completed)[0])
        self.assertNotIn(TOKEN, self.evaluate(obs, T0 + timedelta(minutes=31), fixture=completed)[1])

    def test_atomic_commit_and_retry_on_real_v22_writer(self):
        with tempfile.TemporaryDirectory(prefix='bubus_v26_test_') as tmp:
            dbpath = Path(tmp) / 'test.sqlite3'
            c = journal.open_db(dbpath)
            c.execute('''INSERT INTO fixtures(competition,kickoff_utc,home,away,fixture_source,registered_utc)
                         VALUES(?,?,?,?,?,?)''',
                      ('UCL', KICKOFF.isoformat(timespec='seconds'), 'FK Bodø/Glimt', 'LASK',
                       'UEFA test fixture', '2026-11-22T00:00:00+00:00'))
            c.execute('''INSERT INTO predictions(fixture_id,model,version,p_home,p_draw,p_away,
                         prediction_source,source_csv_sha256,recorded_utc)
                         VALUES(?,?,?,?,?,?,?,?,?)''',
                      (1, 'Elo', 'test', .4, .3, .3, 'test', 'testhash', '2026-11-22T00:00:00+00:00'))
            c.commit()
            _, obs, _ = self.evaluate()
            ready, _, _ = self.evaluate(obs, T0 + timedelta(minutes=31))
            self.assertEqual(v22.commit_results(dbpath, ready, T0 + timedelta(minutes=31),
                                               'html-test', ['espn-test']), 1)
            self.assertEqual(v22.commit_results(dbpath, ready, T0 + timedelta(minutes=32),
                                               'html-test', ['espn-test']), 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM results').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], 1)
            c.close()

    def test_production_v22_has_gap_regression(self):
        # This is a deliberately isolated regression demonstration: unmodified
        # V22 retains the old timestamp when ESPN disappears. V26 fixes that.
        _, obs, _ = self.evaluate()
        _, old, _ = v22.evaluate([FIXTURE], self.pub, {}, obs, T0 + timedelta(minutes=20))
        self.assertIn(TOKEN, old)
        _, new, _ = self.evaluate(obs, T0 + timedelta(minutes=20), espn={})
        self.assertNotIn(TOKEN, new)


if __name__ == '__main__':
    unittest.main()
