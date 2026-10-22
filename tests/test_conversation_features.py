import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import Bot, Store, answer_key
from common import HTMLMessage, MODEL, ROOT, visible_text
from community import SummaryError, help_text, message_link, summary_input, summary_report, summary_schema
from conversation import banter_request, verification_query
from check_dialogues import NoTelegram, assess, evaluate, route
from fact_check import FactError
from test_bot import FakeTelegram


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100123)
        self.telegram = FakeTelegram()
        self.bot = Bot(dict(owner_id=42, model=MODEL, ollama_url='http://localhost:11434'),
                       telegram=self.telegram, store=self.store)
        self.bot.bot_id, self.bot.username = 99, 'parody_bot'

    def tearDown(self):
        self.temp.cleanup()

    def update(self, text, number=1, user=7, reply=None, chat=-100123):
        message = dict(message_id=number, chat=dict(id=chat, type='supergroup'), date=time.time(),
                       text=text, **{'from': dict(id=user, first_name='Участник', is_bot=False)})
        if reply:
            message['reply_to_message'] = dict(reply, **{'from': dict(id=99, is_bot=True)})
        return {'message': message}

    def bot_message(self, text='проверь скорость по кабелю.', number=100, question='Почему WiFi тормозит?', mode='question'):
        message = dict(chat={'id': -100123}, message_id=number, date=time.time(), text=text, _answer_mode=mode)
        self.store.add_message(message, human=False, request_text=question, learnable=mode == 'parody', rateable=True)
        return dict(message_id=number, text=text)

    def model(self, text):
        return {'message': {'content': text}}

    def test_banter_question_still_activates_but_factual_question_is_explanation(self):
        self.bot.handle(self.update('где мой цаца свиноголовин?'))
        job = self.bot.jobs.get_nowait()
        self.assertFalse(self.bot.is_thoughtful(job))
        with patch('bot.http_json', return_value=self.model('ти его опять сам потерял')) as call:
            self.bot.generate(job)
        self.assertIn('3–12', call.call_args.args[1]['messages'][0]['content'])
        self.assertFalse(banter_request('Цаца почему роутер теряет соединение?'))
        self.assertFalse(banter_request('Сколько стоит свинья?'))

    def test_queued_amendment_bypasses_cooldown_without_adding_job(self):
        self.bot.handle(self.update('Бубус почему сервер тормозит?'))
        self.bot.handle(self.update('нет я про резервные копии а не скорость', 2))
        self.assertEqual(self.bot.jobs.qsize(), 1)
        job = self.bot.jobs.get_nowait()
        self.assertEqual(job['reply_to'], 2)
        self.assertEqual(job['request_updates'], ['Бубус почему сервер тормозит?'])
        self.assertIn('резервные', job['text'])
        self.bot.handle(self.update('нет я про другой сервер', 2))
        self.assertEqual(job['_revision'], 1)

    def test_active_generation_is_revised_and_old_answer_is_never_sent(self):
        self.bot.handle(self.update('Почему сервер медленный?'))
        root = self.bot.jobs.get_nowait()
        entered, resume = threading.Event(), threading.Event()
        seen = []
        def generate(job):
            seen.append(job['text'])
            if len(seen) == 1:
                entered.set()
                self.assertTrue(resume.wait(5))
                return 'проверь старую скорость'
            return 'проверь восстановление резервной копии'
        with patch.object(self.bot, 'generate', side_effect=generate), \
                patch.object(self.bot, 'reserve_utility_send', return_value=1):
            worker = threading.Thread(target=self.bot.process_job, args=(root,))
            worker.start()
            self.assertTrue(entered.wait(5))
            self.bot.handle(self.update('нет я про резервные копии', 2))
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(seen), 2)
        self.assertEqual([row[1] for row in self.telegram.sent], ['проверь восстановление резервной копии'])
        self.assertEqual(self.telegram.sent[0][2], 2)
        self.assertFalse(self.bot.has_pending(-100123, 7))
        chain = Store(self.store.path).dialogue(-100123, 10001)
        self.assertEqual([row['text'] for row in chain if row['human']],
                         ['Почему сервер медленный?', 'нет я про резервные копии'])

    def test_unquoted_followups_remain_linked_after_another_answer_and_restart(self):
        for number, (question, answer) in enumerate([
                ('Почему WiFi медленный?', 'помехи снижают скорость.'),
                ('а если по кабелю тоже медленно?', 'проверь линию провайдера.')], 1):
            self.store.set('request:-100123', 0)
            self.bot.handle(self.update(question, number))
            with patch('bot.http_json', return_value=self.model(answer)), \
                    patch.object(self.bot, 'reserve_utility_send', return_value=number):
                self.bot.process_job(self.bot.jobs.get_nowait())
        self.bot.store = Store(self.store.path)
        self.store.set('request:-100123', 0)
        self.bot.handle(self.update('объясни проще', 3))
        job = self.bot.jobs.get_nowait()
        chain = self.bot.store.dialogue(-100123, job['dialogue_id'])
        self.assertEqual([row['text'] for row in chain if row['human']],
                         ['Почему WiFi медленный?', 'а если по кабелю тоже медленно?'])

    def test_other_person_or_unrelated_reply_cannot_revise_request(self):
        self.bot.handle(self.update('Почему сервер медленный?'))
        root = self.bot.jobs.queue[0]
        self.bot.handle(self.update('нет я про резервные копии', 2, user=8))
        self.assertEqual(root['_revision'], 0)
        self.bot.handle(self.update('нет я про резервные копии', 3, reply=dict(message_id=555, text='чужая тема')))
        self.assertEqual(root['_revision'], 0)
        self.assertEqual(self.bot.jobs.qsize(), 1)

    def test_controls_use_own_session_and_apply_only_to_one_response(self):
        response = self.bot_message()
        self.store.remember_session(-100123, 7, response['message_id'])
        responses = ['сравни скорость по кабелю.', 'помехи снижают скорость WiFi.',
                     'посмотри уровень сигнала возле роутера.', 'отключи загрузку файлов и повтори измерение.']
        for number, (text, control) in enumerate([
                ('короче', 'concise'), ('объясни нормально', 'plain'),
                ('без приколов', 'serious'), ('приведи пример', 'example')], 1):
            self.store.set('request:-100123', 0)
            expected_parent = self.store.session(-100123, 7)
            self.bot.handle(self.update(text, number))
            job = self.bot.jobs.get_nowait()
            self.assertEqual(job['response_control'], control)
            self.assertEqual(job['dialogue_id'], expected_parent)
            with patch('bot.http_json', return_value=self.model(responses[number - 1])) as call, \
                    patch.object(self.bot, 'reserve_utility_send', return_value=number):
                self.bot.process_job(job)
            payload = call.call_args.args[1]
            data = json.loads(payload['messages'][1]['content'])
            self.assertEqual(data['response_control'], control)
            if control == 'concise':
                self.assertEqual(payload['options']['num_predict'], 160)
        self.store.set('request:-100123', 0)
        self.bot.handle(self.update('Почему небо синее?', 10))
        self.assertIsNone(self.bot.jobs.get_nowait()['response_control'])

    def test_control_without_context_asks_for_target_and_does_not_guess(self):
        self.bot.handle(self.update('Бубус короче'))
        job = self.bot.utility_jobs.get_nowait()
        self.assertEqual(job['utility']['kind'], 'fixed')
        self.assertIn('нужную реплику', job['utility']['text'])

    def test_control_on_a_new_subject_does_not_pull_previous_session_into_it(self):
        response = self.bot_message()
        self.store.remember_session(-100123, 7, response['message_id'])
        self.bot.handle(self.update('Без приколов почему небо синее?'))
        job = self.bot.jobs.get_nowait()
        self.assertEqual(job['response_control'], 'serious')
        self.assertIsNone(job['dialogue_id'])
        with patch('bot.http_json', return_value=self.model('синий свет сильнее рассеивается в атмосфере.')) as call:
            self.bot.generate(job)
        data = json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['dialogue'], [])

    def test_auto_lookup_uses_sources_and_failure_does_not_ask_offline_model(self):
        self.bot.handle(self.update('Кто сейчас президент Франции?'))
        job = self.bot.jobs.get_nowait()
        with patch.object(self.bot.fact_checker, 'answer', return_value=HTMLMessage('<b>По источникам</b>')) as web, \
                patch.object(self.bot, 'generate') as offline:
            self.bot.process_job(job)
        web.assert_called_once()
        self.assertTrue(web.call_args.kwargs['question'])
        offline.assert_not_called()
        self.assertEqual(self.store.answer_mode(-100123, 10001), 'factcheck')
        self.store.set('request:-100123', 0)
        self.bot.handle(self.update('Кто сейчас премьер-министр Великобритании?', 2))
        with patch.object(self.bot.fact_checker, 'answer', side_effect=FactError('no_readable_sources')), \
                patch.object(self.bot, 'generate') as offline, patch.object(self.bot, 'reserve_utility_send', return_value=2):
            self.bot.process_job(self.bot.jobs.get_nowait())
        offline.assert_not_called()
        self.assertIn('подтвердить не могу', self.telegram.sent[-1][1])

    def test_auto_lookup_never_exports_context_or_private_references(self):
        for query in ['А сколько они стоят сейчас?', 'Почему мой сервер сейчас медленный?',
                      'Кто сейчас @Sidik08?', 'Правда ли пароль test123?', 'Что сейчас в нашем чате?',
                      'Почему сейчас не отвечает 192.168.1.5?']:
            self.assertIsNone(verification_query(query), query)
        self.store.participant_action(dict(kind='participant_set', value='@Sidik08 | Сижик | живёт в Казахстане'),
                                      -100123, 42, 50, owner=True)
        self.bot.handle(self.update('Где сейчас Сижик?'))
        with patch.object(self.bot.fact_checker, 'answer') as web, \
                patch.object(self.bot, 'generate', return_value='по записи владельца он живёт в Казахстане.'):
            self.bot.process_job(self.bot.jobs.get_nowait())
        web.assert_not_called()

    def test_correction_confirmation_is_attributed_scoped_persistent_and_keeps_bank(self):
        response = self.bot_message()
        self.bot.handle(self.update('неправда', 1, reply=response))
        bank = self.store.salary(-100123)
        self.bot.handle(self.update('/correct помехи WiFi могут снижать скорость проверь кабель', 2, reply=response))
        receipt = dict(message_id=10000 + len(self.telegram.sent), text=self.telegram.sent[-1][1])
        self.assertFalse(self.store.correction_examples(-100123, 'Почему WiFi тормозит?', 'question'))
        self.bot.handle(self.update('подтверждаю', 3, reply=receipt, user=8))
        self.assertIn('автор исправления', self.telegram.sent[-1][1])
        self.bot.handle(self.update('подтверждаю', 4, reply=receipt))
        self.assertEqual(self.store.salary(-100123), bank)
        reopened = Store(self.store.path)
        examples = reopened.correction_examples(-100123, 'Почему WiFi тормозит?', 'question')
        self.assertEqual(examples[0]['author'], 'Участник')
        self.assertTrue(examples[0]['participant_claim'])
        self.assertFalse(reopened.correction_examples(-200, 'Почему WiFi тормозит?', 'question'))
        self.assertFalse(reopened.correction_examples(-100123, 'Почему небо синее?', 'question'))
        self.assertFalse(reopened.correction_examples(-100123, 'Почему WiFi тормозит?', 'parody'))
        self.assertIn(answer_key(response['text']), reopened.rejected_keys(-100123))

    def test_corrections_reject_forged_quotes_service_answers_and_other_chat(self):
        response = self.bot_message(mode='weather')
        for quoted, chat in [(response['text'], -100123), ('подмена', -100123), (response['text'], -200)]:
            identifier, _ = self.store.propose_correction(chat, 1, 100, quoted, 7, 'Автор', 'исправленный ответ')
            self.assertIsNone(identifier)
        self.assertEqual(self.store.salary(-100123)['balance'], 0)

    def test_confirmed_correction_is_used_only_for_matching_question(self):
        response = self.bot_message()
        identifier, text = self.store.propose_correction(-100123, 1, 100, response['text'], 7, 'Автор',
                                                       'помехи WiFi проверь сравнением по кабелю')
        receipt = dict(message_id=500, text=text)
        self.store.link_correction(-100123, receipt, identifier)
        self.store.confirm_correction(-100123, 500, text, 7)
        self.bot.handle(self.update('Почему WiFi тормозит?', 2))
        with patch('bot.http_json', return_value=self.model('помехи снижают скорость попробуй другой диапазон.')) as call:
            self.bot.generate(self.bot.jobs.get_nowait())
        data = json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['approved_corrections'][0]['author'], 'Автор')
        self.assertNotIn(response['text'], json.dumps(data['approved_corrections'], ensure_ascii=False))
        self.bot.handle(self.update('подтверждаю', 3, reply=receipt))
        before = len(self.telegram.sent)
        self.bot.handle(self.update('подтверждаю', 3, reply=receipt))
        self.assertEqual(len(self.telegram.sent), before)

    def test_revision_while_waiting_to_send_discards_reserved_old_answer(self):
        self.bot.handle(self.update('Почему WiFi медленный?'))
        root = self.bot.jobs.get_nowait()
        reservations = []
        def reserve(job):
            reservations.append(job['text'])
            if len(reservations) == 1:
                self.bot.handle(self.update('нет я про резервные копии', 2))
            return len(reservations)
        with patch.object(self.bot, 'generate', side_effect=['старый ответ про скорость', 'новый ответ про копии']), \
                patch.object(self.bot, 'reserve_utility_send', side_effect=reserve):
            self.bot.process_job(root)
        self.assertEqual(len(reservations), 2)
        self.assertEqual([row[1] for row in self.telegram.sent], ['новый ответ про копии'])

    def test_revision_skips_retry_of_invalid_old_generation(self):
        self.bot.handle(self.update('Почему WiFi медленный?'))
        root = self.bot.jobs.get_nowait()
        def model(*args, **kwargs):
            data = json.loads(args[1]['messages'][1]['content'])
            if data['current_request'] == 'Почему WiFi медленный?':
                self.bot.handle(self.update('нет я про резервные копии', 2))
                return self.model('<think>obsolete</think>')
            return self.model('проверь восстановление резервной копии.')
        with patch('bot.http_json', side_effect=model) as call, \
                patch.object(self.bot, 'reserve_utility_send', return_value=1):
            self.bot.process_job(root)
        self.assertEqual(call.call_count, 2)
        self.assertEqual([row[1] for row in self.telegram.sent], ['проверь восстановление резервной копии.'])

    def test_control_after_web_answer_rechecks_original_public_question(self):
        response = self.bot_message(question='Кто сейчас президент Франции?', mode='factcheck')
        self.store.remember_session(-100123, 7, response['message_id'])
        self.bot.handle(self.update('короче', 1))
        with patch.object(self.bot.fact_checker, 'answer', return_value=HTMLMessage('Проверенный краткий ответ')) as web, \
                patch.object(self.bot, 'generate') as offline:
            self.bot.process_job(self.bot.jobs.get_nowait())
        self.assertEqual(web.call_args.args[0], 'Кто сейчас президент Франции?')
        self.assertEqual(web.call_args.kwargs['control'], 'concise')
        offline.assert_not_called()

    def test_issue_patterns_group_causes_without_injecting_old_subjects(self):
        for number in [100, 101]:
            message = self.bot_message('плохой старый ответ ' + str(number), number=number)
            self.store.add_issue(-100123, number, 7, message['text'], 'repeat', answer_key)
        self.assertEqual(self.store.issue_patterns(-100123), [dict(reason='repeat', count=2)])

    def test_participant_aliases_owner_permissions_conflicts_and_prompt(self):
        request = dict(kind='participant_set', value='@Sidik08 | Сижег, Сижик, Сидек, Сидик, @Sazhency04 | живёт в Казахстане')
        self.assertIn('только хозяин', self.store.participant_action(request, -100123, 7, 1))
        self.store.participant_action(request, -100123, 42, 2, owner=True)
        for alias in ['Сижег', 'Сижик', 'Сидек', 'Сидик', '@Sazhency04', '@Sidik08']:
            self.assertEqual(self.store.participant_profiles(-100123, 'Кто такой ' + alias + '?')[0]['username'], 'sidik08')
        conflict = dict(kind='participant_set', value='@someone | Сижик | другая запись')
        self.assertIn('другим участником', self.store.participant_action(conflict, -100123, 42, 3, owner=True))
        self.assertFalse(self.store.participant_profiles(-200, 'Сижик'))
        self.assertFalse(self.store.participant_profiles(-100123, 'Сижикович'))
        self.bot.handle(self.update('Кто такой Сижик?', 4))
        with patch('bot.http_json', return_value=self.model('по записи владельца ето участник из Казахстана.')) as call:
            self.bot.generate(self.bot.jobs.get_nowait())
        data = json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['participant_profiles'][0]['confirmed_by'], 42)
        self.assertEqual(len(data['participant_profiles']), 1)
        self.assertEqual(len(Store(self.store.path).participant_profiles(-100123)), 1)

    def test_person_commands_are_available_and_plain_chat_cannot_write_profiles(self):
        self.bot.handle(self.update('/person_set @Sidik08 | Сижик | живёт в Казахстане', user=42))
        self.bot.process_job(self.bot.utility_jobs.get_nowait())
        self.assertTrue(self.store.participant_profiles(-100123, 'Сижик'))
        self.store.set('request:-100123', 0)
        self.bot.handle(self.update('/person Сижик', 2))
        with patch.object(self.bot, 'reserve_utility_send', return_value=2):
            self.bot.process_job(self.bot.utility_jobs.get_nowait())
        self.assertIn('@sidik08', self.telegram.sent[-1][1])
        self.store.participant_action(dict(kind='participant_forget', value='@Sidik08'), -100123, 42, 3, owner=True)
        self.assertFalse(self.store.participant_profiles(-100123))

    def test_participant_listing_fits_utf16_limit_and_never_guesses_from_notes(self):
        for number in range(10):
            value = f'@person{number} | ' + ', '.join('alias' + str(number) + 'x' * 25 + str(n) for n in range(10)) + ' | ' + '😀' * 150
            self.store.participant_action(dict(kind='participant_set', value=value), -100123, 42, number + 1, owner=True)
        listing = self.store.participant_action(dict(kind='participant', value=''), -100123, 7, 30)
        self.assertLessEqual(len(visible_text(listing).encode('utf-16-le')) // 2, 3500)
        self.assertFalse(self.store.participant_profiles(-100123, 'Кто живёт в Казахстане?'))


class SummaryAndReplayTests(unittest.TestCase):
    def test_summary_has_decisions_open_questions_and_links_to_real_ids(self):
        rows = [dict(speaker='Иван', text='договорились встретиться завтра а время ещё обсуждаем', timestamp=time.time(), message_id=77)]
        selected = summary_input(rows, chat_id=-1001234567890)
        payload = dict(topics=[dict(title='Встреча', text='Обсудили встречу.', quip='день выбрали а часы потеряли', sources=[1])],
                       decisions=[dict(text='Встретиться завтра.', sources=[1])],
                       open_questions=[dict(text='Время встречи ещё не выбрали.', sources=[1])])
        result = dict(message=dict(content=json.dumps(payload, ensure_ascii=False)))
        report = summary_report(result, selected, 1, 1)
        self.assertIn('К чему пришли', report)
        self.assertIn('Что осталось открытым', report)
        self.assertIn('https://t.me/c/1234567890/77', report)
        self.assertEqual(report.count('https://t.me/c/1234567890/77'), 3)
        self.assertIn('open_questions', summary_schema(1)['properties'])
        payload['open_questions'][0]['sources'] = [2]
        with self.assertRaises(SummaryError):
            summary_report(dict(message=dict(content=json.dumps(payload))), selected, 1, 1)

    def test_private_or_legacy_group_has_no_guessed_link_and_help_fits_telegram(self):
        for chat in [42, -123, -100, None]:
            self.assertEqual(message_link(chat, 7), '')
        self.assertLess(len(visible_text(help_text(True)).encode('utf-16-le')) // 2, 4000)

    def test_regression_cases_routes_and_semantic_checks(self):
        cases = json.loads((ROOT / 'dialogue_cases.json').read_text())
        for case in cases:
            self.assertEqual(route(case), case['mode'], case['id'])
        case = cases[0]
        self.assertIn('forbidden_topic_or_phrase', assess(case, 'карточки ищи на ebay', 1, 120))
        self.assertIn('slow_answer', assess(case, 'ти его опять потерял', 121, 120))
        self.assertFalse(assess(case, 'ти его опять потерял', 1, 120))

    def test_replay_runs_only_generation_and_has_no_telegram_or_web_access(self):
        case = dict(id='test', request='Почему WiFi медленный?', mode='question', must_any=['помех'])
        config = dict(owner_id=42, model=MODEL, ollama_url='http://localhost:11434')
        with patch('bot.http_json', return_value={'message': {'content': 'помехи снижают скорость.'}}), \
                patch('fact_check.fetch_public') as web, patch.object(Bot, '_process_job') as send:
            result = evaluate(case, config, run_model=True)
        self.assertTrue(result['model_checked'])
        self.assertFalse(result['errors'])
        web.assert_not_called()
        send.assert_not_called()
        with self.assertRaises(RuntimeError):
            NoTelegram().send(1, 'no')

    def test_web_replay_checks_route_without_inventing_an_offline_answer(self):
        case = dict(id='web', request='Кто сейчас президент Франции?', mode='web')
        with patch.object(Bot, 'generate') as model:
            result = evaluate(case, {}, run_model=True)
        model.assert_not_called()
        self.assertFalse(result['model_checked'])
        self.assertIn('network lookup disabled', result['note'])
