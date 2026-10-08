import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import Bot, Store, WELCOME_TEXT
from common import APIError, HTMLMessage, MODEL
from community import MOSCOW, community_request, period_start, summary_input, summary_text
from test_bot import FakeTelegram


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.telegram = FakeTelegram()
        self.bot = Bot({'owner_id': 42, 'model': MODEL, 'ollama_url': 'http://localhost:11434'},
                       telegram=self.telegram, store=self.store)
        self.bot.username = 'parody_bot'
        self.bot.bot_id = 99

    def tearDown(self):
        self.folder.cleanup()

    def update(self, text, number=1, user=7, chat=-100, stamp=None, name='Участник'):
        return {'update_id': number, 'message': {'message_id': number, 'chat': {'id': chat, 'type': 'supergroup'},
                'from': {'id': user, 'first_name': name, 'is_bot': False},
                'date': time.time() if stamp is None else stamp, 'text': text}}

    def human(self, text, number=1, **kwargs):
        self.store.add_message(self.update(text, number, **kwargs)['message'])

    def job(self, text, number=100, user=7):
        self.bot.handle(self.update(text, number, user))
        target = self.bot.jobs if self.bot.jobs.qsize() else self.bot.utility_jobs
        return target.get_nowait()

    def test_natural_requests_commands_and_unaddressed_text(self):
        cases = {'Володька, что было в чате?': 'summary', 'Володька, кто больше всех пиздит?': 'stats',
                 'Бубус, пицца или шаурма?': 'choose', 'Володька, правила': 'rules',
                 'Володька, что ты умеешь?': 'help', 'Володька, активнее': 'activity',
                 'Володька, реже': 'activity', 'Володька, включи автоактивность': 'adaptive',
                 'Володька, отключи приветствия': 'greetings'}
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertEqual(community_request(text)['kind'], kind)
        for text in ('что было в чате?', 'пицца или шаурма?', 'правила', 'активнее', 'просто болтаем'):
            self.assertIsNone(community_request(text))
        self.assertEqual(community_request('/top неделю', '/top')['days'], 7)
        self.assertEqual(community_request('/summary месяц', '/summary')['days'], 30)
        self.assertEqual(community_request('Володька какая погода в рахине?'), None)
        self.assertIsNone(community_request('/convert 100 USD или 100 BYN', '/convert', addressed=True))
        self.assertIsNone(community_request('/weather Орёл или Москва', '/weather', addressed=True))

    def test_choices_are_explicitly_random_escaped_and_do_not_use_model(self):
        job = self.job('/choose <кот> | собака')
        self.assertEqual(self.bot.jobs.qsize(), 0)
        with patch.object(self.bot.random, 'choice', return_value='<кот>'), patch.object(self.bot, 'generate') as model:
            self.bot.process_job(job)
        self.assertIn('Случайный выбор: <b>&lt;кот&gt;</b>', self.telegram.sent[-1][1])
        model.assert_not_called()
        for text in ('/choose кот', '/choose ' + ' | '.join(str(i) for i in range(11))):
            self.assertEqual(community_request(text, '/choose')['kind'], 'fixed')

    def test_public_help_has_examples_and_no_owner_commands(self):
        with patch.object(self.bot, 'generate') as model:
            self.bot.process_job(self.job('/help'))
        report = self.telegram.sent[-1][1]
        self.assertIn('/summary', report)
        self.assertIn('/top', report)
        self.assertNotIn('/rules_set', report)
        self.assertIsInstance(report, HTMLMessage)
        model.assert_not_called()

    def test_members_cannot_change_settings(self):
        for number, text in enumerate(('/activity 20', '/rules_set новые правила', '/greetings_off', '/adaptive_off'), 1):
            self.store.set('request:-100', 0)
            self.bot.handle(self.update(text, number))
            job = self.bot.utility_jobs.get_nowait()
            self.assertEqual(job['utility']['kind'], 'fixed')
        self.assertIsNone(self.store.get('activity_probability'))
        self.assertIsNone(self.store.get('rules'))
        self.assertIsNone(self.store.get('greetings_enabled'))
        self.assertIsNone(self.store.get('adaptive'))

    def test_owner_settings_are_validated_persistent_and_duplicate_safe(self):
        change = self.update('Володька, активнее', user=42)
        self.bot.handle(change)
        self.bot.handle(change)
        self.assertEqual(self.store.get('activity_probability'), 0.06)
        self.bot.handle(self.update('/activity 21', 2, 42))
        self.assertEqual(self.store.get('activity_probability'), 0.06)
        self.bot.handle(self.update('/adaptive_off', 3, 42))
        self.bot.handle(self.update('/greetings_off', 4, 42))
        self.bot.handle(self.update('/activity 20', 5, 42, stamp=time.time() - 121))
        restarted = Store(self.path)
        self.assertEqual(restarted.get('activity_probability'), 0.06)
        self.assertFalse(restarted.get('adaptive'))
        self.assertFalse(restarted.get('greetings_enabled'))
        self.assertFalse(restarted.get('automatic', False))

    def test_rules_keep_authored_punctuation_and_reply_text_and_are_escaped(self):
        self.bot.handle(self.update('/rules_set Не спамить. <b>Без игр!</b>', user=42))
        self.bot.process_job(self.job('/rules', 2))
        self.assertIn('Не спамить. &lt;b&gt;Без игр!&lt;/b&gt;', self.telegram.sent[-1][1])
        change = self.update('/rules_set', 3, 42)
        change['message']['reply_to_message'] = {'text': 'Правило с точкой.'}
        self.bot.handle(change)
        self.assertEqual(self.store.get('rules'), 'Правило с точкой.')

    def test_greetings_are_generic_escaped_deduplicated_and_target_keeps_exact_phrase(self):
        join = self.update('', 1)
        join['message']['new_chat_members'] = [{'id': 11, 'first_name': '<новый>', 'is_bot': False},
                                               {'id': 12, 'first_name': 'ДругойБот', 'is_bot': True}]
        self.bot.handle(join)
        self.bot.handle(join)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertIn('&lt;новый&gt;', self.telegram.sent[0][1])
        self.assertNotIn('ДругойБот', self.telegram.sent[0][1])
        self.store.set('greetings_enabled', False)
        join['message']['message_id'] = 2
        self.bot.handle(join)
        self.assertEqual(len(self.telegram.sent), 1)
        join['message']['new_chat_members'][0]['username'] = 'leonadosasa'
        self.bot.handle(join)
        self.assertEqual(self.telegram.sent[-1][1], WELCOME_TEXT)

    def test_statistics_use_ids_ignore_duplicates_commands_bots_and_other_groups(self):
        self.human('привет', 1, user=7, name='<Имя>')
        self.human('повтор обновления', 1, user=7, name='<Имя>')
        self.human('ответ', 2, user=8, name='<Имя>')
        self.human('ещё ответ', 3, user=7, name='НовоеИмя')
        self.human('/help', 4, user=8)
        self.human('другая группа', 5, chat=-200)
        self.store.add_message(self.update('бот', 6)['message'], human=False)
        report = self.store.statistics(-100, 1)
        self.assertEqual((report['messages'], report['users']), (3, 2))
        self.assertEqual(report['leaders'], [('НовоеИмя', 2), ('<Имя>', 1)])
        self.bot.process_job(self.job('/top', 100))
        self.assertIn('&lt;Имя&gt;', self.telegram.sent[-1][1])
        self.assertIn('Сообщений: <b>3</b>', self.telegram.sent[-1][1])

    def test_statistics_calendar_periods_use_moscow_midnight(self):
        now = datetime(2026, 10, 8, 0, 30, tzinfo=MOSCOW).timestamp()
        with patch('time.time', return_value=now):
            self.human('сегодня', 1, stamp=now)
            self.human('вчера', 2, stamp=now - 3600)
            self.human('позапрошлая неделя', 3, stamp=now - 9 * 86400)
            self.assertEqual(self.store.statistics(-100, 1)['messages'], 1)
            self.assertEqual(self.store.statistics(-100, 7)['messages'], 2)
            self.assertEqual(self.store.statistics(-100, 30)['messages'], 3)

    def test_legacy_database_migrates_without_losing_settings_or_relabeling_old_messages(self):
        path = Path(self.folder.name) / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT);'
                             'CREATE TABLE messages (chat_id INTEGER,message_id INTEGER,timestamp REAL,'
                             'speaker TEXT,text TEXT,human INTEGER,PRIMARY KEY(chat_id,message_id));')
            db.execute('INSERT INTO settings VALUES (?,?)', ('automatic', 'true'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (-100, 55, time.time(), 'Старый', 'история', 1))
        migrated = Store(path)
        self.assertTrue(migrated.get('automatic'))
        self.assertEqual(migrated.context(-100)[0]['text'], 'история')
        self.assertEqual(migrated.statistics(-100, 1)['messages'], 0)
        migrated.add_message(self.update('новое', 56)['message'])
        self.assertEqual(Store(path).statistics(-100, 1)['messages'], 1)

    def test_history_retains_over_two_hundred_messages_but_removes_old_text(self):
        self.human('устарело', 999, stamp=time.time() - 31 * 86400)
        for number in range(230):
            self.human('сохранено', number)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 230)
        self.assertEqual(self.store.statistics(-100, 30)['messages'], 230)

    def test_summary_goes_to_llm_queue_weather_remains_independent(self):
        self.bot.handle(self.update('Володька, что было в чате?', 1))
        self.assertEqual(self.bot.jobs.get_nowait()['utility']['kind'], 'summary')
        self.assertEqual(self.bot.utility_jobs.qsize(), 0)
        self.store.set('request:-100', 0)
        self.bot.handle(self.update('погода в рахине', 2))
        self.assertEqual(self.bot.utility_jobs.get_nowait()['utility']['kind'], 'weather')

    def test_summary_uses_only_prior_human_group_messages_with_a_bounded_prompt(self):
        self.human('обсудили ремонт', 1)
        self.human('/help', 2)
        self.human('другой чат', 3, chat=-200)
        self.store.add_message(self.update('ответ бота', 4)['message'], human=False)
        job = self.job('Володька, что было в чате?', 100)
        self.human('сообщение после запроса', 101, stamp=job['queued_at'] + 1)
        good = {'message': {'content': json.dumps({'points': ['Обсудили ремонт и выбор обоев.']})}}
        with patch('community.http_json', return_value=good) as model, patch.object(self.bot, 'generate') as parody:
            self.bot.process_job(job)
        input_messages = json.loads(model.call_args.args[1]['messages'][1]['content'])['messages']
        self.assertEqual([row['text'] for row in input_messages], ['обсудили ремонт'])
        self.assertIn('Обсудили ремонт', self.telegram.sent[-1][1])
        self.assertIn('По 1 из 1 сообщений', self.telegram.sent[-1][1])
        parody.assert_not_called()
        rows = [{'speaker': 'Автор', 'text': 'длинное ' * 100, 'timestamp': time.time()} for _ in range(120)]
        selected = summary_input(rows)
        self.assertLess(len(selected), 120)
        self.assertLessEqual(sum(len(json.dumps(row, ensure_ascii=False)) for row in selected), 9000)

    def test_summary_failure_returns_real_excerpts_without_parody_fallback(self):
        self.human('<исходное сообщение>', 1)
        with patch('community.http_json', side_effect=APIError('timeout')), patch.object(self.bot, 'fallback_answer') as fallback:
            self.bot.process_job(self.job('/summary', 100))
        self.assertIn('&lt;исходное сообщение&gt;', self.telegram.sent[-1][1])
        self.assertIn('Выдержки из истории вместо пересказа', self.telegram.sent[-1][1])
        fallback.assert_not_called()

    def test_empty_summary_does_not_call_model_or_make_up_events(self):
        with patch('community.http_json') as model:
            self.bot.process_job(self.job('/summary', 100))
        model.assert_not_called()
        self.assertIn('сообщений для пересказа пока нет', self.telegram.sent[-1][1])

    def test_owner_private_statistics_report_bound_group_only(self):
        self.human('беседа в группе', 1)
        self.human('другая группа', 2, chat=-200)
        private = self.update('/stats', 100, user=42, chat=42)
        private['message']['chat']['type'] = 'private'
        self.bot.handle(private)
        self.bot.process_job(self.bot.utility_jobs.get_nowait())
        self.assertEqual(self.telegram.sent[-1][0], 42)
        self.assertIn('Сообщений: <b>1</b>', self.telegram.sent[-1][1])

    def test_summary_rejects_invalid_truncated_reasoning_and_secret_output(self):
        rows = [{'speaker': 'Автор', 'text': 'привет', 'timestamp': time.time()}]
        cases = [{'message': {'content': 'обычный текст'}},
                 {'message': {'content': '{"points":[]}' }},
                 {'message': {'content': '{"points":["<think>analysis</think>"]}'}},
                 {'message': {'content': json.dumps({'points': ['позвони +79991234567']})}},
                 {'message': {'content': '{"points":["привет"]}'}, 'done_reason': 'length'}]
        for result in cases:
            with self.subTest(result=result), patch('community.http_json', return_value=result):
                with self.assertRaises(APIError):
                    summary_text(self.bot.config, rows, 1, 1)

    def test_busy_conversation_suppresses_only_automatic_replies_and_can_be_disabled(self):
        self.store.set('automatic', True)
        for number in range(8):
            self.human('беседа', number, user=7 + number % 2)
        self.assertTrue(self.store.busy_chat(-100))
        self.assertFalse(self.bot.auto_eligible(-100))
        self.assertTrue(self.bot.enqueue(-100, 'Володька привет'))
        self.store.set('adaptive', False)
        self.assertTrue(self.bot.auto_eligible(-100))
        later = time.time() + 61
        self.assertFalse(self.store.busy_chat(-100, later))

    def test_configured_probability_changes_automatic_reaction(self):
        self.store.set('automatic', True)
        self.store.set('activity_probability', 0.06)
        with patch.object(self.bot.random, 'random', return_value=0.04):
            self.bot.handle(self.update('обычная беседа'))
        self.assertTrue(self.bot.jobs.get_nowait()['automatic'])

    def test_new_features_ignore_other_chats_private_users_old_updates_and_foreign_commands(self):
        for number, text in enumerate(('/summary', '/stats', '/help', '/choose кот | собака', '/rules'), 1):
            self.bot.handle(self.update(text, number, chat=-200))
            private = self.update(text, number, chat=7)
            private['message']['chat']['type'] = 'private'
            self.bot.handle(private)
            self.bot.handle(self.update(text, number, stamp=time.time() - 121))
        self.bot.handle(self.update('/summary@other_bot', 50))
        self.assertEqual(self.bot.jobs.qsize(), 0)
        self.assertEqual(self.bot.utility_jobs.qsize(), 0)
        self.assertEqual(self.telegram.sent, [])


if __name__ == '__main__':
    unittest.main()
