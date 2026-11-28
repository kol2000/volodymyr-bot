import csv
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import football_forward_journal_v14 as m

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn=m.open_db(Path(self.tmp.name)/'journal.sqlite3')
        self.addCleanup(self.conn.close)
        self.now=m.utc_now()
        self.row=dict(competition='UCL',kickoff_utc=(self.now+timedelta(days=2)).isoformat(),
                      home='Club A',away='Club B',model='Poisson12_or_Elo',version='v13',
                      p_home='0.5',p_draw='0.25',p_away='0.25',fixture_source='official fixture page checked manually',
                      prediction_source='forward generator output')
    def test_register_and_immutable(self):
        with self.conn: fid=m.register(self.conn,self.row,self.now)
        self.assertEqual(fid,1)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:self.conn.execute('UPDATE predictions SET p_home=.1')
    def test_duplicate_rejected(self):
        with self.conn:m.register(self.conn,self.row,self.now)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:m.register(self.conn,self.row,self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM predictions').fetchone()[0],1)
    def test_timestamp_reject(self):
        self.row['kickoff_utc']='2026-12-01T20:00:00'
        with self.assertRaises(ValueError):m.validate(self.row,self.now)
    def test_past_reject(self):
        self.row['kickoff_utc']=(self.now-timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):m.validate(self.row,self.now)
    def test_probabilities_reject(self):
        self.row['p_home']='nan'
        with self.assertRaises(ValueError):m.validate(self.row,self.now)
    def test_bad_source_reject(self):
        with self.conn:m.register(self.conn,self.row,self.now)
        row=dict(self.row,model='Elo',fixture_source='different source')
        with self.assertRaises(ValueError):
            with self.conn:m.register(self.conn,row,self.now)
    def test_immutability_fixtures(self):
        with self.conn:m.register(self.conn,self.row,self.now)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:self.conn.execute('UPDATE fixtures SET home=home')
