import json
import sqlite3
import tempfile
import unittest
from datetime import date,datetime,timedelta
from pathlib import Path
import chat_features as f


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
          INSERT INTO settings VALUES ('chat_id','-100123');
          CREATE TABLE messages(chat_id INTEGER,message_id INTEGER,timestamp REAL,speaker TEXT,text TEXT,human INTEGER,user_id INTEGER,reply_to INTEGER,body TEXT);
          CREATE TABLE salary_votes(chat_id INTEGER,message_id INTEGER,user_id INTEGER,direction INTEGER,timestamp REAL);''')
        f.install_archive(self.db)
        self.start=datetime(2026,11,22,21,tzinfo=f.MOSCOW);self.end=self.start+timedelta(days=7)
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)

    def tearDown(self):self.db.close();self.temp.cleanup()

    def add(self,mid,text='это интересная длинная цитата участника',user=1,parent=None,human=1,chat=-100123,stamp=None):
        self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',(chat,mid,stamp or self.start.timestamp()+mid,'User'+str(user),text,human,user,parent,text))

    def test_archive_survives_retention_and_scopes_chat(self):
        self.add(1);self.add(2,chat=77)
        self.db.execute('DELETE FROM messages')
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_archive').fetchone()[0],1)
        f.install_archive(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_archive').fetchone()[0],1)

    def test_edits(self):
        self.add(1)
        self.db.execute("UPDATE messages SET body='изменено' WHERE message_id=1")
        self.assertEqual(self.db.execute('SELECT text FROM chat_archive').fetchone()[0],'изменено')

    def test_evidence_awards_and_salary_not_changed(self):
        self.add(1,'какой прекрасный футбольный вечер')
        self.add(2,'ахах какой прекрасный футбольный вечер',user=2,parent=1)
        self.add(3,'какой прекрасный футбольный вечер',parent=2)
        self.add(4,'вот мой смешной ответ',human=0)
        self.db.execute('INSERT INTO salary_votes VALUES (?,?,?,?,?)',(-100123,4,2,1,self.start.timestamp()+9))
        text=f.awards(self.db,-100123,self.start,self.end,model=lambda rows:{'quote':1,'funny':999},folder=self.folder)
        self.assertIn('User1',text);self.assertIn('3 сообщений в цепочке',text)
        self.assertIn('+80 грн',text);self.assertIn('https://t.me/c/123/1',text)
        self.assertNotIn('999',text)
        self.assertEqual(self.db.execute('SELECT count(*) FROM salary_votes').fetchone()[0],1)

    def test_empty_and_week_boundary(self):
        self.add(1,stamp=self.end.timestamp())
        self.assertIsNone(f.awards(self.db,-100123,self.start,self.end,folder=self.folder))

    def test_model_cannot_invent_quotes(self):
        rows=[dict(message_id=1,text='интересная настоящая цитата',human=1)]
        self.assertEqual(f.editorial(rows,lambda _:dict(funny=999,quote='made up')), {})
        self.assertEqual(f.editorial(rows,lambda _:'bad json'),{})

    def test_memory_exact_date_year_and_escaping(self):
        self.add(1,'старое воспоминание <b>без исполнения html</b>',stamp=datetime(2025,11,28,12,tzinfo=f.MOSCOW).timestamp())
        corpus=self.folder/'examples.jsonl'
        corpus.write_text(json.dumps(dict(date='2024-11-28T12:00:00',text='старинная настоящая реплика Бубуса'))+'\n')
        result=f.memory(self.db,-100123,date(2026,11,28),corpus)
        self.assertIn('28.11.2025',result);self.assertIn('&lt;b&gt;',result)
        self.assertIsNone(f.memory(self.db,-100123,date(2026,11,29),corpus))
        self.assertIsNone(f.memory(self.db,44,date(2026,11,28),self.folder/'missing'))

    def test_leap_day_no_substitution(self):
        corpus=self.folder/'examples.jsonl'
        corpus.write_text(json.dumps(dict(date='2024-02-29T12:00:00',text='високосная старая реплика Бубуса'))+'\n')
        self.assertIsNone(f.memory(self.db,-100123,date(2027,2,28),corpus))
        self.assertIsNotNone(f.memory(self.db,-100123,date(2028,2,29),corpus))


if __name__=='__main__':unittest.main()
