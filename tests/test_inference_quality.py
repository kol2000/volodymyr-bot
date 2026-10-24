import json
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from answer_quality import banter_coherence_problem, banter_relation_problem, concise_extract, concise_problem, concise_word_limit, explanation_problem, grounding_problem, specific_clarification
from bot import Bot, Store
from check_dialogues import assess, evaluate, main, timing_text, warm_model
from common import APIError, MODEL, ROOT
from community import summary_text
from fact_check import FactChecker
from inference import MODEL_CONTEXT, generation_stats, record_generation, runner_options
from test_bot import FakeTelegram


class InferenceQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100)
        self.config = dict(owner_id=42, model=MODEL, ollama_url='http://localhost:11434')
        self.bot = Bot(self.config, telegram=FakeTelegram(), store=self.store)

    def job(self, text, **extra):
        return dict(dict(chat_id=-100, text=text, reply_to=10, automatic=False,
                         queued_at=time.time(), thoughtful=True), **extra)

    def result(self, text):
        return {'message': {'content': text}}

    def test_real_bad_answers_from_first_vm_run_fail_stronger_checks(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        bad = 'Жёлтые — ето когда не зелёные но и не оранжевые. Шо за вопрос?'
        self.assertIn('missing_specific_clarification', assess(cases['ambiguous-subject'], bad, 6, 120))
        self.assertIn('unsupported_certainty', assess(cases['private-current'],
                      'Проверь логи на сервере — там точно написано шо сломалось.', 74, 120))
        self.assertIn('unsupported_certainty', assess(cases['real-question'],
                      'WiFi медленнее из-за помех. Кабель передаёт данные напрямую без потерь.', 31, 120))
        self.assertIn('unsupported_certainty', assess(cases['pending-correction'],
                      'Если не получается — копия сломана.', 12, 120))
        self.assertIn('not_shorter_than_source', assess(cases['shorter'],
                      'Короче: WiFi тормозит из-за помех перегрузки сети или проблем с роутером. '
                      'Проверь сигнал перезагрузи устройство и роутер — и всё будет норм.', 83, 120))

    def test_second_vm_run_rephrased_guarantees_are_rejected(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        self.assertIn('unsupported_certainty', assess(cases['serious'],
                      'Сервер теряет соединение из-за проблем с сетью. '
                      'Проверь кабель роутер нагрузку на сервер и логи — там будет точная причина.', 10, 120))
        self.assertIn('unsupported_certainty', assess(cases['pending-correction'],
                      'Сделай пробное восстановление в отдельную папку. '
                      'Если выдаёт ошибку — значит битая. Просто и надёжно.', 11, 120))
        for text in ['В журналах найдёш точную причину сбоя.',
                     'Логи покажут причину точно.',
                     'При восстановлении возникла ошибка. Следовательно резервная копия повреждена.',
                     'Восстановить не получилось — значит она испорчена.']:
            self.assertEqual(grounding_problem(text, 'Как проверить резервную копию сервера?'),
                             'unsupported_certainty', text)
        for text in ['В журналах может быть точная причина сбоя.',
                     'Попробуй найти точную причину в логах.',
                     'Ошибка восстановления не означает что копия битая.',
                     'Ошибка восстановления может означать что копия повреждена.',
                     'Если выдаёт ошибку проверь доступ совместимость и настройки.',
                     'Если проверка пройдена значит она не повреждена.']:
            self.assertIsNone(grounding_problem(text, 'Как проверить резервную копию сервера?'), text)

    def test_ambiguous_boundary_alias_does_not_become_a_user_name(self):
        from conversation import dialogue_roles
        request = 'цацик опять диванный чемпион?'
        roles = dialogue_roles(request)
        self.assertEqual(roles['address_kind'], 'ambiguous')
        self.assertEqual(roles['message_without_address'], 'опять диванный чемпион')
        bad = 'Цацик ти вообще где? Опять в боте сидиш?'
        self.assertEqual(banter_relation_problem(bad, request), 'address_role_reversal')
        self.assertIn('address_role_reversal', assess(dict(mode='parody', request=request), bad, 1, 120))
        self.assertIsNone(banter_relation_problem('дивану без меня никак', request))
        for phrase in ['Мой друг Цацик опять всё перепутал.', 'Цацик сказал что ты ошибся.',
                       'Он написал: «цацик опять диванный чемпион?»']:
            self.assertIn(dialogue_roles(phrase)['address_kind'], ['mention', 'quoted_mention'])
            self.assertEqual(banter_relation_problem('Цацик ти вообще где?', phrase), 'reported_addressee_reversal')

    def test_arrival_frame_preserves_who_arrived_and_who_waited(self):
        from conversation import dialogue_roles
        request = 'явился наконец цацик'
        roles = dialogue_roles(request)
        self.assertEqual(roles['event_roles'], dict(event='arrival', arrived='bot', waited='speaker'))
        self.assertEqual(roles['perspective']['outgoing_i'], 'bot')
        for answer in ['Ну наконец-то дождался.', 'Я уже ждал тебя.', 'Ну наконец ты явился.']:
            self.assertEqual(banter_relation_problem(answer, request), 'event_role_reversal', answer)
        for answer in ['ну дождался меня?', 'Ты дождался.', 'Дождался?', 'Я уже тут.',
                       'Я тебя не ждал.', 'Ну дождался меня.']:
            self.assertIsNone(banter_relation_problem(answer, request), answer)
        for phrase in ['Мой друг Цацик пришёл.', 'цацик ты сказал что он пришёл', 'цацик ты не пришёл']:
            self.assertNotIn('event_roles', dialogue_roles(phrase))
        self.assertIsNone(banter_relation_problem('Я дождался.', 'Мой друг Цацик пришёл.'))

    def test_reversed_arrival_is_retried_with_event_frame_and_checked_again(self):
        good = 'ну дождался меня?'
        with patch('bot.find_examples', return_value=[]), \
                patch('bot.http_json', side_effect=[self.result('Ну наконец-то дождался.'), self.result(good)]) as model:
            job = self.job('явился наконец цацик', thoughtful=False, _capture_rejections=True)
            self.assertEqual(self.bot.generate(job), good)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_rejection_details'][0]['rule'], 'banter.event_role_reversal')
        retry = model.call_args.args[1]['messages']
        self.assertIn('пришёл бот, а ждал собеседник', retry[0]['content'])
        data = json.loads(retry[1]['content'])
        self.assertEqual(data['conversation_roles']['event_roles']['waited'], 'speaker')

    def test_address_roles_separate_vocatives_mentions_quotes_and_triggers(self):
        from conversation import dialogue_roles
        for request in ['Цацик, ты уснул?', 'шо ты опять умничаешь цацик?', 'цацик не умничай', 'явился наконец цацик']:
            role = dialogue_roles(request)
            self.assertEqual(role['address_kind'], 'bot_address', request)
            self.assertNotIn('цацик', role['message_without_address'].casefold())
        for request in ['Мой друг Цацик опять всё перепутал.', 'Кто такой Цацик?',
                        'Он сказал: «цацик ты уснул?»', 'крым наш', 'война']:
            self.assertNotIn(dialogue_roles(request)['address_kind'], ['bot_address', 'bare_call'], request)
        self.assertEqual(dialogue_roles('ЦАЦИК!')['address_kind'], 'bare_call')

    def test_bare_bot_call_is_local_without_archive_model_or_ambient_topic(self):
        self.store.add_message(dict(chat={'id': -100}, message_id=1, date=time.time(), text='Обсуждали сборку шкафа.'))
        replies = []
        for index in range(3):
            with patch('bot.http_json') as model, patch('bot.find_examples') as archive:
                job = self.job('цацик', thoughtful=False)
                answer = self.bot.generate(job)
            model.assert_not_called()
            archive.assert_not_called()
            self.assertEqual(job['_local_reply'], 'bot_call')
            self.assertNotIn('шкаф', answer)
            replies.append(answer)
            self.store.add_message(dict(chat={'id': -100}, message_id=20 + index, date=time.time(),
                                        text=answer, _answer_mode='parody'), human=False)
        self.assertEqual(len(set(replies)), 3)

    def test_echo_and_reversed_address_are_retried_but_third_person_mentions_are_allowed(self):
        bad_cases = [('цацик опять ты всё перепутал', 'Цацик опять ти всё перепутал?', 'request_echo'),
                     ('цацик ты уснул?', 'Цацик а ти вообще живой?', 'address_role_reversal')]
        good = 'сам то понял что написал?'
        for request, bad, reason in bad_cases:
            self.assertEqual(banter_relation_problem(bad, request), reason)
            self.assertIn(reason, assess(dict(mode='parody', request=request), bad, 1, 120))
            with patch('bot.find_examples', return_value=[]), \
                    patch('bot.http_json', side_effect=[self.result(bad), self.result(good)]) as model:
                job = self.job(request, thoughtful=False)
                self.assertEqual(self.bot.generate(job), good)
            self.assertEqual(model.call_count, 2)
            payload = model.call_args.args[1]['messages']
            data = json.loads(payload[1]['content'])
            self.assertEqual(data['conversation_roles']['address_kind'], 'bot_address')
            self.assertEqual(data['current_request'], request)
            self.assertIn('Не переноси на собеседника', payload[0]['content'])
        self.assertEqual(banter_relation_problem('Цацик а ти что натворил?', 'Мой друг Цацик опять всё перепутал.'), 'reported_addressee_reversal')
        self.assertIsNone(banter_relation_problem('А ты?', 'цацик ты уснул?'))

    def test_bare_call_cli_does_not_warm_model_or_count_as_clarification(self):
        report = Path(self.temp.name) / 'call.json'
        output = io.StringIO()
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json') as warmup, patch('bot.http_json') as model, \
                contextlib.redirect_stdout(output):
            result = main(['--run-model', '--cases', str(ROOT / 'dialogue_cases_banter.json'),
                           '--case', 'banter-bare-call', '--report', str(report)])
        self.assertEqual(result, 0)
        warmup.assert_not_called()
        model.assert_not_called()
        self.assertIn('локальный отклик', output.getvalue())
        self.assertIn('локальных откликов: 1', output.getvalue())
        self.assertEqual(json.loads(report.read_text())[0]['local_reply'], 'bot_call')

    def test_known_particle_piles_are_rejected_but_complete_short_reactions_are_allowed(self):
        for answer in ['ти шо ещо било би сечас', 'ти що тут шо бля сученок?', 'ти шо тут ето бля опять']:
            self.assertEqual(banter_coherence_problem(answer), 'unconnected_particles')
            self.assertIn('unconnected_particles', assess(dict(mode='parody', request='цацик'), answer, 1, 120))
        for answer in ['ну ти опять эксперт по всему', 'сначала сам разберись потом учи',
                       'ну и где ти её потерял свиноголовый?', 'чего надо?', 'сам то понял что написал?']:
            self.assertIsNone(banter_coherence_problem(answer), answer)

    def test_particle_pile_retries_without_profile_or_examples_and_is_reported(self):
        bad = 'ти шо ещо било би сечас'
        good = 'сначала сам разберись потом учи'
        job = self.job('шо ты опять умничаешь цацик?', thoughtful=False, _capture_rejections=True)
        with patch('bot.find_examples', return_value=[]), patch('bot.profile_for_request') as profile, \
                patch('bot.http_json', side_effect=[self.result(bad), self.result(good)]) as model:
            self.assertEqual(self.bot.generate(job), good)
        profile.assert_not_called()
        self.assertEqual(model.call_count, 2)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertNotIn('speech_profile', data)
        self.assertEqual(data['style_examples'], [])
        self.assertIn('одно законченное предложение', model.call_args.args[1]['messages'][0]['content'])
        self.assertEqual(job['_rejection_details'][0]['rule'], 'banter.unconnected_particles')
        self.assertEqual(job['_rejection_details'][0]['answer'], bad)
        self.assertEqual(model.call_args.args[1]['options']['num_ctx'], MODEL_CONTEXT)

    def test_two_particle_piles_are_not_sent_as_a_successful_banter_answer(self):
        with patch('bot.find_examples', return_value=[]), \
                patch('bot.http_json', return_value=self.result('ти шо ещо било би сечас')) as model:
            job = self.job('шо ты опять умничаешь цацик?', thoughtful=False)
            self.assertIsNone(self.bot.generate(job))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_rejections'], ['unconnected_particles'] * 2)

    def test_short_parody_instruction_has_bounded_size_without_manual_archive_examples(self):
        examples = [dict(context='роутер тормозит', response=text) for text in [
            'ти провод сначала проверь', 'ну шо опять случилось', 'интернет опять отдыхает',
            'да выключи включи етот роутер', 'скоко можно провода дёргать', 'ти его ещо уговаривай']]
        with patch('bot.find_examples', return_value=examples), \
                patch('bot.http_json', return_value=self.result('ти опять роутер уговариваеш')) as model:
            self.bot.generate(self.job('интернет опять пропал', thoughtful=False))
        payload = model.call_args.args[1]
        self.assertLessEqual(len(payload['messages'][0]['content']), 2800)
        self.assertIn('3–12', payload['messages'][0]['content'])
        data = json.loads(payload['messages'][1]['content'])
        self.assertEqual(data['style_examples'], [])
        self.assertIn('reply_brief', data)

    def test_manual_banter_never_retrieves_archive_or_approved_replies(self):
        with patch('bot.find_examples') as archive, patch.object(self.store, 'approved_examples') as approved, \
                patch('bot.http_json', return_value=self.result('не сплю говори')) as model:
            self.bot.generate(self.job('цацик ты уснул?', thoughtful=False))
        archive.assert_not_called()
        approved.assert_not_called()
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertIn('Ответь от себя', data['reply_brief'])
        self.assertEqual(data['style_examples'], [])

    def test_reported_roles_do_not_turn_mentions_or_quotes_into_direct_addresses(self):
        from conversation import dialogue_roles, banter_brief
        for request, scope in [('Мой друг Цацик опять всё перепутал.', 'third_person'),
                               ('Цацик написал что ты опять споришь.', 'third_person'),
                               ('Он сказал: «цацик ты уснул?»', 'reported_speech'),
                               ('Она спросила: «цацик ты здесь?»', 'reported_speech')]:
            roles = dialogue_roles(request)
            self.assertEqual(roles['reply_scope'], scope)
            self.assertIn('с собеседником', banter_brief(roles))
            self.assertEqual(banter_relation_problem('Цацик а ти вообще где?', request), 'reported_addressee_reversal')
            self.assertIsNone(banter_relation_problem('он опять торопится с выводами', request))
        for request in ['Позови Цацика.', 'Кто такой Цацик?', 'цацик ты сказал что он пришёл',
                        'Цацик, скажи «я здесь».', 'Он сказал: «цацик ты уснул?». Ответь ему.']:
            self.assertNotIn('reply_scope', dialogue_roles(request), request)

    def test_reported_address_retry_receives_its_draft_and_keeps_audience(self):
        bad, good = 'Цацик а ти вообще где?', 'пусть уже скажет чего хочет'
        job = self.job('Он сказал: «цацик ты уснул?»', thoughtful=False, _capture_rejections=True)
        with patch('bot.http_json', side_effect=[self.result(bad), self.result(good)]) as model:
            self.assertEqual(self.bot.generate(job), good)
        self.assertEqual(model.call_count, 2)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['repair_candidate'], bad)
        self.assertEqual(data['conversation_roles']['reply_scope'], 'reported_speech')
        self.assertEqual(job['_rejection_details'][0]['rule'], 'banter.reported_addressee_reversal')
        self.assertIn('Обсуди рассказ', model.call_args.args[1]['messages'][0]['content'])

    def test_reported_name_with_parenthetical_is_a_subject_not_a_vocative(self):
        request = 'Мой друг Цацик опять лекцию читает вместо ответа.'
        for answer in ['Цацик, видимо, решил, что лекция — это форма ответа.',
                       'Цацик, наверное, считает, что эрудиция — это ответ.',
                       'Цацик, наш коллега, наконец вернулся.',
                       'Цацик, похоже, тебя не услышал.']:
            self.assertIsNone(banter_relation_problem(answer, request), answer)
        for answer in ['Цацик, ты опять уснул?', 'Цацик а ти где?', 'Цацик, тебе чего надо?']:
            self.assertEqual(banter_relation_problem(answer, request), 'reported_addressee_reversal')
        self.assertEqual(banter_relation_problem('Цацик — ето ти а я — твой гад.', 'Цацик, опять лекцию читаешь?'),
                         'address_role_reversal')
        self.assertIsNone(banter_relation_problem('Цацик — ето я.', 'Цацик, опять лекцию читаешь?'))

    def test_multiline_dialogue_retry_asks_for_only_bot_reply(self):
        bad = 'Цацик, ладно, это я всё перепутал.\nНу ты и перепутал, блин.'
        job = self.job('Цацик, ладно, это я всё перепутал.', thoughtful=False)
        with patch('bot.http_json', side_effect=[self.result(bad), self.result('ну хоть разобрались')]) as model:
            self.assertEqual(self.bot.generate(job), 'ну хоть разобрались')
        self.assertEqual(model.call_count, 2)
        self.assertIn('Не печатай входную реплику', model.call_args.args[1]['messages'][0]['content'])
        self.assertEqual(job['_rejections'], ['multiline_response'])

    def test_automatic_banter_keeps_bounded_style_examples(self):
        examples = [dict(context='беседа', response='да тут я чего надо')]*4
        with patch('bot.find_examples', return_value=examples), \
                patch('bot.http_json', return_value=self.result('тут спор до утра')) as model:
            self.bot.generate(self.job('ну вы опять спорите', thoughtful=False, automatic=True))
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['style_examples'], examples[:2])

    def test_third_vm_run_implicit_backup_verdict_is_rejected_without_false_positives(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        actual = ('Проверяеш резервную копию не по скорости а восстанавливаеш её в отдельное место. '
                  'Смотриш читается ли файл работает ли приложение нет ли ошибок. '
                  'Если всё как надо — копия живая. Если нет — битая. Просто.')
        self.assertIn('unsupported_certainty', assess(cases['pending-correction'], actual, 11, 120))
        for text in [actual, 'Восстанови копию. Иначе повреждена.',
                     'Проверь читается ли файл. В противном случае копия испорчена.',
                     'Если не открывается — она повреждена.',
                     'Если приложение не работает то копия сломана.']:
            self.assertEqual(grounding_problem(text, 'Как проверить резервную копию?'),
                             'unsupported_certainty', text)
        for text in ['Если нет — проверь доступ совместимость и настройки.',
                     'Если не читается — она не обязательно повреждена.',
                     'Если проверка не прошла статус копии не подтверждён.',
                     'Если нет — возможно повреждена но сначала проверь доступ.',
                     'Файл может быть битым. Если нет — проверь настройки.',
                     'Если повреждение подтверждено контрольными суммами копия повреждена.']:
            self.assertIsNone(grounding_problem(text, 'Как проверить резервную копию?'), text)

    def test_implicit_backup_verdict_retries_with_specific_guidance_before_returning(self):
        job = self.job('Как проверить резервную копию?')
        good = 'восстанови отдельно и проверь файлы при ошибке проверь доступ совместимость и настройки.'
        with patch('bot.http_json', side_effect=[self.result('Восстанови копию. Если нет — битая.'),
                                                self.result(good)]) as model:
            answer = self.bot.generate(job)
        self.assertEqual(answer, good)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_rejections'], ['unsupported_certainty'])
        self.assertIn('Сбой восстановления сам по себе не доказывает',
                      model.call_args.args[1]['messages'][0]['content'])

    def test_standalone_shortening_uses_checked_source_before_model_and_skips_warmup(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        with patch('bot.http_json') as model:
            job = self.job('короче', response_control='concise', quoted_message=source)
            answer = self.bot.generate(job)
        model.assert_not_called()
        self.assertEqual(answer, 'Помехи и слабый сигнал могут снижать скорость.')
        self.assertEqual(job['_local_reply'], 'concise_extract')
        report = Path(self.temp.name) / 'local-shorter.json'
        output = io.StringIO()
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json') as warmup, patch('bot.http_json') as model, \
                contextlib.redirect_stdout(output):
            result = main(['--run-model', '--case', 'shorter', '--report', str(report)])
        self.assertEqual(result, 0)
        warmup.assert_not_called()
        model.assert_not_called()
        data = json.loads(report.read_text())[0]
        self.assertFalse(data['model_checked'])
        self.assertEqual(data['calls'], [])
        self.assertIn('локальное сокращение', output.getvalue())
        self.assertIn('локальных сокращений: 1', output.getvalue())

    def test_local_shortening_respects_extra_instructions_updates_and_negative_feedback(self):
        source = 'Помехи могут снижать скорость. Проверь роутер и сравни соединение с кабелем.'
        candidate = 'Помехи могут снижать скорость.'
        for text, updates in [('короче сохрани сравнение', []), ('короче', ['сохрани сравнение'])]:
            with patch('bot.http_json', return_value=self.result(candidate)) as model:
                self.bot.generate(self.job(text, response_control='concise', quoted_message=source, request_updates=updates))
            model.assert_called_once()
        from bot import answer_key
        with patch.object(self.store, 'rejected_keys', return_value={answer_key(candidate)}), \
                patch('bot.http_json', return_value=self.result('Возможны помехи.')) as model:
            answer = self.bot.generate(self.job('короче', response_control='concise', quoted_message=source))
        model.assert_called_once()
        self.assertNotEqual(answer, candidate)

    def test_specific_retries_preserve_question_and_supply_draft_as_untrusted_data(self):
        cases = [
            ('Почему WiFi медленнее кабеля?', 'Кабель стабильнее и без потерь.',
             'Возможны помехи. Сравни скорость на одном устройстве.', 'Убери гарантию передачи без потерь', 'Сохрани задачу проверки'),
            ('Как проверить резервную копию?', 'Восстанови отдельно. Если нет — битая.',
             'Восстанови отдельно проверь полноту данных и работу приложения.', 'Сохрани задачу проверки резервной копии', 'Объясни различия WiFi'),
            ('Почему WiFi медленнее кабеля?', 'Если разница есть проблема в WiFi а не в провайдере.',
             'Разница скорости помогает сузить возможные причины.', 'не исключай провайдера', 'Сохрани задачу проверки')]
        for subject, bad, good, expected, unrelated in cases:
            with patch('bot.http_json', side_effect=[self.result(bad), self.result(good)]) as model:
                self.assertEqual(self.bot.generate(self.job(subject)), good)
            self.assertEqual(model.call_count, 2)
            payload = model.call_args.args[1]
            prompt = payload['messages'][0]['content']
            self.assertIn(expected, prompt)
            self.assertNotIn(unrelated, prompt)
            self.assertEqual(json.loads(payload['messages'][1]['content'])['current_request'], subject)
            self.assertEqual(json.loads(payload['messages'][1]['content'])['correction_candidate']['answer'], bad)
            self.assertNotIn(bad, prompt)

    def test_targeted_correction_receives_draft_and_faulty_fragment_and_keeps_useful_part(self):
        useful = 'WiFi может быть медленнее из-за помех и расстояния.'
        bad = useful + ' Кабель обеспечивает соединение без внешних влияний.'
        good = useful + ' Кабель меньше зависит от радиопомех.'
        job = self.job('Почему WiFi медленнее кабеля?')
        with patch('bot.http_json', side_effect=[self.result(bad), self.result(good)]) as model:
            self.assertEqual(self.bot.generate(job), good)
        self.assertEqual(model.call_count, 2)
        first, second = [call.args[1]['messages'] for call in model.call_args_list]
        self.assertNotIn('correction_candidate', json.loads(first[1]['content']))
        data = json.loads(second[1]['content'])
        self.assertEqual(data['current_request'], job['text'])
        self.assertEqual(data['correction_candidate']['answer'], bad)
        self.assertEqual(data['correction_candidate']['rule'], 'grounding.cable_external_immunity')
        self.assertIn('без внешних влияний', data['correction_candidate']['faulty_fragment'])
        self.assertIn('непроверенный черновик', second[0]['content'])
        self.assertNotIn('Ответь заново', second[0]['content'])
        self.assertEqual(job['_correction_attempts'], [2])
        self.assertNotIn('_rejection_details', job)

    def test_targeted_correction_still_rejects_a_different_guarantee_and_never_calls_third_time(self):
        job = self.job('Почему WiFi медленнее кабеля?', _capture_rejections=True)
        with patch('bot.http_json', side_effect=[self.result('Кабель без внешних влияний.'),
                                                self.result('Кабель передаёт данные без потерь.')]) as model:
            self.assertIsNone(self.bot.generate(job))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_correction_attempts'], [2])
        self.assertEqual([detail['rule'] for detail in job['_rejection_details']],
                         ['grounding.cable_external_immunity', 'grounding.cable_no_loss'])

    def test_sensitive_rejections_are_not_repair_sources_and_prompt_slogan_is_removed(self):
        from question_answers import PROMPT, NEUTRAL_PROMPT
        for prompt in [PROMPT, NEUTRAL_PROMPT]:
            self.assertNotIn('гипотеза остаётся гипотезой', prompt)
        token = '123456789:' + 'A' * 30
        with patch('bot.http_json', side_effect=[self.result(token), self.result('Возможны помехи.')]) as model:
            self.assertEqual(self.bot.generate(self.job('Почему WiFi медленнее кабеля?')), 'Возможны помехи.')
        second = model.call_args.args[1]
        self.assertNotIn('correction_candidate', json.loads(second['messages'][1]['content']))
        self.assertNotIn(token, str(second))

    def test_default_questions_use_neutral_prompt_and_spelling_without_archive_profile(self):
        from question_answers import NEUTRAL_PROMPT, substantive_style
        raw = 'Это, возможно, помехи. Ты проверь 0,25 и `ты, это`, а также «ты, это».'
        with patch('bot.profile_for_request') as profile, patch('bot.http_json', return_value=self.result(raw)) as model:
            answer = self.bot.generate(self.job('Почему WiFi медленнее кабеля?'))
        profile.assert_not_called()
        payload = model.call_args.args[1]
        self.assertEqual(payload['messages'][0]['content'], NEUTRAL_PROMPT)
        self.assertNotIn('speech_profile', json.loads(payload['messages'][1]['content']))
        self.assertEqual(answer, 'Ето, возможно, помехи. Ти проверь 0,25 и `ты, это`, а также «ты, это».')
        self.assertEqual(substantive_style('Спасибо, пожалуйста, проверь.'), 'Спасибо, пожалуйста, проверь.')
        self.assertEqual(substantive_style('```python\nprint("ты, это", [1, 2])\n```'),
                         '```python\nprint("ты, это", [1, 2])\n```')

    def test_cable_external_immunity_is_rejected_without_blocking_limited_or_negated_claims(self):
        bad = 'WiFi может быть медленнее. Кабель обеспечивает стабильное соединение без внешних влияний.'
        detail = []
        self.assertEqual(grounding_problem(bad, 'Почему WiFi медленнее кабеля?', detail), 'unsupported_certainty')
        self.assertEqual(detail[0]['rule'], 'grounding.cable_external_immunity')
        for answer in ['Кабель не гарантирует передачу без внешних влияний.',
                       'Кабель меньше зависит от радиопомех.', 'WiFi чувствителен к помехам и расстоянию.']:
            self.assertIsNone(grounding_problem(answer, 'Почему WiFi медленнее кабеля?'))
        with patch('bot.http_json', side_effect=[self.result(bad), self.result('Кабель меньше зависит от радиопомех.')]) as model:
            self.assertEqual(self.bot.generate(self.job('Почему WiFi медленнее кабеля?')), 'Кабель меньше зависит от радиопомех.')
        self.assertIn('Убери абсолютную гарантию', model.call_args.args[1]['messages'][0]['content'])

    def test_neutral_diagnostic_removes_persona_profile_and_output_styling_only(self):
        from question_answers import PROMPT, NEUTRAL_PROMPT
        self.assertIn('Ты — явно обозначенный бот-пародия', PROMPT)
        for marker in ['бот-пародия', 'Тон прямой', 'Манера:', '«ти»', 'speech_profile —']:
            self.assertNotIn(marker, NEUTRAL_PROMPT)
        self.assertEqual(PROMPT[PROMPT.index('quality_feedback —'):],
                         NEUTRAL_PROMPT[NEUTRAL_PROMPT.index('quality_feedback —'):])
        jobs = []
        for style in ['parody', 'neutral']:
            job = self.job('Почему сервер теряет соединение?', _diagnostic_question_style=style)
            with patch('bot.profile_for_request', return_value={'examples': ['style-sentinel']}) as profile, \
                    patch('bot.http_json', return_value=self.result('Это может быть связано с сетью.')) as model:
                answer = self.bot.generate(job)
            payload = model.call_args.args[1]
            data = json.loads(payload['messages'][1]['content'])
            if style == 'neutral':
                profile.assert_not_called()
                self.assertNotIn('speech_profile', data)
                self.assertEqual(answer, 'Это может быть связано с сетью.')
                self.assertEqual(payload['messages'][0]['content'], NEUTRAL_PROMPT)
            else:
                profile.assert_called_once()
                self.assertIn('speech_profile', data)
                self.assertNotEqual(answer, 'Это может быть связано с сетью.')
                self.assertEqual(payload['messages'][0]['content'], PROMPT)
            data.pop('speech_profile', None)
            jobs.append((data, payload['options'], payload['model']))
        self.assertEqual(jobs[0], jobs[1])

    def test_neutral_mode_keeps_identical_grounding_rules_attempts_and_diagnostics(self):
        case = next(case for case in json.loads((ROOT / 'dialogue_cases.json').read_text()) if case['id'] == 'real-question')
        results = []
        for style in ['parody', 'neutral']:
            with patch('bot.http_json', return_value=self.result('Кабель передаёт данные без потерь.')) as model:
                result = evaluate(case, self.config, run_model=True, capture_rejections=True, question_style=style)
            self.assertEqual(model.call_count, 2)
            self.assertEqual(result['errors'], ['empty_answer'])
            self.assertEqual(result['question_style'], style)
            results.append([detail['rule'] for detail in result['rejection_details']])
        self.assertEqual(results, [['grounding.cable_no_loss'] * 2] * 2)

    def test_compare_cli_runs_each_question_in_both_styles_and_labels_json_and_console(self):
        report = Path(self.temp.name) / 'comparison.json'
        output = io.StringIO()
        answers = ['Возможны помехи и слабый сигнал.',
                   'Восстанови резервную копию отдельно и проверь полноту данных.'] * 2
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json', return_value={}) as warmup, \
                patch('bot.http_json', side_effect=[self.result(answer) for answer in answers]) as model, \
                contextlib.redirect_stdout(output):
            result = main(['--run-model', '--question-style', 'compare', '--show-rejections',
                           '--case', 'real-question', '--case', 'pending-correction', '--report', str(report)])
        self.assertEqual(result, 0)
        warmup.assert_called_once()
        self.assertEqual(model.call_count, 4)
        rows = json.loads(report.read_text())
        self.assertEqual([(row['id'], row['question_style']) for row in rows],
                         [('real-question', 'parody'), ('pending-correction', 'parody'),
                          ('real-question', 'neutral'), ('pending-correction', 'neutral')])
        self.assertIn('real-question [parody]', output.getvalue())
        self.assertIn('real-question [neutral]', output.getvalue())
        payloads = [call.args[1] for call in model.call_args_list]
        for original, neutral in zip(payloads[:2], payloads[2:]):
            a = json.loads(original['messages'][1]['content'])
            b = json.loads(neutral['messages'][1]['content'])
            a.pop('speech_profile')
            self.assertEqual(a, b)
            self.assertEqual(original['options'], neutral['options'])

    def test_neutral_cli_refuses_parody_cases_and_missing_model_flag(self):
        for args in [['--question-style', 'neutral', '--case', 'real-question'],
                     ['--run-model', '--question-style', 'compare', '--case', 'banter-question']]:
            with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                    patch('check_dialogues.http_json') as model, contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 2)
            model.assert_not_called()

    def test_concise_mode_has_one_word_budget_instead_of_default_brief_budget(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        with patch('bot.http_json', return_value=self.result('слабый сигнал и помехи проверь кабель.')) as model:
            self.bot.generate(self.job('короче сохрани главное', response_control='concise', quoted_message=source))
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['detail'], 'concise')
        self.assertLess(data['max_words'], len(source.split()))
        self.assertIn('max_words', model.call_args.args[1]['messages'][0]['content'])

    def test_controls_share_system_prefix_and_expose_source_on_first_attempt(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        prompts = []
        for control in [None, 'concise', 'serious', 'plain', 'example']:
            with patch('bot.http_json', return_value=self.result('помехи могут снижать скорость.')) as model:
                self.bot.generate(self.job('Почему WiFi тормозит?', response_control=control,
                                           quoted_message=source))
            payload = model.call_args.args[1]
            prompts.append(payload['messages'][0]['content'])
            if control:
                data = json.loads(payload['messages'][1]['content'])
                self.assertEqual(data['rewrite_source'], source)
                self.assertEqual(data['response_control'], control)
        self.assertEqual(len(set(prompts)), 1)

    def test_real_broken_shortening_is_rejected_and_retried_with_source(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        case = cases['shorter']
        broken = 'Помехи расстояние перегрузка. Проверь кабель и рядом с роутером.'
        self.assertIn('incomplete_rewrite', assess(case, broken, 33, 120))
        good = 'Помехи могут снижать скорость. Сравни кабель и WiFi возле роутера.'
        self.assertEqual(assess(case, good, 10, 120), [])
        with patch('bot.http_json', side_effect=[self.result(broken), self.result(good)]) as model:
            job = self.job('короче сохрани главное', response_control='concise', quoted_message=case['dialogue'][-1])
            self.assertEqual(self.bot.generate(job), good)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_rejections'], ['incomplete_rewrite'])
        retry = model.call_args.args[1]['messages']
        self.assertIn('Сохрани смысл действий', retry[0]['content'])
        self.assertEqual(json.loads(retry[1]['content'])['rewrite_source'], case['dialogue'][-1])

    def test_two_overlong_rewrites_keep_checked_complete_source_sentence(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        case = cases['shorter']
        source = case['dialogue'][-1]
        job = self.job('короче сохрани главное', response_control='concise', quoted_message=source)
        with patch('bot.http_json', return_value=self.result('слишком длинный ответ ' * 20)) as model:
            answer = self.bot.generate(job)
        self.assertEqual(answer, 'Помехи и слабый сигнал могут снижать скорость.')
        self.assertEqual(assess(case, answer, 18, 120), [])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(job['_rewrite_fallback'], 'source_sentences')
        self.assertIn(f'максимум {concise_word_limit(source)} слов',
                      model.call_args.args[1]['messages'][0]['content'])

    def test_extraction_never_truncates_a_sentence_or_code_or_copies_unsafe_source(self):
        for source in ['Один ответ.', 'Незаконченный ответ без точки',
                       'Ошибка восстановления — значит копия битая. Проверь резервную копию и настройки доступа.']:
            job = self.job('короче', response_control='concise', quoted_message=source)
            with patch('bot.http_json', return_value=self.result('длинный ответ ' * 40)):
                self.assertIsNone(self.bot.generate(job), source)
        for source in ['Команда `rm -r`. Не запускай.', 'Пункт.\nЕщё пункт.',
                       'Он сказал «проверь». Потом ушёл.']:
            self.assertIsNone(concise_extract(source))

    def test_latest_vm_diagnostic_failures_are_rejected_across_question_modes(self):
        cases = {case['id']: case for case in json.loads((ROOT / 'dialogue_cases.json').read_text())}
        bad = [
            ('real-question', 'WiFi медленнее кабеля из-за помех. Сравни по кабелю и рядом с роутером — если скорость вырастет виноват WiFi.', 'unsupported_certainty'),
            ('serious', 'Проверь кабель и рядом с роутером — если сигнал пропадает при перемещении виноват кабель или интерфейс.', 'incomplete_rewrite'),
            ('pending-correction', 'Сделай тестовое восстановление. Если нет — либо повреждена либо не все данные скопированы.', 'unsupported_certainty')]
        for name, answer, reason in bad:
            self.assertIn(reason, assess(cases[name], answer, 10, 120))
        for answer in ['Если нет — возможно повреждена либо не все данные скопированы.',
                       'Если скорость вырастет это может указывать на проблемы WiFi.']:
            self.assertIsNone(grounding_problem(answer, 'Как проверить резервную копию и WiFi?'))

    def test_rewrite_preserves_hypotheses_but_does_not_require_modals_in_advice(self):
        case = next(case for case in json.loads((ROOT / 'dialogue_cases.json').read_text()) if case['id'] == 'shorter')
        source = case['dialogue'][-1]
        for answer in ['Помехи или слабый сигнал — причина тормозов.',
                       'Помехи снижают скорость.', 'Проблема в слабом сигнале.',
                       'Возможно провайдер тормозит. Помехи — причина снижения скорости.']:
            self.assertEqual(concise_problem(answer, source), 'lost_uncertainty', answer)
        self.assertIn('lost_uncertainty', assess(case, 'Помехи или слабый сигнал — причина тормозов.', 17, 120))
        for answer in ['Помехи могут снижать скорость.', 'Возможная причина — помехи.',
                       'Сравни соединение по кабелю и рядом с роутером.',
                       'Проверь возможные причины снижения скорости.',
                       'Помехи не всегда снижают скорость.']:
            self.assertIsNone(concise_problem(answer, source), answer)
        self.assertIsNone(concise_problem('Сервер выключен.', 'Сервер выключен. Включи его.'))

    def test_lost_uncertainty_is_retried_and_checked_extract_is_used_if_repeated(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        for second in ['Помехи могут снижать скорость.', 'Помехи — причина тормозов.']:
            job = self.job('короче сохрани главное', response_control='concise', quoted_message=source)
            with patch('bot.http_json', side_effect=[self.result('Помехи — причина тормозов.'), self.result(second)]) as model:
                answer = self.bot.generate(job)
            self.assertIn('могут', answer)
            self.assertEqual(model.call_count, 2)
            self.assertEqual(job['_rejections'][0], 'lost_uncertainty')
            self.assertIn('Сохрани степень уверенности', model.call_args.args[1]['messages'][0]['content'])
            if 'могут' not in second:
                self.assertEqual(job['_rewrite_fallback'], 'source_sentences')

    def test_network_comparison_does_not_prove_a_diagnosis_or_allow_unrelated_hedges(self):
        subject = 'Почему WiFi может быть медленнее кабеля?'
        bad = ['Проверь кабель — если скорость вырастет проблема в WiFi.',
               'Если по кабелю быстрее то дело в роутере.',
               'Попробуй подключить кабель: если скорость вырастет причина в WiFi.',
               'Возможно это помехи. Если сигнал пропадает виноват кабель.']
        for answer in bad:
            self.assertEqual(grounding_problem(answer, subject), 'unsupported_certainty', answer)
        for answer in ['Если скорость вырастет проблема может быть в WiFi.',
                       'Если по кабелю быстрее то вероятно дело в роутере.',
                       'Если скорость вырастет проверь WiFi и настройки.',
                       'Если скорость вырастет это не доказывает что проблема в WiFi.',
                       'Проблема в WiFi подтверждена измерениями и диагностикой оборудования.']:
            self.assertIsNone(grounding_problem(answer, subject), answer)

    def test_rejection_reports_are_opt_in_and_draft_is_never_logged_or_used_as_instruction(self):
        bad = 'В логах будет точная причина. rejected-answer-sentinel'
        for capture in [False, True]:
            job = self.job('Почему сервер теряет соединение?', _capture_rejections=capture)
            with patch('bot.http_json', return_value=self.result(bad)) as model, self.assertLogs('volodymyr', level='WARNING') as logs:
                self.assertIsNone(self.bot.generate(job))
            self.assertNotIn('rejected-answer-sentinel', ' '.join(logs.output))
            retry = model.call_args.args[1]['messages']
            self.assertNotIn('rejected-answer-sentinel', retry[0]['content'])
            self.assertEqual(json.loads(retry[1]['content'])['correction_candidate']['answer'], bad)
            if capture:
                self.assertEqual(len(job['_rejection_details']), 2)
                detail = job['_rejection_details'][0]
                self.assertEqual(detail['answer'], bad)
                self.assertEqual(detail['rule'], 'grounding.logs_guarantee')
                self.assertEqual(detail['attempt'], 1)
                self.assertIn('точная причина', detail['fragment'])
            else:
                self.assertNotIn('_rejection_details', job)

    def test_diagnostic_capture_redacts_identifiers_and_records_length_limits(self):
        token = '123456789:' + 'A' * 30
        bad = token + ' https://example.com/private user@example.com'
        job = self.job('Почему сервер теряет соединение?', _capture_rejections=True)
        with patch('bot.http_json', return_value=self.result(bad)):
            self.bot.generate(job)
        captured = json.dumps(job['_rejection_details'])
        for value in [token, 'example.com', 'user@']:
            self.assertNotIn(value, captured)
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        job = self.job('короче сохрани главное', response_control='concise', quoted_message=source, _capture_rejections=True)
        with patch('bot.http_json', return_value=self.result('слишком длинный ответ ' * 20)):
            self.bot.generate(job)
        detail = job['_rejection_details'][0]
        self.assertEqual(detail['rule'], 'rewrite.word_limit')
        self.assertEqual(detail['words'], 60)
        self.assertEqual(detail['max_words'], concise_word_limit(source))

    def test_cli_show_rejections_includes_both_failed_attempts_in_console_and_report(self):
        report = Path(self.temp.name) / 'diagnostic.json'
        output = io.StringIO()
        bad = 'Если скорость вырастет проблема в WiFi.'
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json', return_value={}), \
                patch('bot.http_json', return_value=self.result(bad)), contextlib.redirect_stdout(output):
            result = main(['--run-model', '--case', 'real-question', '--show-rejections', '--report', str(report)])
        self.assertEqual(result, 1)
        data = json.loads(report.read_text())[0]
        self.assertEqual(data['errors'], ['empty_answer'])
        self.assertEqual([detail['attempt'] for detail in data['rejection_details']], [1, 2])
        self.assertEqual(data['rejection_details'][1]['rule'], 'grounding.network_comparison_verdict')
        self.assertEqual(data['rejection_details'][1]['answer'], bad)
        self.assertIn('Отклонённая попытка 2', output.getvalue())
        self.assertIn('Сработавший фрагмент:', output.getvalue())
        self.assertIn(bad, output.getvalue())

    def test_shortening_retry_keeps_the_specific_source_after_removing_old_assistant_turns(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        self.store.add_message(dict(chat={'id': -100}, message_id=1, date=time.time(), text='Почему WiFi тормозит?'))
        self.store.add_message(dict(chat={'id': -100}, message_id=2, date=time.time(), text=source,
                                    _reply_to=1, _answer_mode='question'), human=False)
        with patch('bot.http_json', side_effect=[self.result('слишком длинный ответ ' * 20),
                    self.result('помехи и слабый сигнал сравни соединение по кабелю.')]) as model:
            self.bot.generate(self.job('короче сохрани главное', dialogue_id=2, response_control='concise'))
        self.assertEqual(model.call_count, 2)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['rewrite_source'], source)
        self.assertTrue(all(row['human'] for row in data['dialogue']))

    def test_standalone_control_retains_diagnostic_scope_for_certainty_checks(self):
        self.store.add_message(dict(chat={'id': -100}, message_id=1, date=time.time(), text='Почему сервер теряет соединение?'))
        self.store.add_message(dict(chat={'id': -100}, message_id=2, date=time.time(),
                                    text='проверь логи сервера и сетевое соединение.',
                                    _reply_to=1, _answer_mode='question'), human=False)
        with patch('bot.http_json', side_effect=[self.result('там будет точная причина.'),
                    self.result('логи могут помочь проверить версии но нужна диагностика.')]) as model:
            answer = self.bot.generate(self.job('без приколов', dialogue_id=2, response_control='serious'))
        self.assertEqual(model.call_count, 2)
        self.assertIn('могут помочь', answer)

    def test_uncertainty_negation_and_quoted_claims_are_not_rejected(self):
        for text in ['Возможны помехи проверь скорость по кабелю.',
                     'Логи не всегда показывают причину сбоя.',
                     'Кабель не гарантирует передачу без потерь.',
                     'Не факт что там точно написано шо сломалось.',
                     'Неудачное восстановление не означает что копия сломана.',
                     'Фразу «там точно написано» нельзя считать диагнозом.']:
            self.assertIsNone(grounding_problem(text, 'Почему сервер теряет соединение и как проверить резервную копию?'), text)

    def test_pronoun_fault_without_named_subject_gets_local_question_without_model(self):
        for request in ['Он опять не работает. Почему?', 'Почему она не запускается?',
                        'Они снова зависают?', 'Бубус, оно не включается?']:
            with patch('bot.http_json') as model:
                job = self.job(request)
                answer = self.bot.generate(job)
            model.assert_not_called()
            self.assertTrue(answer.endswith('?'))
            self.assertEqual(job['_clarification_reason'], 'missing_reference')
            self.assertEqual(job['_local_reply'], 'clarification')

    def test_named_subject_in_request_quote_or_human_dialogue_preserves_model_explanation(self):
        for request, quoted in [('Он, принтер, опять не работает. Почему?', ''),
                                ('Он опять не работает. Почему?', 'Принтер зажевал бумагу.')]:
            with patch('bot.http_json', return_value=self.result('Проверь подачу бумаги в принтере.')) as model:
                answer = self.bot.generate(self.job(request, quoted_message=quoted))
            model.assert_called_once()
            self.assertIn('принтер', answer)
        self.store.add_message(dict(chat={'id': -100}, message_id=1, date=time.time(), text='Принтер зажевал бумагу.'))
        self.store.add_message(dict(chat={'id': -100}, message_id=2, date=time.time(),
                                    text='Проверь подачу бумаги.', _reply_to=1, _answer_mode='question'), human=False)
        with patch('bot.http_json', return_value=self.result('Проверь лоток принтера.')) as model:
            self.bot.generate(self.job('Он опять не работает. Почему?', dialogue_id=2))
        model.assert_called_once()

    def test_pronoun_only_quote_does_not_invent_a_subject_and_cli_skips_warmup(self):
        with patch('bot.http_json') as model:
            job = self.job('Он опять не работает. Почему?', quoted_message='Он опять не работает.')
            self.bot.generate(job)
        model.assert_not_called()
        report = Path(self.temp.name) / 'pronoun.json'
        output = io.StringIO()
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json') as warmup, patch('bot.http_json') as model, \
                contextlib.redirect_stdout(output):
            result = main(['--run-model', '--cases', str(ROOT / 'dialogue_cases_fresh.json'),
                           '--case', 'fresh-missing-reference', '--report', str(report)])
        self.assertEqual(result, 0)
        warmup.assert_not_called()
        model.assert_not_called()
        data = json.loads(report.read_text())[0]
        self.assertEqual(data['clarification_reason'], 'missing_reference')
        self.assertFalse(data['model_checked'])
        self.assertEqual(data['errors'], [])
        self.assertIn('локальное уточнение', output.getvalue())

    def test_ambiguous_subject_without_context_asks_specific_question_without_inference(self):
        job = self.job('Жёлтые?')
        with patch('bot.http_json') as model:
            answer = self.bot.generate(job)
        model.assert_not_called()
        self.assertTrue(specific_clarification(answer))
        self.assertIn('жёлтые', answer)
        self.assertEqual(job['_local_reply'], 'clarification')
        self.assertTrue(answer.endswith('?'))

    def test_all_local_clarification_variants_are_specific_and_rotate(self):
        replies = []
        for number in range(3):
            reply = self.bot.generate(self.job('Жёлтые?'))
            replies.append(reply)
            self.store.add_message(dict(chat={'id': -100}, message_id=100 + number,
                                        date=time.time(), text=reply, _answer_mode='question'), human=False)
        self.assertEqual(len(set(replies)), 3)
        self.assertTrue(all(specific_clarification(reply) for reply in replies))

    def test_ambiguous_word_with_actual_reply_context_keeps_model_explanation(self):
        with patch('bot.http_json', return_value=self.result('жёлтые карточки ето предупреждения игрокам.')) as model:
            answer = self.bot.generate(self.job('Жёлтые?', quoted_message='Судья показал несколько карточек игрокам.'))
        self.assertIn('карточки', answer)
        model.assert_called_once()

    def test_rewrite_may_reuse_own_source_but_other_answer_still_cannot_repeat(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        shorter = 'помехи и слабый сигнал снижают скорость. сравни соединение по кабелю.'
        self.assertTrue(explanation_problem(shorter, [source], 'Почему WiFi тормозит?'))
        self.assertIsNone(explanation_problem(shorter, [source], 'Почему WiFi тормозит?', reference=source))
        self.assertEqual(explanation_problem(source, [source], 'короче', reference=source), 'repeated_response')
        unrelated = 'Карточки продаются на eBay и TCGPlayer доставка в Россию ограничена.'
        self.assertEqual(explanation_problem(unrelated, [source, unrelated], 'короче', reference=source), 'repeated_response')

    def test_shorter_request_does_not_trigger_a_second_inference_for_legitimate_rewording(self):
        source = 'Помехи и слабый сигнал могут снижать скорость. Сравни соединение по кабелю и рядом с роутером.'
        self.store.add_message(dict(chat={'id': -100}, message_id=1, date=time.time(), text='Почему WiFi тормозит?'))
        self.store.add_message(dict(chat={'id': -100}, message_id=2, date=time.time(), text=source,
                                    _reply_to=1, _answer_mode='question'), human=False)
        reply = 'помехи могут снижать скорость. сравни соединение по кабелю.'
        with patch('bot.http_json', return_value=self.result(reply)) as model:
            self.assertEqual(self.bot.generate(self.job('короче сохрани главное', dialogue_id=2, response_control='concise')), reply)
        model.assert_called_once()

    def test_bad_diagnostic_guarantee_retries_with_reason_and_no_guarantee_is_sent(self):
        job = self.job('Почему мой сервер теряет соединение?')
        with patch('bot.http_json', side_effect=[self.result('в логах там точно написано шо сломалось.'),
                    self.result('без логов причину не определить проверь ошибки сети.')]) as model:
            answer = self.bot.generate(job)
        self.assertEqual(model.call_count, 2)
        self.assertNotIn('точно', answer)
        self.assertEqual(job['_rejections'], ['unsupported_certainty'])
        self.assertEqual(job['_metrics']['model_calls'], 2)
        retry_prompt = model.call_args.args[1]['messages'][0]['content']
        self.assertIn('Назови возможные причины', retry_prompt)
        self.assertNotIn('Сбой восстановления сам по себе не доказывает', retry_prompt)

    def test_runner_context_stays_the_same_for_question_parody_warmup_summary_and_factcheck(self):
        options = []
        for text, extra in [('Почему небо синее?', {}), ('цацик ты где', {'thoughtful': False}),
                            ('короче', {'response_control': 'concise'})]:
            with patch('bot.find_examples', return_value=[]), \
                    patch('bot.http_json', return_value=self.result('ти ещо громче поори')) as call:
                self.bot.generate(self.job(text, **extra))
            options.append(call.call_args.args[1]['options'])
        with patch('check_dialogues.http_json', return_value={}) as call:
            warm_model(self.config)
        options.append(call.call_args.args[1]['options'])
        data = dict(topics=[dict(title='Тема', text='Обсудили выбор.', quip='ну выбирайте', sources=[1])],
                    decisions=[], open_questions=[])
        with patch('community.http_json', return_value=self.result(json.dumps(data))) as call:
            summary_text(self.config, [dict(speaker='Участник', text='выбираем', timestamp=time.time())], 1, 1)
        options.append(call.call_args.args[1]['options'])
        checker = FactChecker()
        data = dict(verdict='unclear', answer='подтвердить пока не получается.', sources=[1])
        with patch.object(checker, 'evidence', return_value=([dict(id=1, title='Источник', text='текст', url='https://www.python.org/')], time.time())), \
                patch('fact_check.http_json', return_value=self.result(json.dumps(data))) as call:
            checker.answer('проверка факта', self.config)
        options.append(call.call_args.args[1]['options'])
        self.assertEqual({option['num_ctx'] for option in options}, {MODEL_CONTEXT})
        self.assertEqual({option['num_thread'] for option in options}, {16})

    def test_nanosecond_stats_and_retries_are_summed_without_prompts(self):
        result = dict(load_duration=30_000_000_000, prompt_eval_duration=2_000_000_000,
                      eval_duration=5_000_000_000, total_duration=38_000_000_000,
                      prompt_eval_count=100, prompt_eval_cached_count=70, eval_count=50)
        result['message'] = {'content': 'private-question-sentinel'}
        stats = generation_stats(result, 40)
        self.assertEqual(stats['load_seconds'], 30)
        self.assertEqual(stats['tokens_per_second'], 10)
        self.assertEqual(stats['other_seconds'], 3)
        job = {}
        record_generation(job, result, 40)
        record_generation(job, result, 40)
        self.assertEqual(job['_metrics']['model_calls'], 2)
        self.assertEqual(job['_metrics']['load_seconds'], 60)
        self.assertEqual(job['_metrics']['output_tokens'], 100)
        self.assertNotIn('private-question-sentinel', str(job))
        self.assertNotIn('message', job['_metrics'])

    def test_missing_and_invalid_stats_are_unknown_instead_of_fabricated_zero(self):
        stats = generation_stats(dict(load_duration=True, eval_duration=-1,
                                     prompt_eval_duration=float('nan'), total_duration=10 ** 1000), 1)
        self.assertEqual(stats, dict(seconds=1, context=MODEL_CONTEXT))
        result = dict(answer_checked=True, seconds=1, calls=[stats], metrics={})
        self.assertIn('не вернула подробные метрики', timing_text(result))
        self.assertNotIn('загрузка: 0', timing_text(result))

    def test_replay_distinguishes_local_clarification_from_actual_model_calls(self):
        case = dict(id='ambiguous', mode='question', request='Жёлтые?', require_clarification=True)
        with patch('bot.http_json') as model:
            result = evaluate(case, self.config, run_model=True)
        model.assert_not_called()
        self.assertFalse(result['model_checked'])
        self.assertTrue(result['answer_checked'])
        self.assertFalse(result['errors'])
        self.assertIn('без Ollama', timing_text(result))

    def test_cli_local_only_check_never_loads_model_and_writes_honest_report(self):
        report = Path(self.temp.name) / 'report.json'
        output = io.StringIO()
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json') as warmup, patch('bot.http_json') as model, \
                contextlib.redirect_stdout(output):
            result = main(['--run-model', '--case', 'ambiguous-subject', '--report', str(report)])
        self.assertEqual(result, 0)
        warmup.assert_not_called()
        model.assert_not_called()
        self.assertIn('локальных уточнений: 1', output.getvalue())
        self.assertFalse(json.loads(report.read_text())[0]['model_checked'])

    def test_cli_model_unavailable_exits_before_scenarios_without_leaking_url(self):
        output = io.StringIO()
        with patch('check_dialogues.CONFIG', Path(self.temp.name) / 'missing.json'), \
                patch('check_dialogues.http_json', side_effect=APIError('private-url-token', 503)), \
                patch('check_dialogues.evaluate') as evaluate, contextlib.redirect_stdout(output):
            result = main(['--run-model', '--case', 'real-question'])
        self.assertEqual(result, 1)
        self.assertIn('HTTP=503', output.getvalue())
        self.assertNotIn('private-url-token', output.getvalue())
        evaluate.assert_not_called()
