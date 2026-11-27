import json,time,unittest
from unittest.mock import patch
import situational_activity as s
import test_bot


class SituationTests(unittest.TestCase):
    def setUp(self):
        self.f=test_bot.BotTests();self.f.setUp();self.f.bind()
        self.bot=self.f.bot;self.store=self.f.store
        self.bot.pending_requests.clear()
        self.store.set('automatic',True);self.store.set('adaptive',False)
    def tearDown(self):self.f.tearDown()
    def rows(self):
        return [dict(id=10,user_id=7,text='интересная футбольная история',human=True,timestamp=time.time()-30)]
    def call(self,value):return lambda *a,**kw:{'message':{'content':json.dumps(value)}}
    def choice(self,**kwargs):return dict(speak=True,confidence=.9,kind='football',message_id=10,reason='уместное дополнение',query='',**kwargs)

    def test_strict_decisions(self):
        config=dict(ollama_url='http://test',model='test')
        valid=self.choice()
        self.assertEqual(s.evaluate(config,self.rows(),self.call(valid))['kind'],'football')
        for changes in [dict(speak=False),dict(speak='true'),dict(confidence=.5),dict(message_id=999),dict(kind='other'),dict(kind='factcheck',query='')]:
            self.assertIsNone(s.evaluate(config,self.rows(),self.call(dict(valid,**changes))))
        self.assertIsNone(s.evaluate(config,self.rows(),lambda *a,**kw: {'message':{'content':'bad json'}}))

    def test_scheduled_evaluation_deduplicated(self):
        now=time.time()
        for i in range(2):
            msg=self.f.update('длинное обсуждение футбольного матча',user=7+i,number=10+i,age=30)['message']
            self.store.add_message(msg)
        self.assertTrue(s.consider(self.bot,now))
        self.assertEqual(self.bot.jobs.qsize(),1)
        self.assertFalse(s.consider(self.bot,now+10))
        job=self.bot.jobs.get_nowait();self.assertTrue(job['automatic'])

    def test_disabled_and_quiet_chat(self):
        self.store.set('automatic',False)
        self.assertFalse(s.consider(self.bot))
        self.store.set('automatic',True)
        self.assertFalse(s.consider(self.bot))

    def test_keywords_and_generic_question_do_not_bypass_model(self):
        for number,text in [(12,'украина война киев'),(13,'Кто выиграет матч?')]:
            self.bot.handle(self.f.update(text,user=7,number=number))
        self.assertTrue(self.bot.jobs.empty())
        self.assertTrue(self.bot.utility_jobs.empty())
        self.bot.handle(self.f.update('бубус как дела',user=7,number=14))
        self.assertFalse(self.bot.jobs.empty())

    def test_topic_drift_and_direct_request_cancel(self):
        job=dict(chat_id=-100,queued_at=time.time(),situation={'kind':'humor'},situation_latest=20)
        self.assertTrue(s.still_relevant(self.bot,job))
        self.store.set('last_direct:-100',job['queued_at']+1)
        self.assertFalse(s.still_relevant(self.bot,job))
        self.store.set('last_direct:-100',0)
        for i in range(4):self.store.add_message(self.f.update('новая тема',user=7,number=21+i)['message'])
        self.assertFalse(s.still_relevant(self.bot,job))

    def test_silence_never_claims_send(self):
        job=dict(chat_id=-100,queued_at=time.time(),automatic=True,text='разговор',reply_to=10,utility=None,tease_target=None)
        with patch.object(self.bot,'auto_eligible',return_value=True),patch.object(s,'prepare',return_value=False),patch.object(self.store,'claim_send') as claim:
            self.bot._process_job(job)
        claim.assert_not_called();self.assertEqual(self.f.telegram.sent,[])

    def test_existing_automatic_limits(self):
        self.store.add_message(self.f.update('текст',number=10)['message'])
        for i in range(5):
            with patch('bot.time.time',return_value=time.time()+i*3700):self.store.claim_send('automatic',-100)
        self.assertIsNone(self.store.claim_send('automatic',-100))

    def test_automatic_factcheck_uses_automatic_quota(self):
        job=dict(chat_id=-100,queued_at=time.time(),automatic=True,text='утверждение',reply_to=10,
                 utility={'kind':'factcheck','query':'проверяемое утверждение'},tease_target=None,
                 _metrics={},thoughtful=False,random_quote=False)
        from common import HTMLMessage
        with patch.object(self.bot,'auto_eligible',return_value=True),patch.object(s,'prepare',return_value=True),\
             patch.object(self.bot.fact_checker,'answer',return_value=HTMLMessage('<b>Найденные источники опровергают</b>\nответ')),\
             patch.object(self.store,'claim_send',return_value=None) as claim,patch.object(self.bot,'reserve_utility_send') as manual:
            self.bot._process_job(job)
        claim.assert_called_once_with('automatic',-100);manual.assert_not_called()

    def test_inconclusive_factcheck_silent(self):
        job=dict(chat_id=-100,queued_at=time.time(),automatic=True,text='утверждение',reply_to=10,
                 utility={'kind':'factcheck','query':'проверяемое утверждение'},tease_target=None,
                 _metrics={},thoughtful=False,random_quote=False)
        with patch.object(self.bot,'auto_eligible',return_value=True),patch.object(s,'prepare',return_value=True),\
             patch.object(self.bot.fact_checker,'answer',return_value='Однозначного подтверждения нет'),\
             patch.object(self.store,'claim_send') as claim:
            self.bot._process_job(job)
        claim.assert_not_called()


if __name__=='__main__':unittest.main()
