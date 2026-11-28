import sqlite3
import unittest
from datetime import datetime,timezone,timedelta
from football_ucl_results_v16 import parse_page,process,PAIRS

class ResultTests(unittest.TestCase):
    def page(self,first='Bodø/Glimt 2-1 LASK'):
        return ('<html><div>Matchday 4</div><div>Bodø/Glimt 9-9 LASK</div>'
                '<div>Matchday 5</div><div>Tuesday 24 November 2026</div>'
                f'<div>{first}</div><div>Wednesday 25 November 2026</div>'
                '<div>Matchday 6</div></html>').encode()
    def test_round_isolation(self):
        self.assertEqual(parse_page(self.page()),{('2026-11-24','FK Bodø/Glimt','LASK'):(2,1)})
    def test_only_future_no_score(self):
        self.assertEqual(parse_page(self.page('Bodø/Glimt vs LASK (18:45)')), {})
    def test_duplicated_score_rejected(self):
        raw=self.page().replace(b'<div>Wednesday',b'<div>Bod\xc3\xb8/Glimt 2-1 LASK</div><div>Wednesday')
        with self.assertRaises(ValueError):parse_page(raw)
    def test_missing_heading_rejected(self):
        with self.assertRaises(ValueError):parse_page(b'<div>Matchday 5</div>')
    def make_db(self):
        conn=sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE fixtures (id INTEGER PRIMARY KEY, competition TEXT,kickoff_utc TEXT,home TEXT,away TEXT)')
        conn.execute('CREATE TABLE results (fixture_id INTEGER PRIMARY KEY,home_goals INTEGER,away_goals INTEGER,result_source TEXT,recorded_utc TEXT)')
        for i,(_,_,h,a) in enumerate(PAIRS,1):
            date='2026-11-24' if i<=9 else '2026-11-25'
            conn.execute('INSERT INTO fixtures VALUES (?,?,?,?,?)',(i,'UCL',date+'T17:45:00+00:00',h,a))
        return conn
    def test_two_observations_needed(self):
        conn=self.make_db()
        now=datetime(2026,11,25,12,tzinfo=timezone.utc)
        cache,status,ready,n=process(conn,self.page(),{},now,observe=True)
        self.assertEqual(ready,0)
        cache,status,ready,n=process(conn,self.page(),cache,now+timedelta(minutes=31),commit=True)
        self.assertEqual(ready,1)
        self.assertEqual(conn.execute('SELECT home_goals,away_goals FROM results').fetchone(),(2,1))
    def test_mismatch_blocks_commit(self):
        conn=self.make_db()
        conn.execute("UPDATE fixtures SET home='WRONG' WHERE id=1")
        with self.assertRaises(ValueError):
            process(conn,self.page(),{},datetime(2026,11,25,12,tzinfo=timezone.utc),commit=True)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM results').fetchone()[0],0)

if __name__=='__main__':unittest.main()
