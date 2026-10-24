import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_features import negative_reason
from bot import Bot, Store
from common import MODEL
from conversation import dialogue_tone
from quality_report import quality_report, record_quality
from test_bot import FakeTelegram


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store=Store(Path(self.folder.name)/'check.sqlite3')
        self.store.set('chat_id',-100)
        self.telegram=FakeTelegram()
        self.bot=Bot(dict(owner_id=42,model=MODEL,ollama_url='http://localhost:11434'),self.telegram,self.store)
        self.bot.bot_id=99
        self.source=dict(message_id=100,text='Первая полная фраза. Вторая полная фраза.',chat={'id':-100},date=time.time())
        self.store.add_message(dict(self.source,_reply_to=1,_answer_mode='question'),human=False,
                               request_text='Как проверить резервную копию?',rateable=True)

    def update(self,text,user=7,number=2):
        reply=dict(self.source,**{'from':{'id':99}})
        return {'message':dict(chat={'id':-100,'type':'supergroup'},message_id=number,date=time.time(),text=text,
                               reply_to_message=reply,**{'from':{'id':user,'first_name':'Участник'}})}

    def test_feedback_categories_are_distinct_and_new_phrases_are_not_salary_votes(self):
        for text,reason in [('не так понял','misunderstood'),('слишком длинно','too_long'),
                            ('слишком кратко','too_short'),('слишком грубо','too_rude'),('это неправда','incorrect')]:
            self.assertEqual(negative_reason(text),reason)
            self.assertIsNone(negative_reason(text,legacy_only=True))
        self.assertEqual(negative_reason('неправда',legacy_only=True),'incorrect')

    def test_long_feedback_targets_original_answer_without_charging_bank(self):
        with patch.object(self.store,'rate_reply') as rate:
            self.bot.handle(self.update('слишком длинно'))
        rate.assert_not_called()
        job=self.bot.jobs.get_nowait()
        self.assertEqual(job['dialogue_id'],100)
        self.assertEqual(job['response_control'],'concise')
        self.assertEqual(job['quoted_message'],self.source['text'])

    def test_misunderstanding_asks_for_missing_meaning_and_feedback_can_change(self):
        self.bot.handle(self.update('не так понял'))
        self.assertIn('Что именно',self.telegram.sent[-1][1])
        self.bot.handle(self.update('слишком грубо',number=3))
        self.assertGreater(self.store.get('calm_until:-100:7'),time.time())
        self.assertFalse(self.store.get('calm_until:-100:8',0))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT reason FROM answer_issues').fetchall(),[('too_rude',)])

    def test_quality_report_keeps_numeric_metrics_and_fixed_reasons_only(self):
        for total in (1,2,100):
            record_quality(self.store,dict(chat_id=-100,thoughtful=True,_outcome='sent',text='SECRET',
                           _inference_calls=[{'seconds':.1},{'seconds':.2}],
                           _rejections=['lost_uncertainty','raw private text']),
                           dict(total_seconds=total,queue_seconds=0,model_calls=2,model_seconds=1,secret='SECRET'))
        report=quality_report(self.store,-100)
        self.assertIn('p50 2.0',report)
        self.assertIn('p95 100.0',report)
        self.assertIn('повторных вызовов: 3',report)
        self.assertIn('0.6 с суммарно',report)
        self.assertNotIn('SECRET',report)
        with self.store.db() as db:
            saved=str(db.execute('SELECT metrics,rejections FROM quality_runs').fetchall())
        self.assertNotIn('SECRET',saved)
        self.assertNotIn('raw private text',saved)

    def test_quality_command_is_owner_only(self):
        self.bot.handle(self.update('/quality'))
        self.assertIn('доступна владельцу',self.telegram.sent[-1][1])
        self.bot.handle(self.update('/quality',user=42,number=3))
        self.assertIn('последние 7 дней',self.telegram.sent[-1][1])

    def test_quality_retention_does_not_keep_old_runs(self):
        record_quality(self.store,dict(chat_id=-100),dict(total_seconds=1))
        with self.store.db() as db:
            db.execute('UPDATE quality_runs SET created=?',(time.time()-8*86400,))
        record_quality(self.store,dict(chat_id=-100),dict(total_seconds=2))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_runs').fetchone()[0],1)

    def correction(self,request,answer):
        self.store.add_message(dict(chat={'id':-100},message_id=1,date=time.time(),text=request,**{'from':{'id':7}}))
        with self.store.db() as db:
            db.execute('INSERT INTO answer_corrections(chat_id,message_id,source_id,user_id,author,request,corrected,mode,reason,created,confirmed) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (-100,5,100,8,'Автор',request,answer,'question','incorrect',time.time(),time.time()))

    def test_corrections_preserve_direction_and_exclude_changing_facts(self):
        self.correction('Почему WiFi медленнее кабеля?','Возможны помехи.')
        self.assertTrue(self.store.correction_examples(-100,'Почему WiFi медленнее кабеля?','question'))
        self.assertFalse(self.store.correction_examples(-100,'Почему WiFi медленнее кабеля?','question',user_id=8))
        self.assertFalse(self.store.correction_examples(-100,'Почему кабель медленнее WiFi?','question'))
        self.assertFalse(self.store.correction_examples(-100,'Почему WiFi не медленнее кабеля?','question'))
        with self.store.db() as db:
            db.execute("UPDATE answer_corrections SET corrected='Сегодня скорость составляет 10 Мбит.'")
        self.assertFalse(self.store.correction_examples(-100,'Почему WiFi медленнее кабеля?','question'))

    def test_personal_correction_cannot_be_applied_to_another_person(self):
        self.correction('Как зовут моего кота?','Его зовут Барсик.')
        self.assertTrue(self.store.correction_examples(-100,'Как зовут моего кота?','question',user_id=7))
        self.assertFalse(self.store.correction_examples(-100,'Как зовут моего кота?','question',user_id=8))
        self.assertFalse(self.store.correction_examples(-100,'Как зовут моего кота?','question'))

    def test_irritation_tone_is_not_inferred_from_a_quote_or_third_person(self):
        self.assertEqual(dialogue_tone('Цацик, хватит умничать!'),'calm')
        self.assertEqual(dialogue_tone('Он написал: «хватит умничать!»'),'conversational')
        self.assertEqual(dialogue_tone('Мой друг Цацик сказал что ему надоело.'),'conversational')

    def test_quoted_irritation_is_not_a_license_for_a_personal_argument(self):
        job=dict(chat_id=-100,text='Он написал: «цацик хватит умничать!»',automatic=False,queued_at=time.time(),user_id=7)
        with patch('bot.http_json',return_value={'message':{'content':'Ну и что, если умничаю? Ты сам начал.'}}):
            answer=self.bot.generate(job)
        self.assertEqual(job['_rejections'],['reported_action_reversal']*2)
        self.assertEqual(job['_local_reply'],'clarification')
        self.assertIn('смысл',answer)

    def test_calm_tone_rejects_insult_and_uses_a_second_attempt(self):
        job=dict(chat_id=-100,text='Цацик, хватит умничать!',automatic=False,queued_at=time.time(),user_id=7)
        with patch('bot.http_json',side_effect=[{'message':{'content':'Сам виноват, идиот.'}},
                                              {'message':{'content':'Что именно нужно уточнить?'}}]) as model:
            answer=self.bot.generate(job)
        self.assertIn('уточнить',answer)
        self.assertEqual(model.call_count,2)
        self.assertEqual(job['_rejections'],['tone_mismatch'])
