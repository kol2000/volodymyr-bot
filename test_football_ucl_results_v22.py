import sqlite3
import tempfile
import csv
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone

import football_ucl_results_v22 as v22

UTC=timezone.utc
NOW=datetime(2026,12,11,12,0,tzinfo=UTC)


def fixture(fid=19, rnd=6, kickoff=None, done=False):
    kickoff = kickoff or datetime(2026,12,8,17,45,tzinfo=UTC)
    return (fid,rnd,kickoff,'Viking','Feyenoord Rotterdam','Viking','Feyenoord',done)


def page(lines):
    return ('<html>'+''.join('<p>'+l+'</p>' for l in lines)+'</html>').encode()


def espn(score=(2,1), kickoff=None, pair=('Viking','Feyenoord')):
    kickoff=kickoff or datetime(2026,12,8,17,45,tzinfo=UTC)
    return {(kickoff.date().isoformat(),*pair):(kickoff,score,'example-id')}


class V22Tests(unittest.TestCase):
    def test_round6_result_accepted_exact_only(self):
        data=page(['Matchday 6','Tuesday 8 December 2026','Viking 2-1 Feyenoord',
                   'Matchday 7', 'Tuesday 19 January 2027'])
        keys={(6,'2026-12-08','Viking','Feyenoord')}
        self.assertEqual(v22.official_scores(data,keys),{(6,'2026-12-08','Viking','Feyenoord'):(2,1)})

    def test_wrong_round_not_accepted(self):
        data=page(['Matchday 5','Tuesday 8 December 2026','Viking 2-1 Feyenoord','Matchday 6'])
        self.assertEqual(v22.official_scores(data,{(6,'2026-12-08','Viking','Feyenoord')}),{})

    def test_article_prose_not_accepted(self):
        data=page(['Matchday 6','Tuesday 8 December 2026',
                   'Viking 2-1 Feyenoord: a dramatic night', 'Viking 2-1 Feyenoord'])
        self.assertEqual(len(v22.official_scores(data,{(6,'2026-12-08','Viking','Feyenoord')})),1)

    def test_observation_and_stability(self):
        pub={(6,'2026-12-08','Viking','Feyenoord'):(2,1)}
        fixtures=[fixture()]
        first,obs,msg=v22.evaluate(fixtures,pub,espn(),{},NOW)
        self.assertEqual(first,[])
        self.assertEqual(obs['2026-12-08|Viking|Feyenoord Rotterdam']['score'],[2,1])
        second,_,_=v22.evaluate(fixtures,pub,espn(),obs,NOW+timedelta(minutes=31))
        self.assertEqual(second,[(19,2,1,'example-id',6)])

    def test_mismatch_blocks_and_resets(self):
        pub={(6,'2026-12-08','Viking','Feyenoord'):(2,1)}
        old={'2026-12-08|Viking|Feyenoord Rotterdam':{
            'score':[1,1], 'first_seen_utc':(NOW-timedelta(hours=2)).isoformat()}}
        ready,obs,_=v22.evaluate([fixture()],pub,espn(score=(3,1)),old,NOW)
        self.assertEqual(ready,[])
        # Conflicting providers must not overwrite the observation.
        self.assertEqual(obs,old)
        ready,obs,_=v22.evaluate([fixture()],pub,espn(),old,NOW)
        self.assertEqual(ready,[])
        self.assertEqual(obs['2026-12-08|Viking|Feyenoord Rotterdam']['first_seen_utc'],NOW.isoformat())

    def test_too_early_and_done_block(self):
        pub={(6,'2026-12-08','Viking','Feyenoord'):(2,1)}
        before=datetime(2026,12,8,20,0,tzinfo=UTC)
        self.assertFalse(v22.evaluate([fixture()],pub,espn(),{},before)[0])
        self.assertFalse(v22.evaluate([fixture(done=True)],pub,espn(),{},NOW)[0])

    def test_wrong_kickoff_blocks(self):
        pub={(6,'2026-12-08','Viking','Feyenoord'):(2,1)}
        e=espn(kickoff=datetime(2026,12,8,20,0,tzinfo=UTC))
        ready,_,_=v22.evaluate([fixture()],pub,e,{},NOW)
        self.assertEqual(ready,[])

    def test_calendar_still_certified_after_score_replaces_vs(self):
        """Regression: V19 future-only parser misses completed fixtures."""
        known = [('Arsenal','Arsenal'), ('Barcelona','Barcelona')]
        from football_ucl_schedule_audit_v17 import NAMES
        from datetime import date
        # 18 unique fixture pairs per round; scores replace one round-5 fixture.
        names = list(NAMES)
        self.assertGreaterEqual(len(names), 20)
        days = {5:'2026-11-24',6:'2026-12-08',7:'2027-01-19',8:'2027-01-27'}
        lines=['Kick-offs are 21:00 unless stated; all times CET.']
        records=[]
        for rnd, day in days.items():
            d=date.fromisoformat(day)
            lines.extend([f'Matchday {rnd}',d.strftime('%A %-d %B %Y')])
            for i in range(18):
                home, away = names[i], names[(i+18)%len(names)]
                # For rare overlap in the small fixture pool, choose another club.
                if home==away: away=names[-1]
                h, a = NAMES[home], NAMES[away]
                if rnd==5 and i==0:
                    lines.append(f'{h} 2-1 {a}')
                else:
                    lines.append(f'{h} vs {a}')
                records.append({'gw':rnd,'date':day,'time':'21:00','home':home,'away':away})
        with tempfile.TemporaryDirectory() as td:
            csv_path=Path(td)/'fixtures.csv'
            with csv_path.open('w',newline='',encoding='utf-8') as out:
                writer=csv.DictWriter(out,fieldnames=['gw','date','time','home','away'])
                writer.writeheader();writer.writerows(records)
            cal,scored=v22.verified_calendar(page(lines),csv_path)
        self.assertTrue(all(len(cal[r])==18 for r in range(5,9)))
        self.assertEqual(len(scored),1)

    def test_registered_fixtures_must_be_in_schedule(self):
        c=sqlite3.connect(':memory:')
        c.execute('CREATE TABLE fixtures(id INTEGER,competition TEXT,kickoff_utc TEXT,home TEXT,away TEXT,fixture_source TEXT)')
        c.execute('CREATE TABLE results(fixture_id INTEGER)')
        c.execute("INSERT INTO fixtures VALUES(19,'UCL','2026-12-08T17:45:00+00:00','WRONG','Feyenoord Rotterdam','UEFA test')")
        calendar={i:[] for i in range(5,9)}
        calendar[6]=[(datetime(2026,12,8,17,45,tzinfo=UTC),'Viking','Feyenoord')]
        with self.assertRaisesRegex(ValueError,'UNVERIFIED'):
            v22.registered_fixtures(c,calendar,{'Viking':'Viking','Feyenoord':'Feyenoord Rotterdam'})


if __name__=='__main__': unittest.main()
