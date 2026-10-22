import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from answer_quality import contextual_request, explanation_problem, question_context
from bot import Bot, Store
from common import MODEL
from question_answers import FALLBACK, answer_style
from test_bot import FakeTelegram

# Regression excerpts: a new question was incorrectly dragged back to cards.
FIRST=('ти про кого? Жёлтые — ето шо карточки? Махачкала — не место для коллекционирования '
       'там скорее пыль и бардак. если ищи — ищи в интернете а не тут.')
SECOND=('ти про кого? если речь о карточках Pokémon — их цена зависит от редкости но доставку '
        'в Россию сечас реально не организовать. ето не шо-то из Махачкалы ето интернет и '
        'аукционы. если хочеш — ищи на сайтах типа eBay или TCGPlayer там цены есть.')
THIRD=('ти про кого? если речь о карточках — их нету тут ето не магазин. если хочеш — ищи '
       'на eBay или TCGPlayer там цены есть. доставка в Россию сечас — как в Махачкалу: '
       'реально не организовать.')
NEW_QUESTION='где мой цаца свиноголовин?'


class QualityTests(unittest.TestCase):
    def test_screenshot_recycled_paragraph_is_rejected(self):
        self.assertIn(explanation_problem(THIRD,[FIRST,SECOND],NEW_QUESTION),
                      ('repeated_response','stale_context','repeated_opening'))

    def test_shuffled_paraphrase_of_same_advice_is_rejected(self):
        old='Карточки Pokémon продают на eBay и TCGPlayer. Доставка в Россию недоступна из-за ограничений.'
        new='На TCGPlayer и eBay продают карточки Pokémon. Из-за ограничений доставка в Россию недоступна.'
        self.assertTrue(explanation_problem(new,[old],NEW_QUESTION))

    def test_third_same_opening_is_rejected_even_when_rest_of_answer_changes(self):
        old=['ти про кого? поясни какой цвет имееш в виду.',
             'ти про кого? уточни модель устройства.']
        self.assertEqual(explanation_problem('ти про кого? назови номер версии Linux.',old,'Какой Linux выбрать?'),
                         'repeated_opening')

    def test_useful_wifi_followup_can_share_terms_and_first_instruction(self):
        old='проверь соединение по кабелю и сравни с WiFi'
        new='проверь соединение по кабелю. если скорость нормальная проверь помехи в WiFi.'
        self.assertIsNone(explanation_problem(new,[old],'Бубус почему роутер режет скорость?'))

    def test_reused_code_does_not_make_new_explanation_a_duplicate(self):
        old='Посмотри адрес интерфейса.\n```sh\nip -br address show\n```'
        new='Команда показывает состояние сетевых устройств и назначенные адреса.\n```sh\nip -br address show\n```'
        self.assertIsNone(explanation_problem(new,[old],'Объясни эту команду подробнее'))

    def test_display_normalization_does_not_hide_duplicate(self):
        self.assertEqual(explanation_problem('ты знаешь что это было', ['ти знаеш что ето било']),
                         'repeated_response')

    def test_new_topic_selects_only_relevant_humans(self):
        rows=[dict(speaker='А',text='Сколько стоят карточки Pokémon?',human=True),
              dict(speaker='Бубус',text=SECOND,human=False),
              dict(speaker='Б',text='цаца сегодня опять пропал',human=True)]
        self.assertEqual(question_context(rows,NEW_QUESTION),rows[-1:])
        self.assertEqual(question_context(rows[:2],NEW_QUESTION),[])

    def test_short_reference_keeps_human_context_but_not_bot_monologue(self):
        rows=[dict(speaker='А',text='Карточки Pokémon коллекционные',human=True),
              dict(speaker='Бубус',text=SECOND,human=False)]
        self.assertTrue(contextual_request('А сколько они стоят сейчас?'))
        self.assertEqual(question_context(rows,'А сколько они стоят сейчас?'),rows[:1])

    def test_reply_and_bare_call_do_not_collect_another_conversation(self):
        rows=[dict(speaker='А',text='Карточки Pokémon коллекционные',human=True)]
        self.assertEqual(question_context(rows,'Почему?',quoted='Нужен новый кабель.'),[])
        self.assertEqual(question_context(rows,'цацик?',fresh_call=True),[])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'bot.sqlite3'
        self.store=Store(self.path)
        self.store.set('chat_id',-100)
        self.telegram=FakeTelegram()
        self.bot=Bot(dict(owner_id=42,model=MODEL,ollama_url='http://127.0.0.1:11434'),
                     telegram=self.telegram,store=self.store)

    def remember(self,text,number,human=False,mode='question',stamp=None):
        self.store.add_message(dict(chat=dict(id=-100),message_id=number,
                               date=time.time()-10 if stamp is None else stamp,text=text,
                               _answer_mode=mode,**{'from':dict(id=7,first_name='Участник')}),human=human)

    def job(self,text=NEW_QUESTION,**kwargs):
        return dict(dict(chat_id=-100,text=text,reply_to=100,automatic=False,
                         queued_at=time.time(),user_id=7,thoughtful=True),**kwargs)

    @staticmethod
    def result(text):
        return dict(message=dict(content=text),done_reason='stop')

    def test_real_screenshot_loop_gets_one_new_answer_after_retry(self):
        self.remember(FIRST,1)
        self.remember(SECOND,2)
        # Utility notifications must not push meaningful answer history out.
        for n in range(3,45):
            self.remember('Матч завершён · 1:0',n,mode='football')
        fresh='тут я бля кого ти ещо потеряв'
        with patch('bot.http_json',side_effect=[self.result(THIRD),self.result(fresh)]) as model:
            self.bot.process_job(self.job())
        self.assertEqual(model.call_count,2)
        self.assertEqual(len(self.telegram.sent),1)
        self.assertEqual(self.telegram.sent[0][1],fresh)
        data=json.loads(model.call_args_list[0].args[1]['messages'][1]['content'])
        retry=json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['live_context'],[])
        self.assertNotIn('Pokémon',json.dumps(data,ensure_ascii=False))
        self.assertTrue(retry['previous_failure'])
        self.assertGreater(model.call_args.args[1]['options']['temperature'],0.35)

    def test_standalone_question_has_no_old_bot_output_or_unrelated_human_topic(self):
        self.remember('Сколько стоят карточки Pokémon?',1,human=True)
        self.remember(SECOND,2)
        with patch('bot.http_json',return_value=self.result('тут я кого потеряв опять')) as model:
            self.bot.generate(self.job())
        data=json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['live_context'],[])
        self.assertEqual(data['context_scope'],'current_request')

    def test_context_cannot_read_messages_after_queued_question_even_with_old_timestamp(self):
        now=time.time()
        self.remember('У роутера пропадает WiFi',1,human=True,stamp=now-60)
        self.remember('У роутера другая неисправность',101,human=True,stamp=now-1)
        self.remember('Ещё один роутер сломан',2,human=True,stamp=now+60)
        with patch('bot.http_json',return_value=self.result('проверь питание роутера')) as model:
            self.bot.generate(self.job('Почему роутер греется?',queued_at=now))
        data=json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual([r['text'] for r in data['live_context']],['У роутера пропадает WiFi'])

    def test_bare_nickname_question_does_not_revive_an_old_subject(self):
        self.remember('Карточки Pokémon коллекционные',1,human=True)
        self.remember(SECOND,2)
        with patch('bot.http_json',return_value=self.result('шо ти знов меня зовеш')) as model:
            self.bot.generate(self.job('Володька?'))
        data=json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['live_context'],[])
        self.assertEqual(data['dialogue'],[])

    def test_retry_removes_old_assistant_turns_from_explicit_chain(self):
        self.remember('Сколько стоят карточки?',1,human=True)
        self.store.add_message(dict(chat=dict(id=-100),message_id=2,date=time.time()-9,text=SECOND,
                                   _reply_to=1,_answer_mode='question'),human=False)
        with patch('bot.http_json',side_effect=[self.result(SECOND),self.result('уточни какие именно карточки')]) as model:
            self.bot.generate(self.job('А сколько они стоят сейчас?',dialogue_id=2))
        retry=json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertTrue(all(r['human'] for r in retry['dialogue']))
        self.assertNotIn(SECOND,json.dumps(retry,ensure_ascii=False))

    def test_fallback_clarifications_rotate_instead_of_repeating_fixed_sentence(self):
        self.assertEqual(self.bot.question_fallback(self.job()),FALLBACK)
        self.remember(FALLBACK,1)
        alternate=self.bot.question_fallback(self.job())
        self.assertNotEqual(alternate,FALLBACK)
        self.remember(alternate,2)
        self.assertNotIn(self.bot.question_fallback(self.job()),(FALLBACK,alternate))

    def test_recent_model_answer_history_uses_full_body_and_survives_restart(self):
        text='начало '+('содержательное объяснение '*60)+'конец ответа'
        self.remember(text,1)
        self.remember('Данные валют',2,mode='currency')
        self.assertEqual(Store(self.path).recent_model_answers(-100),[text])
        self.assertEqual(Store(self.path).recent_model_answers(-200),[])

    def test_actual_football_sends_and_legacy_untyped_service_messages_do_not_flush_history(self):
        self.remember(FIRST,1)
        for n in range(35):
            self.bot.send_football(-100,'Матч завершён · 1:0')
        self.store.add_message(dict(chat=dict(id=-100),message_id=999,date=time.time(),
                                   text='⚽ Ростов — Акрон · 1:0\nФутбол на Куличках · проверено 20:00 МСК'),
                               human=False,learnable=False,rateable=False)
        self.assertEqual(self.store.recent_model_answers(-100),[FIRST])
