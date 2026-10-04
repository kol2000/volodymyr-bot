import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import (Bot, COMMANDS, FALLBACK_REPLIES, FALLBACK_TOPICS, MOSCOW, Store,
                 TRIGGER_FORMS, TRIGGER_WORDS, WELCOME_TEXT, answer_key, repeated_answer,
                 response_problem, surzhyk_text, valid_answer)
from common import APIError, HTMLMessage, MODEL, Telegram, http_json
from chat_services import ServiceError
import retrieval


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, chat_id, text, reply_to=None):
        self.sent.append((chat_id, text, reply_to))
        if self.fail:
            raise APIError('Сетевая ошибка или тайм-аут')
        return {'chat': {'id': chat_id}, 'message_id': 10000 + len(self.sent),
                'date': time.time(), 'text': text}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Path(self.folder.name) / 'bot.sqlite3'
        self.store = Store(self.database)
        self.telegram = FakeTelegram()
        self.bot = Bot({'owner_id': 42, 'model': MODEL, 'ollama_url': 'http://127.0.0.1:11434'},
                       telegram=self.telegram, store=self.store)
        self.bot.bot_id = 99
        self.bot.username = 'parody_bot'

    def tearDown(self):
        self.folder.cleanup()

    def update(self, text, user=42, chat=-100, number=1, age=0):
        return {'update_id': number, 'message': {'chat': {'id': chat, 'type': 'supergroup'},
                'from': {'id': user, 'first_name': 'Tester', 'is_bot': False},
                'message_id': number, 'date': time.time() - age, 'text': text}}

    def bind(self):
        self.bot.handle(self.update('/bubus'))
        self.bot.jobs.get_nowait()
        self.store.set('request:-100', 0)

    def test_group_binding_is_owner_only_and_auto_off(self):
        self.bot.handle(self.update('/bubus', user=7))
        self.assertIsNone(self.store.get('chat_id'))
        self.bind()
        self.assertEqual(self.store.get('chat_id'), -100)
        self.assertFalse(self.store.get('automatic'))
        self.bot.handle(self.update('/auto_on', user=7, number=2))
        self.assertFalse(self.store.get('automatic'))
        self.bot.handle(self.update('/auto_on', number=3))
        self.assertTrue(self.store.get('automatic'))

    def test_wrong_group_and_duplicate_update_do_not_queue(self):
        self.bind()
        self.bot.handle(self.update('/bubus привет', chat=-200, user=7, number=2))
        self.assertEqual(self.bot.jobs.qsize(), 0)
        message = self.update('/bubus интернет опять отвалился', user=7, number=3)
        self.bot.handle(message)
        self.bot.handle(message)
        self.assertEqual(self.bot.jobs.qsize(), 1)

    def test_old_messages_are_not_replayed(self):
        self.bind()
        self.bot.handle(self.update('/bubus старый текст', number=2, age=3600))
        self.assertEqual(self.bot.jobs.qsize(), 0)

    def test_old_say_command_is_ignored_even_as_reply(self):
        self.bind()
        message = self.update('/say где пивас', number=2)
        message['message']['reply_to_message'] = {'from': {'id': self.bot.bot_id}}
        self.bot.handle(message)
        self.assertEqual(self.bot.jobs.qsize(), 0)

    def test_removed_bind_does_not_connect_or_appear_in_menu(self):
        self.bot.handle(self.update('/bind'))
        self.assertIsNone(self.store.get('chat_id'))
        self.assertNotIn('/bind', [command for command, _ in COMMANDS])

    def test_first_bubus_connects_and_queues_random_phrase(self):
        self.bot.handle(self.update('/bubus'))
        self.assertEqual(self.store.get('chat_id'), -100)
        job = self.bot.jobs.get_nowait()
        self.assertTrue(job['random_quote'])
        self.assertFalse(job['automatic'])

    def test_bare_bubus_by_member_does_not_disable_automatic_mode(self):
        self.bind()
        self.store.set('automatic', True)
        self.bot.handle(self.update('/bubus@parody_bot', user=7, number=2))
        self.assertTrue(self.bot.jobs.get_nowait()['random_quote'])
        self.assertTrue(self.store.get('automatic'))

    def test_old_bubus_does_not_rebind_group(self):
        self.bind()
        self.bot.handle(self.update('/bubus', chat=-200, age=3600, number=2))
        self.assertEqual(self.store.get('chat_id'), -100)

    def test_random_phrase_skips_recent_and_invalid_without_model_call(self):
        self.remember_answer('а шо там хоть поменяли то')
        job = {'chat_id': -100, 'text': '', 'random_quote': True, 'automatic': False}
        candidates = ['А шо там хоть поменяли то!', '<think>analysis</think>',
                      'номер 12345678', 'ну ето уже смешно))', 'ти заебал уже))']
        with patch('bot.random_candidates', return_value=candidates), patch('bot.http_json') as model:
            self.assertEqual(self.bot.generate(job), 'ти заебал уже))')
        model.assert_not_called()

    def test_plain_reply_queues_current_text(self):
        self.bind()
        message = self.update('где пивас', user=7, number=2)
        message['message']['reply_to_message'] = {'from': {'id': self.bot.bot_id}}
        self.bot.handle(message)
        self.assertEqual(self.bot.jobs.get_nowait()['text'], 'где пивас')

    def test_all_trigger_words_queue_direct_response_with_auto_off(self):
        self.bind()
        for i, word in enumerate(TRIGGER_WORDS, 2):
            with self.subTest(word=word):
                self.store.set('request:-100', 0)
                text = f'ну {word.upper()}! опять обсуждаем'
                self.bot.handle(self.update(text, user=7, number=i))
                job = self.bot.jobs.get_nowait()
                self.assertEqual(job['text'], text)
                self.assertEqual(job['trigger_words'], [word])
                self.assertFalse(job['automatic'])
                self.assertFalse(job['random_quote'])

    def test_trigger_substrings_and_other_bot_commands_do_not_activate(self):
        self.bind()
        with patch.object(self.bot.random, 'random', return_value=1):
            self.bot.handle(self.update('киевский цацапка бубусик цацыга хрякать свиньями сосатками крымский криминал дагестан', user=7, number=2))
            self.bot.handle(self.update('/say@other_bot бубус', user=7, number=3))
        self.assertEqual(self.bot.jobs.qsize(), 0)

    def test_name_inflections_are_canonical_direct_triggers(self):
        self.bind()
        for number, (form, name) in enumerate(TRIGGER_FORMS.items(), 2):
            if form == name:
                continue
            with self.subTest(form=form):
                self.store.set('request:-100', 0)
                self.bot.handle(self.update(f'делаем {form.upper()} вумним!', user=7, number=number))
                self.assertEqual(self.bot.jobs.get_nowait()['trigger_words'], [name])

    def test_screenshot_phrase_at_midnight_queues_one_direct_response(self):
        self.bind()
        midnight = datetime(2026, 10, 5, 0, 1, tzinfo=MOSCOW).timestamp()
        with patch('time.time', return_value=midnight):
            self.bot.handle(self.update('делаем Володьку Бубуса вумним!', user=7, number=2))
        self.assertEqual(self.bot.jobs.qsize(), 1)
        job = self.bot.jobs.get_nowait()
        self.assertEqual(job['trigger_words'], ['володька', 'бубус'])
        self.assertFalse(job['automatic'])

    def test_multiple_trigger_words_one_job_and_cooldown(self):
        self.bind()
        self.bot.handle(self.update('бубус цаца БУБУС', user=7, number=2))
        self.bot.handle(self.update('война', user=7, number=3))
        self.assertEqual(self.bot.jobs.qsize(), 1)
        self.assertEqual(self.bot.jobs.get_nowait()['trigger_words'], ['бубус', 'цаца'])

    def test_crimea_phrase_case_whitespace_and_single_match(self):
        self.bind()
        for number, text in enumerate(('КРЫМ НАШ!', 'крым   наш', 'крым\tнаш', 'крым\u00a0наш'), 2):
            with self.subTest(text=text):
                self.store.set('request:-100', 0)
                self.bot.handle(self.update(text, user=7, number=number))
                self.assertEqual(self.bot.jobs.qsize(), 1)
                job = self.bot.jobs.get_nowait()
                self.assertEqual(job['trigger_words'], ['крым наш'])
                self.assertFalse(job['automatic'])

    def test_phrase_and_new_words_make_only_one_response(self):
        self.bind()
        self.bot.handle(self.update('крым наш, крим и ДАГ', user=7, number=2))
        self.assertEqual(self.bot.jobs.qsize(), 1)
        self.assertEqual(self.bot.jobs.get_nowait()['trigger_words'], ['крым наш', 'крим', 'даг'])

    def test_bare_crimea_phrase_has_no_old_archive_context(self):
        with patch('bot.find_examples') as archive, patch('bot.http_json', return_value={'message': {'content': 'опять спорить начинаешь'}}) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='КРЫМ   НАШ!')), 'опять спорить начинаеш')
        archive.assert_not_called()
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['live_context'], [])
        self.assertEqual(data['style_examples'], [])

    def test_trigger_context_is_supplied_to_model(self):
        job = {'chat_id': -100, 'text': 'бубус ты где', 'automatic': False,
               'trigger_words': ['бубус']}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value={'message': {'content': 'ти ещо громче поори'}}) as model:
            self.assertEqual(self.bot.generate(job), 'ти ещо громче поори')
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['trigger_words'], ['бубус'])

    def test_model_spelling_is_corrected_without_retry_or_changing_user_text(self):
        raw = 'ты пишешь что это было бы ещё сейчас'
        expected = 'ти пишеш что ето било би ещо сечас'
        job = self.manual_job(text='ты знаешь что это?')
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value={'message': {'content': raw}}) as model:
            self.assertEqual(self.bot.generate(job), expected)
        self.assertEqual(model.call_count, 1)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['current_request'], 'ты знаешь что это?')

    def test_archive_random_quote_uses_same_spelling(self):
        with patch('bot.random_candidates', return_value=['ты бля знаешь что это было']), patch('bot.http_json') as model:
            self.assertEqual(self.bot.generate(self.manual_job(random_quote=True)),
                             'ти бля знаеш что ето било')
        model.assert_not_called()

    def test_spelling_changes_do_not_evade_repeat_history(self):
        self.remember_answer('ти бля знаеш что ето било')
        self.assertTrue(repeated_answer('ты бля знаешь что это было', self.store.recent_answers(-100)))
        replies = [{'message': {'content': 'ты бля знаешь что это было'}},
                   {'message': {'content': 'ты можешь помолчать хоть минуту'}}]
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=replies) as model:
            self.assertEqual(self.bot.generate(self.manual_job()), 'ти можеш помолчать хоть минуту')
        self.assertEqual(model.call_count, 2)

    def test_sent_response_and_saved_history_match_screenshot_style(self):
        self.bind()
        with patch.object(self.bot, 'generate', return_value='ахуел, так и знал, что ты опять в пизде'):
            self.bot.process_job(self.manual_job())
        expected = 'ахуел, так и знал, что ти опять в пизде'
        self.assertEqual(self.telegram.sent[-1][1], expected)
        self.assertEqual(self.store.recent_answers(-100)[0], expected)

    def test_spelling_preserves_archive_soft_signs_and_word_boundaries(self):
        self.assertEqual(surzhyk_text('ТЫ ПИШЕШЬ, знаешь, говоришь; опять хоть делать есть фильм тысяча'),
                         'ТИ ПИШЕШ, знаеш, говориш; опять хоть делать есть фильм тысяча')
        self.assertEqual(surzhyk_text('__SILENCE__'), '__SILENCE__')
        for text in FALLBACK_REPLIES + tuple(text for _, pool in FALLBACK_TOPICS for text in pool):
            self.assertEqual(surzhyk_text(text), text)

    def test_plain_amount_and_weather_use_separate_queue_without_llm_job(self):
        self.bind()
        for number, text in enumerate(('100 USDT', 'Володька, какая погода в городе Орёл', '/weather Орёл', '/convert 100 USD'), 2):
            self.store.set('request:-100', 0)
            self.bot.handle(self.update(text, user=7, number=number))
            self.assertEqual(self.bot.jobs.qsize(), 0)
            job = self.bot.utility_jobs.get_nowait()
            self.assertFalse(job['automatic'])
            self.assertIsNotNone(job['utility'])

    def test_currency_and_weather_send_api_result_without_model_or_fallback(self):
        self.bind()
        self.bot.handle(self.update('100 USDT', user=7, number=2))
        job = self.bot.utility_jobs.get_nowait()
        report = HTMLMessage('💱 <b>100 USDT</b>\n💵 <b>≈ 99,90 USD</b>\n💰 <b>≈ 8 441,55 RUB</b>')
        with patch.object(self.bot.services, 'answer', return_value=report), patch.object(self.bot, 'generate') as model, patch.object(self.bot, 'fallback_answer') as fallback:
            self.bot.process_job(job)
        self.assertEqual(self.telegram.sent[-1], (-100, report, 2))
        self.assertIsInstance(self.telegram.sent[-1][1], HTMLMessage)
        model.assert_not_called()
        fallback.assert_not_called()

    def test_busy_full_llm_queue_does_not_block_utility_queue(self):
        self.bind()
        self.bot.active_job = True
        for _ in range(4):
            self.bot.jobs.put_nowait(self.manual_job())
        self.bot.handle(self.update('100 USDT', user=7, number=2))
        self.assertEqual(self.bot.jobs.qsize(), 4)
        self.assertEqual(self.bot.utility_jobs.qsize(), 1)

    def test_service_failure_does_not_fabricate_weather_or_use_parody_fallback(self):
        self.bind()
        self.bot.handle(self.update('погода Орёл', user=7, number=2))
        with patch.object(self.bot.services, 'answer', side_effect=ServiceError('stale_weather')), patch.object(self.bot, 'generate') as model, patch.object(self.bot, 'fallback_answer') as fallback:
            self.bot.process_job(self.bot.utility_jobs.get_nowait())
        self.assertEqual(self.telegram.sent[-1][1], 'сечас свежую погоду не достал')
        model.assert_not_called()
        fallback.assert_not_called()

    def test_utility_routing_obeys_group_bot_command_age_and_duplicate_guards(self):
        self.bind()
        message = self.update('100 USDT', user=7, number=2)
        self.bot.handle(message)
        self.bot.handle(message)
        self.assertEqual(self.bot.utility_jobs.qsize(), 1)
        self.bot.utility_jobs.get_nowait()
        self.store.set('request:-100', 0)
        self.bot.handle(self.update('100 USDT', chat=-200, user=7, number=3))
        self.bot.handle(self.update('/weather@other_bot Орёл', user=7, number=4))
        self.bot.handle(self.update('погода Орёл', user=7, number=5, age=121))
        bot_message = self.update('100 USDT', number=6)
        bot_message['message']['from']['is_bot'] = True
        self.bot.handle(bot_message)
        self.assertTrue(self.bot.utility_jobs.empty())

    def test_utility_waits_for_send_pause_and_rechecks_binding_after_wait(self):
        self.bind()
        base = time.time()
        clock = [base]
        with patch('bot.time.time', side_effect=lambda: clock[0]):
            self.store.claim_send('manual', -100)
            job = self.manual_job(utility={'kind': 'currency', 'amounts': []})
            def advance(seconds):
                clock[0] += seconds
            with patch('bot.time.sleep', side_effect=advance) as wait, patch.object(self.bot.services, 'answer', return_value='100 USD ≈ 8450 RUB'):
                self.bot.process_job(job)
            wait.assert_called_once_with(15)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.store.counts()['manual'], 2)

    def test_utility_wait_cancels_if_group_changes(self):
        self.bind()
        base = time.time()
        with patch('bot.time.time', return_value=base):
            self.store.claim_send('manual', -100)
            job = self.manual_job(utility={'kind': 'currency', 'amounts': []})
            with patch('bot.time.sleep', side_effect=lambda _: self.store.set('chat_id', -200)), patch.object(self.bot.services, 'answer', return_value='100 USD ≈ 8450 RUB'):
                self.bot.process_job(job)
        self.assertEqual(self.telegram.sent, [])
        self.assertEqual(self.store.get('last_skip:-100')['reason'], 'chat_changed')

    def test_addressed_command_and_other_bot_routing(self):
        self.bind()
        self.bot.handle(self.update('/bubus@other_bot где пивас', number=2))
        self.assertEqual(self.bot.jobs.qsize(), 0)
        self.bot.handle(self.update('/bubus@parody_bot где пивас', number=3))
        self.assertEqual(self.bot.jobs.get_nowait()['text'], 'где пивас')
        self.assertEqual(self.store.context(-100)[-1]['text'], 'где пивас')

    def test_reply_summoning_other_bot_is_ignored(self):
        self.bind()
        message = self.update('@friedrich_bot', number=2)
        message['message']['reply_to_message'] = {'from': {'id': self.bot.bot_id}}
        with patch.object(self.bot.random, 'random', return_value=0):
            self.bot.handle(message)
        self.assertEqual(self.bot.jobs.qsize(), 0)
        self.bot.handle(self.update('@parody_bot', number=3))
        self.assertEqual(self.bot.jobs.qsize(), 1)

    def remember_answer(self, text):
        self.store.add_message(self.update(text, number=100)['message'], human=False)

    def test_repeat_is_regenerated_and_old_answer_is_not_live_context(self):
        self.remember_answer('а шо там хоть поменяли то')
        job = {'chat_id': -100, 'text': 'где пивас', 'automatic': False}
        replies = [{'message': {'content': 'А шо там хоть поменяли то))'}},
                   {'message': {'content': 'ти холодильник хоть открывал))'}}]
        with patch('bot.find_examples', return_value=[{'context': '', 'response': 'а шо там хоть поменяли то'}]):
            with patch('bot.http_json', side_effect=replies) as request:
                self.assertEqual(self.bot.generate(job), 'ти холодильник хоть открывал))')
        self.assertEqual(request.call_count, 2)
        data = json.loads(request.call_args_list[0].args[1]['messages'][1]['content'])
        self.assertEqual(data['current_request'], 'где пивас')
        self.assertEqual(data['style_examples'], [])
        self.assertEqual(data['live_context'], [])
        self.assertNotIn('recent_bot_responses_do_not_repeat', data)
        self.assertNotIn('а шо там хоть поменяли то', json.dumps(data, ensure_ascii=False))

    def test_repeated_second_generation_uses_local_reply_and_normal_quota(self):
        self.bind()
        self.remember_answer('а шо там хоть поменяли то')
        job = {'chat_id': -100, 'text': 'мастер опять не пришел ремонтировать',
               'reply_to': 2, 'automatic': False, 'queued_at': time.time()}
        duplicate = {'message': {'content': 'А ШО ТАМ ХОТЬ ПОМЕНЯЛИ ТО!'}}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value=duplicate) as request:
            self.bot.process_job(job)
        self.assertEqual(request.call_count, 2)
        reply = self.telegram.sent[-1][1]
        self.assertIn(reply, FALLBACK_REPLIES)
        self.assertNotEqual(answer_key(reply), answer_key('а шо там хоть поменяли то'))
        self.assertEqual(self.store.counts().get('manual', 0), 1)
        self.assertEqual(self.store.recent_answers(-100)[-1], 'а шо там хоть поменяли то')
        self.assertIn(reply, self.store.recent_answers(-100))

    def manual_job(self, **overrides):
        return dict({'chat_id': -100, 'text': 'цацик', 'reply_to': 2,
                     'automatic': False, 'queued_at': time.time()}, **overrides)

    def test_invalid_output_types_use_a_local_reply(self):
        self.bind()
        for result in ([], {'message': 'bad'}, {'message': {'content': ['bad']}}, {},
                       {'message': {'content': '<think>bad</think>'}},
                       {'message': {'content': ''}}, {'message': {'content': '__SILENCE__'}}):
            with self.subTest(result=result):
                self.store.set('request:-100', 0)
                with patch.object(self.store, 'claim_send', return_value=1):
                    with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value=result):
                        before = len(self.telegram.sent)
                        self.bot.process_job(self.manual_job())
                self.assertEqual(len(self.telegram.sent), before + 1)
                self.assertIsNotNone(valid_answer(self.telegram.sent[-1][1]))
                self.assertNotIn('Не получилось', self.telegram.sent[-1][1])

    def test_timeout_has_no_second_model_request_and_no_diagnostic_leak(self):
        self.bind()
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=APIError('private-url-token')) as model:
            with self.assertLogs('volodymyr', level='INFO') as logs:
                self.bot.process_job(self.manual_job())
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertIn('model_unavailable', '\n'.join(logs.output))
        self.assertNotIn('private-url-token', '\n'.join(logs.output))
        self.assertEqual(self.store.counts()['manual'], 1)

    def test_temporary_http_error_can_recover_before_fallback(self):
        good = {'message': {'content': 'да тут я хватит орать'}}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=[APIError('private', 503), good]) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='цацик ты где')), good['message']['content'])
        self.assertEqual(model.call_count, 2)

    def test_retry_drops_style_examples_and_reports_reason_to_model(self):
        self.remember_answer('да тут я хватит орать')
        bad = {'message': {'content': 'да тут я хватит орать'}}
        good = {'message': {'content': 'шо опять надо то'}}
        examples = [{'context': 'other topic', 'response': 'old style text'}]
        with patch('bot.find_examples', return_value=examples), patch('bot.http_json', side_effect=[bad, good]) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='цацик ты где')), good['message']['content'])
        first = json.loads(model.call_args_list[0].args[1]['messages'][1]['content'])
        retry = json.loads(model.call_args_list[1].args[1]['messages'][1]['content'])
        self.assertEqual(first['style_examples'], examples)
        self.assertEqual(retry['style_examples'], [])
        self.assertEqual(retry['previous_failure'], 'repeated_response')
        self.assertEqual(retry['current_request'], 'цацик ты где')
        self.assertNotIn('recent_bot_responses_do_not_repeat', retry)
        self.assertNotIn('да тут я хватит орать', json.dumps(retry, ensure_ascii=False))

    def test_fallback_is_valid_varied_and_survives_recent_topic_exhaustion(self):
        for text in FALLBACK_REPLIES + tuple(text for _, pool in FALLBACK_TOPICS for text in pool):
            self.assertEqual(valid_answer(text), text)
            self.assertLessEqual(len(text.split()), 12)
        for i, text in enumerate(FALLBACK_REPLIES[:30], 500):
            self.store.add_message(self.update(text, number=i)['message'], human=False)
        reply = self.bot.fallback_answer(self.manual_job(text='ordinary message'))
        self.assertNotIn(reply, FALLBACK_REPLIES[:30])
        _, topic = FALLBACK_TOPICS[0]
        with patch.object(self.store, 'recent_answers', return_value=list(topic)):
            self.assertIn(self.bot.fallback_answer(self.manual_job(text='война')), FALLBACK_REPLIES)
        with patch.object(self.store, 'recent_answers', return_value=[]):
            self.assertIn(self.bot.fallback_answer(self.manual_job(text='война')), topic)

    def test_fallback_obeys_quota_cooldown_rebinding_and_age(self):
        self.bind()
        with patch.object(self.bot, 'generate', return_value=None):
            with patch.object(self.store, 'claim_send', return_value=None):
                self.bot.process_job(self.manual_job())
            self.bot.process_job(self.manual_job(chat_id=-200))
            self.bot.process_job(self.manual_job(queued_at=time.time() - 181))
        self.assertEqual(self.telegram.sent, [])
        with patch.object(self.bot, 'generate', return_value=None):
            self.bot.process_job(self.manual_job())
            self.bot.process_job(self.manual_job())
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.store.counts()['manual'], 1)
        def move(_):
            self.store.set('chat_id', -200)
            return None
        with patch.object(self.bot, 'generate', side_effect=move):
            self.bot.process_job(self.manual_job())
        self.assertEqual(len(self.telegram.sent), 1)

    def test_fallback_does_not_reply_to_automatic_failure_or_silence(self):
        self.bind()
        with patch.object(self.bot, 'auto_eligible', return_value=True):
            with patch.object(self.bot, 'generate', return_value=None), patch.object(self.bot, 'fallback_answer') as fallback:
                self.bot.process_job(self.manual_job(automatic=True))
                fallback.assert_not_called()
        with patch.object(self.bot, 'generate', return_value='__SILENCE__'), patch.object(self.bot, 'fallback_answer') as fallback:
            self.bot.process_job(self.manual_job(automatic=True))
            fallback.assert_not_called()
        self.assertEqual(self.telegram.sent, [])

    def test_fallback_recovers_from_local_retrieval_failure(self):
        import sqlite3
        self.bind()
        with patch('bot.find_examples', side_effect=sqlite3.OperationalError('private data')):
            self.bot.process_job(self.manual_job(text='интернет опять пропал'))
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertIsNotNone(valid_answer(self.telegram.sent[-1][1]))

    def test_uncertain_fallback_send_is_not_retried(self):
        self.bind()
        self.telegram.fail = True
        with patch.object(self.bot, 'generate', return_value=None):
            self.bot.process_job(self.manual_job())
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.store.counts()['manual'], 1)

    def test_near_repetition_catches_changed_opening_and_spelling(self):
        old = 'мира а ти чё уже и бубуську забыл'
        self.assertTrue(repeated_answer('ето шо уже и бубуську забил?', [old]))
        self.assertTrue(repeated_answer('ти холодильник хоть открывал))', ['ты холодильник хоть открывал']))
        self.assertFalse(repeated_answer('ну ти шо пивас уже закончился', ['ну ти шо роутер не проверял']))
        self.assertFalse(repeated_answer('шо опять надо то', ['да тут я хватит орать']))

    def test_cyborg_and_forgotten_bubus_need_an_explicit_subject(self):
        self.assertEqual(response_problem('цыц опять с киборгом?', subject='цацыг'), 'unrelated_topic')
        self.assertEqual(response_problem('шо за киборги опять', subject='хряк'), 'unrelated_topic')
        self.assertIsNone(response_problem('шо за киборг такой', subject='кто такой киборг'))
        self.assertEqual(response_problem('а шо уже и бубуську забыл?', subject='где мой брат бубус'), 'stale_catchphrase')
        self.assertIsNone(response_problem('да ти бубуську забыл', subject='я забыл бубуську'))

    def test_bare_trigger_uses_fresh_call_without_archive_or_previous_output(self):
        self.bind()
        self.remember_answer('цыц опять с киборгом?')
        self.store.add_message(self.update('вчера обсуждали старую тему', number=9)['message'])
        with patch('bot.find_examples') as archive, patch('bot.http_json', return_value={'message': {'content': 'шо тебе надо то'}}) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='СОСАТКА!')), 'шо тебе надо то')
        archive.assert_not_called()
        payload = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(payload['live_context'], [])
        self.assertEqual(payload['style_examples'], [])
        self.assertNotIn('киборг', json.dumps(model.call_args.args[1], ensure_ascii=False))

    def test_archive_catchphrases_are_not_inserted_in_an_unrelated_request(self):
        rows = [{'context': '', 'response': 'цыц опять с киборгом?'},
                {'context': '', 'response': 'а шо уже и бубуську забыл?'},
                {'context': 'обычная беседа', 'response': 'да тут я чего надо'}]
        with patch('bot.find_examples', return_value=rows), patch('bot.http_json', return_value={'message': {'content': 'да тут я чего надо'}}) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='где мой брат бубус')), 'да тут я чего надо')
        payload = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(payload['style_examples'], [rows[-1]])

    def test_unrelated_cyborg_twice_gets_a_local_reply(self):
        self.bind()
        with patch('bot.http_json', return_value={'message': {'content': 'цыц опять с киборгом?'}}) as model:
            with self.assertLogs('volodymyr', level='INFO') as logs:
                self.bot.process_job(self.manual_job(text='цацыг'))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertNotIn('киборг', self.telegram.sent[-1][1])
        self.assertIn('unrelated_topic', '\n'.join(logs.output))
        self.assertIn('резервная реплика', '\n'.join(logs.output))

    def test_near_repeat_outside_previous_ten_is_rejected(self):
        self.remember_answer('ти холодильник хоть открывал')
        for number in range(200, 220):
            self.store.add_message(self.update(f'другая реплика номер {number}', number=number)['message'], human=False)
        replies = [{'message': {'content': 'ты холодильник хоть открывал?'}},
                   {'message': {'content': 'пиво ищи сам чего пристал'}}]
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=replies) as model:
            self.assertEqual(self.bot.generate(self.manual_job(text='где пивас')), 'пиво ищи сам чего пристал')
        self.assertEqual(model.call_count, 2)

    def test_group_cooldown_is_logged_without_technical_notice(self):
        self.bind()
        self.bot.handle(self.update('цацыг', number=2))
        self.bot.handle(self.update('хряк', number=3))
        self.assertEqual(self.bot.jobs.qsize(), 1)
        self.assertEqual(self.telegram.sent, [])
        self.assertEqual(self.store.get('last_skip:-100')['reason'], 'request_cooldown')
        self.store.set('request:-100', 0)
        with patch.object(self.store, 'counts', return_value={'manual': 30}):
            self.bot.handle(self.update('сосатка', number=4))
        self.assertEqual(self.bot.jobs.qsize(), 2)
        self.assertEqual(self.telegram.sent, [])

    def test_manual_triggers_still_send_after_thirty_attempts_and_restart(self):
        self.bind()
        base = time.time()
        day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
        with self.store.db() as db:
            for i in range(30):
                db.execute('INSERT INTO sends(timestamp,day,kind,chat_id,status) VALUES (?,?,?,?,?)',
                           (base - 60 - i * 16, day, 'manual', -100, 'sent'))
        self.bot.store = Store(self.database)
        with patch.object(self.bot, 'generate', return_value='шо опять надо то'):
            for number, text in enumerate(('володька', 'где бубуська?', 'крым наш'), 2):
                with patch('bot.time.time', return_value=base + number * 16):
                    self.bot.handle(self.update(text, user=7, number=number))
                    self.assertEqual(self.bot.jobs.qsize(), 1)
                    self.bot.process_job(self.bot.jobs.get_nowait())
        self.assertEqual(len(self.telegram.sent), 3)
        self.assertEqual(self.store.counts()['manual'], 33)

    def test_queue_full_reason_and_received_time_are_visible_in_status(self):
        self.bind()
        for number in range(2, 7):
            self.store.set('request:-100', 0)
            self.bot.handle(self.update('бубус', user=7, number=number))
        self.assertEqual(self.bot.jobs.qsize(), 4)
        self.assertEqual(self.store.get('last_skip:-100')['reason'], 'queue_full')
        self.bot.active_job = True
        self.bot.handle(self.update('/status', number=7))
        status = self.telegram.sent[-1][1]
        self.assertIn('без дневного лимита', status)
        self.assertIn('обработка: идёт', status)
        self.assertIn('queue_full', status)
        self.assertNotIn('ещё не получено', status)

    def test_send_cooldown_and_telegram_wait_record_distinct_reasons(self):
        self.bind()
        with patch.object(self.bot, 'generate', return_value='шо опять надо то'):
            self.bot.process_job(self.manual_job())
            self.bot.process_job(self.manual_job())
            self.assertEqual(self.store.get('last_skip:-100')['reason'], 'send_cooldown')
            self.store.set('blocked_until', time.time() + 60)
            self.bot.process_job(self.manual_job())
        self.assertEqual(self.store.get('last_skip:-100')['reason'], 'telegram_retry_after')
        self.assertEqual(len(self.telegram.sent), 1)

    def test_private_owner_can_still_see_cooldown_notice(self):
        for number, text in enumerate(('цацыг', 'хряк'), 2):
            message = self.update(text, chat=42, number=number)
            message['message']['chat']['type'] = 'private'
            self.bot.handle(message)
        self.assertEqual(self.bot.jobs.qsize(), 1)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertIn('Подожди', self.telegram.sent[-1][1])

    def test_invalid_model_responses_are_retried(self):
        job = {'chat_id': -100, 'text': 'хуй туды', 'automatic': False}
        bad_results = [
            {'message': {'content': ''}},
            {'message': {'content': 'первая строка\nвторая строка'}},
            {'message': {'content': 'Хорошо, пользователь пишет грубо'}},
            {'message': {'content': 'x' * 221}},
            {'message': {'content': '__SILENCE__'}},
            {'message': {'content': 'оборванный ответ'}, 'done_reason': 'length'},
            {'error': 'unused sensitive diagnostic'},
        ]
        for bad in bad_results:
            with self.subTest(bad=bad):
                good = {'message': {'content': 'ну ти и разговорчивый))'}}
                with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=[bad, good]) as model:
                    with self.assertLogs('volodymyr', level='WARNING') as logs:
                        self.assertEqual(self.bot.generate(job), 'ну ти и разговорчивый))')
                self.assertEqual(model.call_count, 2)
                self.assertNotIn('хуй туды', '\n'.join(logs.output))
                self.assertNotIn('unused sensitive diagnostic', '\n'.join(logs.output))
                if bad.get('done_reason') == 'length':
                    self.assertEqual(model.call_args.args[1]['options']['num_predict'], 160)

    def test_rejected_response_reason_logged_without_its_text(self):
        job = {'chat_id': -100, 'text': 'обращение', 'automatic': False}
        bad = {'message': {'content': 'private-first-line\nprivate-second-line'}}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value=bad) as model:
            with self.assertLogs('volodymyr', level='WARNING') as logs:
                self.assertIsNone(self.bot.generate(job))
        self.assertEqual(model.call_count, 2)
        self.assertTrue(all('multiline_response' in line for line in logs.output))
        self.assertNotIn('private-first-line', '\n'.join(logs.output))

    def test_automatic_silence_is_not_retried(self):
        job = {'chat_id': -100, 'text': 'обсуждение', 'automatic': True}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value={'message': {'content': '__SILENCE__'}}) as model:
            self.assertEqual(self.bot.generate(job), '__SILENCE__')
        self.assertEqual(model.call_count, 1)

    def join_update(self, username='leonadosasa', number=10, **kwargs):
        update = self.update('', number=number, **kwargs)
        update['message']['new_chat_members'] = [
            {'id': 77, 'username': username, 'first_name': 'Member', 'is_bot': False}]
        return update

    def test_target_join_gets_exact_greeting_with_automation_off_and_no_model(self):
        self.bind()
        self.store.set('automatic', False)
        with patch.object(self.bot, 'generate') as model:
            self.bot.handle(self.join_update(username='LeoNadoSasa'))
        model.assert_not_called()
        self.assertEqual(self.telegram.sent[-1], (-100, 'оо алкаш епти ти де бил?', 10))
        self.assertEqual(self.store.counts(), {'greeting': 1})

    def test_other_user_sender_and_wrong_group_do_not_get_greeting(self):
        self.bind()
        sender = self.join_update(username='someone_else')
        sender['message']['from']['username'] = 'leonadosasa'
        self.bot.handle(sender)
        self.bot.handle(self.join_update(username='leonardosasa', number=11))
        self.bot.handle(self.join_update(chat=-200, number=12))
        self.bot.handle(self.join_update(age=3600, number=13))
        self.assertEqual(self.telegram.sent, [])

    def test_join_matching_member_not_inviting_bot(self):
        self.bind()
        update = self.join_update()
        update['message']['from']['is_bot'] = True
        self.bot.handle(update)
        self.assertEqual(self.telegram.sent[-1][1], WELCOME_TEXT)

    def test_greeting_duplicate_event_stays_suppressed_after_restart(self):
        self.bind()
        update = self.join_update()
        self.bot.handle(update)
        restarted = Bot(self.bot.config, telegram=self.telegram, store=Store(self.database))
        restarted.handle(update)
        self.assertEqual(len(self.telegram.sent), 1)
        restarted.handle(self.join_update(number=11))
        self.assertEqual(len(self.telegram.sent), 2)

    def test_uncertain_greeting_is_not_retried(self):
        self.bind()
        self.telegram.fail = True
        update = self.join_update()
        self.bot.handle(update)
        self.bot.handle(update)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.store.counts(), {'greeting': 1})

    def test_greeting_bypasses_manual_quota_and_cooldown(self):
        self.bind()
        with self.store.db() as db:
            day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
            for _ in range(30):
                db.execute('INSERT INTO sends(timestamp,day,kind,chat_id,status) VALUES (?,?,?,?,?)',
                           (time.time(), day, 'manual', -100, 'sent'))
        self.bot.handle(self.join_update())
        self.assertEqual(self.telegram.sent[-1][1], WELCOME_TEXT)
        self.assertEqual(self.store.counts(), {'manual': 30, 'greeting': 1})

    def test_repeat_history_survives_restart(self):
        self.remember_answer('а шо там хоть поменяли то')
        restarted = Store(self.database)
        self.assertEqual(restarted.recent_answers(-100), ['а шо там хоть поменяли то'])
        self.assertEqual(restarted.recent_answers(-200), [])

    def test_rebind_stops_old_group_and_turns_off_auto(self):
        self.bind()
        self.store.set('automatic', True)
        self.bot.handle(self.update('/bubus', chat=-200, number=2))
        self.assertFalse(self.store.get('automatic'))
        self.assertFalse(self.bot.allowed(-100))
        self.assertTrue(self.bot.allowed(-200))

    def test_automatic_mode_runs_all_hours_with_fresh_context(self):
        self.bind()
        self.store.set('automatic', True)
        for hour in (0, 3, 9, 10, 15, 23):
            now = datetime(2026, 10, 5, hour, 1, tzinfo=MOSCOW).timestamp()
            with patch.object(self.store, 'latest_human', return_value=now - 60):
                self.assertTrue(self.bot.auto_eligible(-100, now=now))
        day = datetime(2026, 10, 5, 15, 0, tzinfo=MOSCOW).timestamp()
        with patch.object(self.store, 'latest_human', return_value=day - 7201):
            self.assertFalse(self.bot.auto_eligible(-100, now=day))

    def test_daily_limit_and_cooldown_survive_restart(self):
        base = time.time()
        with patch('bot.time.time', return_value=base):
            first = self.store.claim_send('automatic', -100)
            self.assertIsNotNone(first)
            self.assertIsNone(self.store.claim_send('automatic', -100))
        for i in range(1, 5):
            with patch('bot.time.time', return_value=base + i * 3601):
                self.assertIsNotNone(self.store.claim_send('automatic', -100))
        restarted = Store(self.database)
        with patch('bot.time.time', return_value=base + 6 * 3601):
            self.assertIsNone(restarted.claim_send('automatic', -100))

    def test_auto_off_during_generation_cancels_send(self):
        self.bind()
        self.store.set('automatic', True)
        job = {'chat_id': -100, 'text': 'интернет', 'reply_to': 2,
               'automatic': True, 'queued_at': time.time()}
        def generation(_):
            self.store.set('automatic', False)
            return 'а шо там опять сломалось'
        with patch.object(self.bot, 'auto_eligible', side_effect=lambda *a: self.store.get('automatic')):
            with patch.object(self.bot, 'generate', side_effect=generation):
                before = len(self.telegram.sent)
                self.bot.process_job(job)
                self.assertEqual(len(self.telegram.sent), before)

    def test_uncertain_send_is_not_retried(self):
        self.bind()
        self.telegram.fail = True
        job = {'chat_id': -100, 'text': 'интернет', 'reply_to': 2,
               'automatic': False, 'queued_at': time.time()}
        with patch.object(self.bot, 'generate', return_value='шо там опять случилось'):
            before = len(self.telegram.sent)
            self.bot.process_job(job)
            self.assertEqual(len(self.telegram.sent), before + 1)
        self.assertEqual(self.store.counts().get('manual'), 1)

    def test_output_guard_rejects_analysis_and_long_answers(self):
        self.assertIsNone(valid_answer('Хорошо, пользователь пишет про интернет'))
        self.assertIsNone(valid_answer('x' * 221))
        self.assertIsNone(valid_answer('<think>reason</think>ответ'))
        self.assertIsNone(valid_answer('токен 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'))
        self.assertEqual(valid_answer('а шо там опять случилось))'), 'а шо там опять случилось))')
        self.assertEqual(valid_answer('я бот-пародия, ти шо))'), 'я бот-пародия, ти шо))')

    def test_history_is_bounded(self):
        for i in range(230):
            self.store.add_message(self.update('сообщение', number=i)['message'])
        with self.store.db() as db:
            count = db.execute('SELECT count(*) FROM messages').fetchone()[0]
        self.assertEqual(count, 200)

    def test_telegram_token_not_in_network_error(self):
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('secret-token-url')):
            with self.assertRaises(APIError) as caught:
                http_json('https://api.telegram.org/botSECRET/getMe', {})
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertNotIn('secret-token-url', str(caught.exception))


class RetrievalTests(unittest.TestCase):
    def test_no_constant_examples_without_search_terms(self):
        self.assertEqual(retrieval.find_examples('а шо ти ето'), [])

    @unittest.skipUnless(retrieval.SOURCE.is_file(), 'Private corpus is kept on the VM')
    def test_real_corpus_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(retrieval, 'STATE', Path(folder)), patch.object(retrieval, 'INDEX', Path(folder) / 'index.sqlite3'):
                count = retrieval.build_index()
                self.assertGreater(count, 20000)
                examples = retrieval.find_examples('интернет роутер ремонт')
                self.assertGreater(len(examples), 2)
                self.assertTrue(any(row['context'] for row in examples))
                self.assertTrue(all(row['response'] for row in examples))
                candidates = retrieval.random_candidates(10)
                self.assertEqual(len(candidates), 10)
                self.assertTrue(all(candidates))


class HTTPTests(unittest.TestCase):
    def test_telegram_html_is_enabled_only_for_trusted_template(self):
        telegram = Telegram('test-token')
        for message in (HTMLMessage('<b>100 USD</b>'), '<b>ordinary model text</b>'):
            with self.subTest(message=message), patch.object(telegram, 'call', return_value={}) as call, patch('common.time.sleep'):
                telegram.send(-100, message, reply_to=5)
            payload = call.call_args.kwargs
            self.assertEqual(payload['text'], message)
            self.assertEqual(payload['reply_parameters']['message_id'], 5)
            if isinstance(message, HTMLMessage):
                self.assertEqual(payload['parse_mode'], 'HTML')
            else:
                self.assertNotIn('parse_mode', payload)

    def test_model_inventory_uses_get(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'models': [{'name': MODEL}]}).encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = http_json(f'http://127.0.0.1:{server.server_port}/api/tags', None)
            self.assertEqual(result['models'][0]['name'], MODEL)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
