import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import football_ucl_results_v16 as v16
import football_ucl_results_v20 as v20

UTC=timezone.utc
NOW=datetime(2026,11,27,12,0,tzinfo=UTC)


def espn_event(home='Bodø/Glimt',away='LASK',score=(2,1),completed=True, name='STATUS_FULL_TIME', time='2026-11-24T17:45:00Z'):
    return {
      'id':'abc123', 'date':time,
      'status':{'type':{'name':name,'state':'post' if completed else 'pre','completed':completed}},
      'competitions':[{'competitors':[
        {'homeAway':'home','team':{'displayName':home},'score':str(score[0])},
        {'homeAway':'away','team':{'displayName':away},'score':str(score[1])},
      ]}]
    }


def html(score=(2,1)):
    lines=['Matchday 5','Tuesday 24 November 2026',f'Bodø/Glimt {score[0]}-{score[1]} LASK',
           'Wednesday 25 November 2026','Matchday 6']
    return ('<html>'+''.join('<p>'+x+'</p>' for x in lines)+'</html>').encode()


def db():
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE fixtures (id INTEGER PRIMARY KEY,competition TEXT,kickoff_utc TEXT,home TEXT,away TEXT)')
    c.execute('CREATE TABLE results (fixture_id INTEGER PRIMARY KEY,home_goals INTEGER,away_goals INTEGER)')
    # Keep the 18 identities exactly as in V16 (as required by the production verifier).
    for i,(_,_,home,away) in enumerate(v16.PAIRS,1):
        day='2026-11-25' if i in (10,11,12,13,14,15,16,17,18) else '2026-11-24'
        clock='17:45:00' if i in (1,2,10,11) else '20:00:00'
        c.execute('INSERT INTO fixtures VALUES(?,?,?,?,?)',(i,'UCL',f'{day}T{clock}+00:00',home,away))
    return c


class V20Tests(unittest.TestCase):
    def test_alias_uniqueness(self):
        index=v20.alias_index()
        self.assertEqual(index['bodo/glimt'],'Bodø/Glimt')
        with self.assertRaises(ValueError):
            v20.alias_index({'Bodø/Glimt':['LASK']})

    def test_espn_requires_final(self):
        self.assertEqual(len(v20.parse_espn([{'events':[espn_event()]}])),1)
        self.assertEqual(len(v20.parse_espn([{'events':[espn_event(completed=False)]}])),0)
        self.assertEqual(len(v20.parse_espn([{'events':[espn_event(name='STATUS_POSTPONED')]}])),0)

    def test_espn_unknown_alias_fails_closed(self):
        self.assertEqual(v20.parse_espn([{'events':[espn_event(home='Unknown')]}]),{})

    def test_score_required_and_mismatch(self):
        c=db()
        e=v20.parse_espn([{'events':[espn_event(score=(3,1))]}])
        obs={'2026-11-24|FK Bodø/Glimt|LASK': {'score':[2,1], 'first_seen_utc':(NOW-timedelta(hours=1)).isoformat()}}
        ready,messages=v20.check(c,html(),obs,e,NOW)
        self.assertEqual(ready,[])
        self.assertIn('BLOCK score mismatch',' '.join(messages))

    def test_ready_requires_observation_stability(self):
        c=db()
        e=v20.parse_espn([{'events':[espn_event()]}])
        obs={'2026-11-24|FK Bodø/Glimt|LASK': {'score':[2,1], 'first_seen_utc':(NOW-timedelta(minutes=10)).isoformat()}}
        ready,messages=v20.check(c,html(),obs,e,NOW)
        self.assertEqual(ready,[])
        obs['2026-11-24|FK Bodø/Glimt|LASK']['first_seen_utc']=(NOW-timedelta(hours=1)).isoformat()
        ready,messages=v20.check(c,html(),obs,e,NOW)
        self.assertEqual(ready,[(1,2,1,'abc123')])
        self.assertIn('READY',' '.join(messages))

    def test_wrong_kickoff_blocks(self):
        c=db()
        e=v20.parse_espn([{'events':[espn_event(time='2026-11-24T20:00:00Z')]}])
        obs={'2026-11-24|FK Bodø/Glimt|LASK': {'score':[2,1], 'first_seen_utc':(NOW-timedelta(hours=1)).isoformat()}}
        ready,messages=v20.check(c,html(),obs,e,NOW)
        self.assertEqual(ready,[])
        self.assertIn('BLOCK ESPN kickoff mismatch',' '.join(messages))

if __name__=='__main__':unittest.main()
