import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import Bot, Store, answer_problem
from common import APIError, MODEL
from question_answers import FALLBACK, PROMPT, answer_style, ends_question, thoughtful_request
from test_bot import FakeTelegram


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100)
        self.telegram = FakeTelegram()
        self.bot = Bot(dict(owner_id=42, model=MODEL, ollama_url='http://127.0.0.1:11434'),
                       telegram=self.telegram, store=self.store)
        self.bot.bot_id, self.bot.username = 99, 'parody_bot'

    def tearDown(self):
        self.temp.cleanup()

    def message(self, text, number=1, user=7, chat=-100):
        return {'update_id': number, 'message': {
            'chat': {'id': chat, 'type': 'supergroup'}, 'date': time.time(),
            'from': {'id': user, 'first_name': 'Участник', 'is_bot': False},
            'message_id': number, 'text': text}}

    def job(self, text='Бубус, почему роутер режет скорость?', **overrides):
        return dict(dict(chat_id=-100, text=text, reply_to=1, automatic=False,
                         queued_at=time.time()), **overrides)

    def result(self, text):
        return {'message': {'content': text}, 'done_reason': 'stop'}

    def test_question_suffix_and_explanations_not_keyword_substrings(self):
        for text in ('почему?', 'как устроен интернет?!', '«что случилось?»',
                     'Бубус, объясни DNS', 'Володька, подумай как решить задачу',
                     'Бубус, а почему роутер греется', '/unused?'):
            with self.subTest(text=text):
                self.assertTrue(thoughtful_request(text))
        for text in ('цацик', 'крым наш', 'он спросил почему, и ушёл', 'вопрос? потом ответ',
                     'объяснение было длинным', 'не подумай ничего такого'):
            with self.subTest(text=text):
                self.assertFalse(thoughtful_request(text))
        self.assertFalse(ends_question('Бубус, объясни DNS'))

    def test_any_final_question_activates_with_automation_disabled_and_no_nickname(self):
        self.store.set('automatic', False)
        self.bot.handle(self.message('Почему WiFi медленнее кабеля?'))
        job = self.bot.jobs.get_nowait()
        self.assertTrue(job['thoughtful'])
        self.assertFalse(job['automatic'])
        self.assertFalse(job['utility'])
        self.assertTrue(self.bot.jobs.empty())

    def test_trigger_plus_question_creates_one_explanation_and_bare_trigger_stays_short(self):
        self.bot.handle(self.message('Бубус, почему WiFi медленный?'))
        self.assertTrue(self.bot.jobs.get_nowait()['thoughtful'])
        self.assertTrue(self.bot.jobs.empty())
        self.store.set('request:-100', 0)
        self.bot.handle(self.message('цацик', number=2))
        self.assertFalse(self.bot.jobs.get_nowait()['thoughtful'])

    def test_question_does_not_bypass_scope_staleness_commands_or_deduplication(self):
        self.bot.handle(self.message('Как работает DNS?', chat=-200))
        self.bot.handle(self.message('/say@other_bot почему?', number=2))
        stale = self.message('Что случилось?', number=3)
        stale['message']['date'] -= 121
        self.bot.handle(stale)
        self.assertTrue(self.bot.jobs.empty())
        fresh = self.message('Как работает DNS?', number=4)
        self.bot.handle(fresh)
        self.store.set('request:-100', 0)
        self.bot.handle(fresh)
        self.assertEqual(self.bot.jobs.qsize(), 1)

    def test_plain_chat_without_question_or_trigger_does_not_summon_bot(self):
        with patch.object(self.bot.random, 'random', return_value=1):
            self.bot.handle(self.message('обычный разговор без вопроса'))
            self.bot.handle(self.message('почему так, я не знаю'))
        self.assertTrue(self.bot.jobs.empty())

    def test_services_keep_priority_over_general_questions(self):
        for number, (text, kind) in enumerate([
                ('Володька, какая погода в Орле?', 'weather'),
                ('Володька, какой счет ЦСКА?', 'football'),
                ('Володька, что умеешь?', 'help'),
                ('какой счет ЦСКА?', 'football'),
                ('что умеешь?', 'help')], 1):
            with self.subTest(text=text):
                self.store.set('request:-100', 0)
                self.bot.handle(self.message(text, number))
                job = self.bot.utility_jobs.get_nowait()
                self.assertEqual(job['utility']['kind'], kind)
                self.assertFalse(job['thoughtful'])
                self.assertTrue(self.bot.jobs.empty())

    def test_reply_text_and_live_human_and_bot_context_are_passed_as_data(self):
        self.store.add_message(self.message('по кабелю 100 мегабит', 2)['message'])
        self.store.add_message(dict(chat={'id': -100}, message_id=3, date=time.time(),
                                    text='проверь диапазон WiFi'), human=False)
        message = self.message('Почему?', 4)
        message['message']['reply_to_message'] = {'text': 'роутер ограничен старым стандартом',
                                                 'from': {'id': 99}}
        self.bot.handle(message)
        job = self.bot.jobs.get_nowait()
        answer = 'скорость зависит от стандарта WiFi и помех. проверь соединение по кабелю.'
        with patch('bot.http_json', return_value=self.result(answer)) as model, \
                patch('bot.find_examples') as archive, patch.object(self.store, 'approved_examples') as approved:
            self.assertEqual(self.bot.generate(job), answer)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['quoted_message'], 'роутер ограничен старым стандартом')
        self.assertEqual(data['current_request'], 'Почему?')
        self.assertEqual([row['human'] for row in data['live_context']], [True, False])
        self.assertNotIn('style_examples', data)
        archive.assert_not_called()
        approved.assert_not_called()
        self.assertEqual(model.call_args.args[1]['messages'][0]['content'], PROMPT)
        self.assertEqual(model.call_args.args[1]['options']['num_ctx'], 4096)
        self.assertEqual(model.call_args.args[1]['options']['num_predict'], 512)
        self.assertEqual(model.call_args.kwargs['timeout'], 120)

    def test_long_multiline_explanation_sends_once_and_is_not_a_learned_fact(self):
        raw = ('ты проверь скорость по кабелю: это отделит проблему линии от WiFi.\n\n'
               '1. подключи компьютер кабелем.\n2. сравни скорость рядом с роутером.\n'
               '3. если разница большая, проверь диапазон и помехи. '
               'ещё учитывай ограничения сетевой карты: старый адаптер может тормозить соединение.')
        with patch('bot.http_json', return_value=self.result(raw)):
            self.bot.process_job(self.job())
        self.assertEqual(len(self.telegram.sent), 1)
        answer = self.telegram.sent[0][1]
        self.assertGreater(len(answer), 220)
        self.assertIn('ти проверь', answer)
        self.assertIn('\n2.', answer)
        with self.store.db() as db:
            row = db.execute('SELECT learnable,rateable FROM messages WHERE human=0').fetchone()
        self.assertEqual(row, (0, 1))

    def test_normalization_preserves_inline_literals_and_fenced_code(self):
        raw = 'ты знаешь что это?\n```python\nprint("ты є і пишешь")\n```\nвот `ты пишешь` и WiFi'
        styled = answer_style(raw)
        self.assertIn('ти знаеш что ето?', styled)
        self.assertIn('print("ты є і пишешь")', styled)
        self.assertIn('`ты пишешь`', styled)
        self.assertEqual(answer_style(styled), styled)
        with patch('bot.http_json', return_value=self.result(raw)):
            self.bot.process_job(self.job())
        self.assertEqual(self.telegram.sent[0][1], styled)

    def test_truncation_retry_has_more_tokens_and_uses_question_prompt(self):
        results = [dict(self.result('оборванный ответ'), done_reason='length'), self.result('сравни скорость по кабелю и WiFi.')]
        with patch('bot.http_json', side_effect=results) as model:
            answer = self.bot.generate(self.job())
        self.assertEqual(answer, 'сравни скорость по кабелю и WiFi.')
        self.assertEqual(model.call_count, 2)
        retry = model.call_args.args[1]
        self.assertEqual(retry['options']['num_predict'], 768)
        self.assertNotIn('3–12', retry['messages'][0]['content'])
        self.assertIn('length_limit', retry['messages'][0]['content'])

    def test_invalid_question_outputs_are_not_sent_as_analysis_or_partial_code(self):
        for bad in ('<think>секретный черновик</think>', '__SILENCE__', '',
                    'Хорошо, пользователь спрашивает про роутер', '```python\nprint(1)',
                    'x' * 3001, '😀' * 1800):
            with self.subTest(bad=bad[:30]):
                with patch('bot.http_json', side_effect=[self.result(bad), self.result('проверь соединение по кабелю.')]):
                    self.assertEqual(self.bot.generate(self.job()), 'проверь соединение по кабелю.')

    def test_transport_failure_does_not_start_second_inference_or_random_parody(self):
        with patch('bot.http_json', side_effect=APIError('private-url-token')) as model, \
                patch.object(self.bot, 'fallback_answer') as parody:
            with self.assertLogs('volodymyr', 'WARNING') as logs:
                self.bot.process_job(self.job())
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.telegram.sent[0][1], FALLBACK)
        self.assertNotIn('private-url-token', '\n'.join(logs.output))
        parody.assert_not_called()

    def test_temporary_http_error_can_recover_without_falling_back_to_parody(self):
        with patch('bot.http_json', side_effect=[APIError('private', 503),
                self.result('начни с проверки скорости по кабелю.')]) as model:
            self.assertEqual(self.bot.generate(self.job()), 'начни с проверки скорости по кабелю.')
        self.assertEqual(model.call_count, 2)

    def test_group_rebinding_during_long_answer_prevents_send_to_old_group(self):
        def generate(_):
            self.store.set('chat_id', -200)
            return 'проверь скорость по кабелю.'
        with patch.object(self.bot, 'generate_thoughtful', side_effect=generate):
            self.bot.process_job(self.job())
        self.assertFalse(self.telegram.sent)
        self.assertEqual(self.store.get('last_skip:-100')['reason'], 'chat_changed')

    def test_automatic_intervention_never_switches_to_question_mode(self):
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json',
                return_value=self.result('ти ещо громче поори')) as model:
            self.bot.generate(self.job(automatic=True, thoughtful=True))
        self.assertIn('3–12', model.call_args.args[1]['messages'][0]['content'])
        self.assertEqual(model.call_args.args[1]['options']['num_predict'], 100)

    def test_question_lifetime_allows_long_cpu_generation_but_stale_job_is_dropped(self):
        with patch('bot.http_json', return_value=self.result('проверь соединение по кабелю.')) as model:
            self.bot.process_job(self.job(queued_at=time.time() - 190))
        model.assert_called_once()
        self.assertEqual(len(self.telegram.sent), 1)
        with patch('bot.http_json') as model:
            self.bot.process_job(self.job(queued_at=time.time() - 361))
        model.assert_not_called()
        self.assertEqual(len(self.telegram.sent), 1)

    def test_regular_parody_validation_keeps_existing_short_limits(self):
        self.assertEqual(answer_problem('x' * 221), 'response_too_long')
        self.assertEqual(answer_problem('строка\nстрока'), 'multiline_response')
        self.assertIsNone(answer_problem('достаточно длинное\nобъяснение', thoughtful=True))

    def test_different_answers_can_share_terms_without_short_joke_repeat_filter(self):
        self.store.add_message(dict(chat={'id': -100}, message_id=10, date=time.time(),
                                   text='проверь соединение по кабелю и сравни с WiFi'), human=False)
        text = 'проверь соединение по кабелю. если скорость нормальная, проверь помехи в WiFi.'
        with patch('bot.http_json', return_value=self.result(text)) as model:
            self.assertEqual(self.bot.generate(self.job()), answer_style(text))
        model.assert_called_once()

