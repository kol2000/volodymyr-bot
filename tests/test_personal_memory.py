import contextlib
import sqlite3
import unittest
from unittest.mock import patch
import personal_memory as m


class Store:
    def __init__(self):self.conn=sqlite3.connect(':memory:');m.init(self)
    @contextlib.contextmanager
    def db(self):
        with self.conn:yield self.conn


class MemoryTests(unittest.TestCase):
    def setUp(self):self.store=Store();self.now=1800000000
    def tearDown(self):self.store.conn.close()
    def act(self,command,value='',user=1,chat=-100123):
        return m.action(self.store,dict(command=command,value=value),chat,user,10,self.now)
    def ctx(self,user=1,chat=-100123,query=''):
        return m.context(self.store,chat,user,query,self.now)

    def test_explicit_opt_out(self):
        self.act('/my_memory','off')
        m.observe(self.store,-100123,1,'я болею за Спартак',1,now=self.now)
        self.assertEqual(self.ctx(),{})
        self.assertIn('сначала',self.act('/remember_me','клуб = Спартак'))
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM personal_facts').fetchone()[0],0)

    def test_automatic_default_and_archive_sources(self):
        db=self.store.conn
        db.execute('CREATE TABLE chat_archive(chat_id INTEGER,user_id INTEGER,message_id INTEGER,text TEXT,timestamp REAL,human INTEGER)')
        for i,text in enumerate(['я болею за Спартак','футбол отличный матч','смотрю футбол матч','футбол отличный матч']):
            db.execute('INSERT INTO chat_archive VALUES (?,?,?,?,?,1)',(-100123,1,i+1,text,self.now-(4-i)*86400))
        db.execute('INSERT INTO chat_archive VALUES (?,?,?,?,?,1)',(-100999,2,8,'я болею за Зенит',self.now-100))
        db.commit()
        result=m.backfill(self.store,-100123,self.now)
        self.assertEqual(result['participants'],1)
        facts={r['category']:r for r in self.ctx()['facts']}
        self.assertEqual(facts['club']['value'],'Спартак')
        self.assertEqual(facts['club']['source_message'],1)
        self.assertIn('футбол',facts['topics']['value'])
        self.assertEqual(facts['club']['certainty'],'archive_observation')
        self.act('/forget_me','клуб')
        m.backfill(self.store,-100123,self.now)
        self.assertFalse(any(r['category']=='club' for r in self.ctx()['facts']))
        self.act('/forget_me','all')
        m.backfill(self.store,-100123,self.now)
        self.assertEqual(self.ctx(),{})

    def test_candidate_confirmation_conflict(self):
        self.act('/my_memory','on')
        m.observe(self.store,-100123,1,'я болею за Спартак',7,now=self.now)
        self.assertEqual(self.ctx()['facts'][0]['certainty'],'archive_observation')
        self.assertIn('наблюдение из твоих сообщений',self.act('/my_memory'))
        self.act('/confirm_me','клуб')
        self.assertEqual(self.ctx()['facts'][0]['value'],'Спартак')
        self.assertEqual(self.ctx()['facts'][0]['source_message'],7)
        m.observe(self.store,-100123,1,'я болею за Зенит',8,now=self.now)
        self.assertEqual(self.ctx()['facts'][0]['value'],'Спартак')
        self.act('/confirm_me','клуб')
        self.assertEqual(self.ctx()['facts'][0]['value'],'Зенит')

    def test_no_third_party_quotes_or_questions(self):
        self.act('/my_memory','on')
        for text in ['он болеет за Спартак','я не болею за Спартак','я болею за Спартак?','«я болею за Спартак»','если я болею за Спартак']:
            m.observe(self.store,-100123,1,text,1,now=self.now)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM personal_facts').fetchone()[0],0)

    def test_isolation_and_correction(self):
        self.act('/my_memory','on');self.act('/remember_me','игры = Ведьмак')
        self.assertEqual(self.ctx(user=2)['facts'],[]);self.assertEqual(self.ctx(chat=-100456)['facts'],[])
        self.act('/remember_me','игры = Dota 2')
        self.assertEqual([r['value'] for r in self.ctx()['facts']],['Dota 2'])
        self.assertNotIn('Ведьмак',self.act('/my_memory'))

    def test_banter_independent_and_off(self):
        self.act('/my_memory','on');self.assertFalse(self.ctx()['banter'])
        self.act('/my_memory','banter on');self.assertTrue(self.ctx()['banter'])
        self.act('/my_memory','off');self.assertEqual(self.ctx(),{})

    def test_history_explicit_relevant_and_delete(self):
        self.act('/my_memory','on');self.act('/my_memory','history off')
        m.observe(self.store,-100123,1,'обсуждаем ведьмак квест',1,True,self.now)
        self.assertEqual(self.ctx(query='ведьмак')['conversation'],[])
        self.act('/my_memory','history on')
        m.observe(self.store,-100123,1,'обсуждаем ведьмак квест',1,True,self.now)
        self.assertEqual(len(self.ctx(query='ведьмак')['conversation']),1)
        self.assertEqual(self.ctx(query='футбол')['conversation'],[])
        self.act('/my_memory','history off')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM personal_conversations').fetchone()[0],0)

    def test_expiration_and_forget(self):
        self.act('/my_memory','on');self.act('/remember_me','обещание = вернуть игру')
        self.assertEqual(m.context(self.store,-100123,1,now=self.now+31*86400)['facts'],[])
        self.act('/remember_me','клуб = Спартак');self.act('/forget_me','клуб')
        self.assertFalse(any(r['category']=='club' for r in self.ctx()['facts']))
        self.act('/forget_me','all');self.assertEqual(self.ctx(),{})
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM personal_facts').fetchone()[0],0)

    def test_command_routing_and_no_cross_user(self):
        import test_bot
        f=test_bot.BotTests();f.setUp()
        try:
            f.bind()
            f.bot.handle(f.update('/my_memory on',user=7,number=20))
            f.bot.handle(f.update('/remember_me клуб = Спартак',user=7,number=21))
            self.assertEqual(m.context(f.store,-100,7)['facts'][0]['value'],'Спартак')
            self.assertEqual(m.context(f.store,-100,42)['facts'],[])
            self.assertTrue(f.bot.utility_jobs.empty())
        finally:f.tearDown()

    def test_confirmed_memory_reaches_model_as_data(self):
        import test_bot
        f=test_bot.BotTests();f.setUp()
        try:
            f.bind()
            m.action(f.store,dict(command='/my_memory',value='on'),-100,7,20)
            m.action(f.store,dict(command='/remember_me',value='клуб = Спартак'),-100,7,21)
            f.bot.enqueue(-100,'какую футбольную команду я люблю',22,user_id=7,force_thoughtful=True)
            job=f.bot.jobs.get_nowait()
            with patch('bot.http_json',return_value={'message':{'content':'Ты говорил что болеешь за Спартак.'}}) as call:
                f.bot.generate_thoughtful(job)
            self.assertTrue(call.called)
            messages=call.call_args.args[1]['messages']
            import json
            rendered=json.dumps(messages,ensure_ascii=False)
            self.assertIn('current_participant',rendered)
            self.assertIn('Спартак',rendered)
            self.assertIn('self_reported',rendered)
        finally:f.tearDown()


if __name__=='__main__':unittest.main()
