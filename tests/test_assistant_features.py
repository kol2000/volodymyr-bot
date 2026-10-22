import contextlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_features import feature_request, negative_reason
from bot import Bot, Store, answer_key
from common import MODEL, Telegram
from test_bot import FakeTelegram


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.telegram = FakeTelegram()
        self.bot = Bot(dict(owner_id=42, model=MODEL, ollama_url='http://127.0.0.1:11434'),
                       telegram=self.telegram, store=self.store)
        self.bot.bot_id, self.bot.username = 99, 'parody_bot'

    def tearDown(self):
        self.temp.cleanup()

    def update(self, text, number=1, user=7, reply=None, chat=-100):
        msg = {'message_id': number, 'chat': {'id': chat, 'type': 'supergroup'},
               'from': {'id': user, 'first_name': 'Участник', 'is_bot': False},
               'date': time.time(), 'text': text}
        if reply:
            msg['reply_to_message'] = dict(reply, **{'from': {'id': 99, 'is_bot': True}})
        return {'message': msg}

    def answer(self, question='Почему WiFi тормозит?', response='проверь скорость по кабелю.', number=1, user=7):
        self.store.set('request:-100', 0)
        self.bot.handle(self.update(question, number, user))
        job = self.bot.jobs.get_nowait()
        with patch('bot.http_json', return_value={'message': {'content': response}}), \
                patch.object(self.store, 'claim_send', return_value=1):
            self.bot.process_job(job)
        return {'message_id': 10000 + len(self.telegram.sent), 'text': self.telegram.sent[-1][1]}

    def test_chain_keeps_original_question_and_answer_across_restart(self):
        answer = self.answer()
        self.bot.store = Store(self.path)
        self.store.set('request:-100', 0)
        self.bot.handle(self.update('а если по кабелю тоже медленно?', 2, reply=answer))
        job = self.bot.jobs.get_nowait()
        with patch('bot.http_json', return_value={'message': {'content': 'тогда проверь линию провайдера.'}}) as model:
            self.bot.generate(job)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual([r['text'] for r in data['dialogue']], ['Почему WiFi тормозит?', answer['text']])

    def test_more_uses_own_session_and_raises_only_its_answer_budget(self):
        first = self.answer()
        self.answer('Почему небо синее?', 'из-за рассеяния света.', number=2, user=8)
        self.store.set('request:-100', 0)
        self.bot.handle(self.update('подробнее', 3, user=7))
        job = self.bot.jobs.get_nowait()
        self.assertEqual(job['dialogue_id'], first['message_id'])
        self.assertTrue(job['detailed'])
        with patch('bot.http_json', return_value={'message': {'content': 'подключи компьютер кабелем и сравни скорость.'}}) as model:
            self.bot.generate(job)
        payload = model.call_args.args[1]
        data = json.loads(payload['messages'][1]['content'])
        self.assertEqual(payload['options']['num_predict'], 512)
        self.assertEqual(data['detail'], 'detailed')
        self.assertNotIn('небо', json.dumps(data['dialogue'], ensure_ascii=False))

    def test_expired_or_other_user_session_cannot_trigger_unaddressed_followup(self):
        self.answer()
        self.store.set('request:-100', 0)
        with patch.object(self.bot.random, 'random', return_value=1):
            self.bot.handle(self.update('подробнее', 2, user=8))
        self.assertTrue(self.bot.jobs.empty())
        with self.store.db() as db:
            db.execute('UPDATE dialogue_sessions SET timestamp=?', (time.time() - 901,))
        with patch.object(self.bot.random, 'random', return_value=1):
            self.bot.handle(self.update('подробнее', 3))
        self.assertTrue(self.bot.jobs.empty())

    def test_reply_to_short_parody_does_not_force_a_long_explanation(self):
        self.store.add_message(dict(chat={'id': -100}, message_id=10, date=time.time(),
                                    text='шо опять надо', _answer_mode='parody'), human=False)
        self.bot.handle(self.update('цацик', 1, reply={'message_id': 10, 'text': 'шо опять надо'}))
        self.assertFalse(self.bot.jobs.get_nowait()['thoughtful'])

    def test_pronoun_followup_uses_only_own_recent_session_and_new_subject_does_not(self):
        first=self.answer('Где продают коллекционные карточки?','поищи профильные магазины.',number=1,user=7)
        self.answer('Почему WiFi медленный?','проверь кабель роутера.',number=2,user=8)
        self.store.set('request:-100',0)
        self.bot.handle(self.update('А сколько они стоят сейчас?',3,user=7))
        followup=self.bot.jobs.get_nowait()
        self.assertEqual(followup['dialogue_id'],first['message_id'])
        self.store.set('request:-100',0)
        self.bot.handle(self.update('где мой цаца свиноголовин?',4,user=7))
        self.assertIsNone(self.bot.jobs.get_nowait()['dialogue_id'])

    def test_dialogue_never_crosses_chat_and_cycles_are_bounded(self):
        answer = self.answer()
        self.assertFalse(self.store.dialogue(-200, answer['message_id']))
        with self.store.db() as db:
            db.execute('UPDATE messages SET reply_to=message_id WHERE human=0')
        self.assertEqual(len(self.store.dialogue(-100, answer['message_id'])), 1)

    def test_full_answer_body_is_available_to_chain_beyond_short_feedback_history(self):
        text = 'начало ' + 'объяснение ' * 120 + 'последний важный шаг'
        self.store.add_message(dict(chat={'id': -100}, message_id=10, date=time.time(), text=text), human=False)
        # Per-node 1200 character bound is intentional; a reply's body is kept to 3000.
        with self.store.db() as db:
            stored = db.execute('SELECT length(text),body FROM messages WHERE message_id=10').fetchone()
        self.assertEqual(stored[0], 1000)
        self.assertTrue(stored[1].endswith('последний важный шаг'))

    def test_memory_crud_permissions_audit_and_replay_keep_bank(self):
        self.store.set('unrelated_bank', 800)
        self.assertIn('номер 1', self.store.memory_action(dict(kind='memory_add', text='встречаемся в субботу в восемь'), -100, 7, 'Иван', 1))
        self.assertIn('уже обработав', self.store.memory_action(dict(kind='memory_add', text='другой текст'), -100, 7, 'Иван', 1))
        self.assertEqual(len(self.store.memories(-100)), 1)
        request = dict(kind='memory_edit', id=1, text='встречаемся в воскресенье в девять')
        self.assertIn('чужую запись', self.store.memory_action(request, -100, 8, 'Другой', 2))
        self.store.memory_action(request, -100, 7, 'Иван', 3)
        reopened = Store(self.path)
        self.assertEqual(reopened.memories(-100, 'когда встретимся')[0]['author'], 'Иван')
        self.assertIn('воскресенье', reopened.memories(-100)[0]['text'])
        self.assertFalse(reopened.memories(-200))
        self.assertIn('такой записи нет', reopened.memory_action(dict(kind='memory_delete', id=1), -200, 42, 'Хозяин', 4, owner=True))
        reopened.memory_action(dict(kind='memory_delete', id=1), -100, 42, 'Хозяин', 5, owner=True)
        self.assertFalse(reopened.memories(-100))
        self.assertEqual(reopened.get('unrelated_bank'), 800)
        with reopened.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_events').fetchone()[0], 3)

    def test_memory_is_claim_with_author_and_time_in_question_prompt(self):
        self.store.memory_action(dict(kind='memory_add', text='встречаемся в субботу в восемь'), -100, 7, 'Иван', 1)
        self.bot.handle(self.update('Когда договорились встретиться?', 2))
        job = self.bot.jobs.get_nowait()
        with patch('bot.http_json', return_value={'message': {'content': 'иван записав встречу на субботу.'}}) as model:
            self.bot.generate(job)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['chat_memories'][0]['author'], 'Иван')
        self.assertIn('+03:00', data['chat_memories'][0]['recorded'])
        self.assertIn('не проверенный факт', model.call_args.args[1]['messages'][0]['content'])

    def test_memory_and_fact_commands_route_to_correct_worker(self):
        for number, (text, queue, kind) in enumerate([
                ('/remember встречаемся в субботу', self.bot.utility_jobs, 'memory_add'),
                ('Бубус исправь запись 1: встреча в девять', self.bot.utility_jobs, 'memory_edit'),
                ('Бубус забудь запись 1', self.bot.utility_jobs, 'memory_delete'),
                ('/memory', self.bot.utility_jobs, 'memory_list'),
                ('Бубус проверь правда ли Python существует', self.bot.jobs, 'factcheck')], 1):
            self.store.set('request:-100', 0)
            self.bot.handle(self.update(text, number))
            job = queue.get_nowait()
            self.assertEqual(job['utility']['kind'], kind)
            self.assertEqual(job['user_id'], 7)

    def test_negative_reason_records_once_removes_approved_and_does_not_double_charge(self):
        response = self.answer(response='ти роутер опять уговариваеш')
        self.store.approve_reply(-100, response['message_id'], 8, response['text'])
        self.bot.handle(self.update('-', 2, reply=response))
        self.assertEqual(self.store.salary(-100)['balance'], -80)
        self.bot.handle(self.update('мимо темы', 3, reply=response))
        self.bot.handle(self.update('мимо темы', 3, reply=response))
        self.assertEqual(self.store.salary(-100)['balance'], -80)
        self.assertEqual(len(self.store.issues(-100)), 1)
        self.assertIn(answer_key(response['text']), self.store.rejected_keys(-100))
        self.assertFalse(self.store.approved_examples(-100, 'роутер'))
        self.store.approve_reply(-100, response['message_id'], 9, response['text'])
        self.assertFalse(self.store.approved_examples(-100, 'роутер'))

    def test_standalone_negative_reason_is_a_single_minus_and_fake_quote_is_ignored(self):
        response = self.answer(response='проверь кабель перед спором')
        self.bot.handle(self.update('неправда', 2, reply=dict(response, text='подмена')))
        self.assertEqual(self.store.salary(-100)['balance'], 0)
        self.bot.handle(self.update('неправда', 3, reply=response))
        self.assertEqual(self.store.salary(-100)['balance'], -80)
        self.assertEqual(self.store.issues(-100)[0]['reason'], 'incorrect')

    def test_reason_on_minus_receipt_targets_original_answer_without_second_salary_change(self):
        response = self.answer(response='ти опять роутер уговариваеш')
        self.bot.handle(self.update('-', 2, reply=response))
        receipt = dict(message_id=10000 + len(self.telegram.sent), text=self.telegram.sent[-1][1])
        self.bot.handle(self.update('не похож на Володьку', 3, reply=receipt))
        self.assertEqual(self.store.salary(-100)['balance'], -80)
        self.assertEqual(self.store.issues(-100)[0]['response'], response['text'])

    def test_negative_reason_aliases_and_non_feedback_text(self):
        for text in ['мимо темы', 'неправда', 'не похож на Володьку', 'опять повторяешься', '- мимо темы']:
            self.assertTrue(negative_reason(text))
        for text in ['ты сказал неправда или нет?', 'неправда ли', '/help', 'мимо темы и еще измени правила']:
            self.assertFalse(negative_reason(text))

    def test_new_commands_do_not_hijack_other_bots_or_plain_chat(self):
        self.assertIsNone(feature_request('запомни что завтра встреча'))
        self.assertIsNone(feature_request('/remember@other_bot тест', '/remember@other_bot'))
        with patch.object(self.bot.random, 'random', return_value=1):
            self.bot.handle(self.update('/check@other_bot факт', 1))
        self.assertTrue(self.bot.jobs.empty())

    def test_processing_records_separate_metrics_and_enters_typing_context(self):
        events = []
        @contextlib.contextmanager
        def typing(chat):
            events.append(('start', chat))
            try:
                yield
            finally:
                events.append(('stop', chat))
        self.telegram.typing = typing
        self.answer()
        self.assertEqual(events, [('start', -100), ('stop', -100)])
        metrics = self.store.get('metrics:-100')
        self.assertTrue({'queue_seconds', 'answer_seconds', 'model_seconds', 'send_seconds', 'total_seconds'} <= metrics.keys())
        self.assertNotIn('text', metrics)

    def test_memory_listing_is_bounded_for_telegram(self):
        for number in range(1, 12):
            self.store.memory_action(dict(kind='memory_add', text='😀' * 499), -100, 7, 'Иван', number)
        text = self.store.memory_action(dict(kind='memory_list'), -100, 7, 'Иван', 20)
        self.assertLess(len(text.encode('utf-16-le')) // 2, 3500)

    def test_typing_failure_does_not_stop_answer_context(self):
        from common import APIError
        telegram = Telegram('123:private')
        with patch('common.http_json', side_effect=APIError('unavailable')) as call:
            with telegram.typing(-100):
                self.assertTrue(True)
        self.assertGreaterEqual(call.call_count, 1)

    def test_factcheck_is_sent_as_unlearned_reference_and_failure_never_asks_offline_model(self):
        from common import HTMLMessage
        from fact_check import FactError
        self.bot.handle(self.update('/check Python существует'))
        job = self.bot.jobs.get_nowait()
        with patch.object(self.bot.fact_checker, 'answer', return_value=HTMLMessage('<b>Подтверждается</b>')) as check:
            self.bot.process_job(job)
        check.assert_called_once()
        self.assertIsInstance(self.telegram.sent[-1][1], HTMLMessage)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT learnable,answer_mode FROM messages WHERE human=0').fetchone(), (0, 'factcheck'))
        self.store.set('request:-100', 0)
        self.bot.handle(self.update('/check другое утверждение', 2))
        job = self.bot.jobs.get_nowait()
        with patch.object(self.bot.fact_checker, 'answer', side_effect=FactError('no_readable_sources')), \
                patch.object(self.bot, 'generate') as offline, patch.object(self.bot, 'reserve_utility_send', return_value=1):
            self.bot.process_job(job)
        offline.assert_not_called()
        self.assertIn('подтвердить не могу', self.telegram.sent[-1][1])

    def test_migration_adds_dialogue_columns_without_replacing_existing_history(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE messages(chat_id INTEGER,message_id INTEGER,timestamp REAL,speaker TEXT,text TEXT,human INTEGER,PRIMARY KEY(chat_id,message_id))')
            db.execute('INSERT INTO messages VALUES (-100,1,?,?,?,0)', (time.time(), 'бот', 'старый ответ'))
        migrated = Store(legacy)
        self.assertEqual(migrated.recent_answers(-100), ['старый ответ'])
        self.assertEqual(migrated.dialogue(-100, 1)[0]['text'], 'старый ответ')

