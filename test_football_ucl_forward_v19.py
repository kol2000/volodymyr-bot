import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import football_ucl_forward_v19 as v19

class V19Tests(unittest.TestCase):
    def test_mandatory_default_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            html=Path(directory)/'uefa.html'
            csv=Path(directory)/'games.csv'
            html.write_bytes(b'<html>Matchday 6</html>'*1000)
            csv.write_text('gw,date,time,home,away\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'generic 21:00 CET rule missing'):
                v19.certified_calendar(html,csv,6)

    def test_prior_round_results_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            con=sqlite3.connect(':memory:')
            con.row_factory=sqlite3.Row
            con.execute('CREATE TABLE fixtures(id INTEGER PRIMARY KEY,competition TEXT,kickoff_utc TEXT,home TEXT,away TEXT)')
            con.execute('CREATE TABLE results(fixture_id INTEGER,home_goals INTEGER,away_goals INTEGER,recorded_utc TEXT,result_source TEXT)')
            t=datetime(2026,11,24,20,tzinfo=timezone.utc)
            cal={5:[(t,'Arsenal','LASK')]}
            with self.assertRaisesRegex(ValueError,'missing V14 result'):
                v19.completed_prior_rounds(con,cal,6,{'Arsenal':'Arsenal FC','LASK':None},t+timedelta(days=3))

    def test_result_keeps_score_and_time(self):
        con=sqlite3.connect(':memory:'); con.row_factory=sqlite3.Row
        con.execute('CREATE TABLE fixtures(id INTEGER PRIMARY KEY,competition TEXT,kickoff_utc TEXT,home TEXT,away TEXT)')
        con.execute('CREATE TABLE results(fixture_id INTEGER,home_goals INTEGER,away_goals INTEGER,recorded_utc TEXT,result_source TEXT)')
        t=datetime(2026,11,24,20,tzinfo=timezone.utc)
        con.execute("INSERT INTO fixtures VALUES(1,'UCL',?,?,?)",(t.isoformat(timespec='seconds'),'Arsenal FC','LASK'))
        con.execute("INSERT INTO results VALUES(1,3,2,?,?)",((t+timedelta(days=1)).isoformat(),'verified manual'))
        games=v19.completed_prior_rounds(con,{5:[(t,'Arsenal','LASK')]},6,{'Arsenal':'Arsenal FC','LASK':None},t+timedelta(days=3))
        self.assertEqual((games[0].home,games[0].gh,games[0].ga),('Arsenal FC',3,2))

if __name__=='__main__':unittest.main()
