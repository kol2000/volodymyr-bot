import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import Bot, Store, answer_key, repeated_answer
from common import MODEL, parody_text
from feedback import salary_acknowledgement
from question_answers import answer_style
from question_answers import PROMPT as QUESTION_PROMPT
from common import ROOT
from test_bot import FakeTelegram


class SpeechTests(unittest.TestCase):
    def test_genitive_spelling_is_a_replacement_and_not_an_added_catchphrase(self):
        for text,expected in [('чего тут непонятного','чего тут непонятново'),
                              ('ничего непонятного нет','ничего непонятново нет'),
                              ('Непонятного человека привели','Непонятново человека привели'),
                              ('непонятно шо ти имееш в виду','непонятно шо ти имееш в виду'),
                              ('ти сначала текст прочитай','ти сначала текст прочитай')]:
            with self.subTest(text=text):
                self.assertEqual(parody_text(text),expected)

    def test_screenshot_adverb_misuse_is_fixed_without_changing_valid_genitive(self):
        text='Если не читаеш текст — брать его некуда. Непонятново шо ти имееш в виду.'
        self.assertEqual(parody_text(text),text.replace('Непонятново','Непонятно'))
        self.assertEqual(parody_text('мне непонятново как ето работает'),'мне непонятно как ето работает')
        self.assertEqual(parody_text('НЕПОНЯТНОВО что делать'),'НЕПОНЯТНО что делать')
        self.assertEqual(parody_text('чего тут непонятново шо делать дальше'),'чего тут непонятново шо делать дальше')
        self.assertEqual(parody_text('цитата «Непонятново шо ти имееш в виду»'),
                         'цитата «Непонятново шо ти имееш в виду»')

    def test_model_prompts_do_not_prime_the_optional_spelling_word(self):
        for prompt in ((ROOT/'prompt.txt').read_text(),QUESTION_PROMPT):
            self.assertNotIn('непонятново',prompt.lower())

    def test_screenshot_politeness_and_commas_are_removed(self):
        self.assertEqual(parody_text('Если ты про кого-то из реальных людей — уточни, пожалуйста.'),
                         'Если ти про кого-то из реальных людей — уточни.')
        self.assertEqual(parody_text('Спасибо за вопрос. Ты, пожалуйста, уточни.'), 'Ти уточни.')
        self.assertEqual(parody_text('извините, но я не знаю, когда это произойдёт'),
                         'но я не знаю когда ето произойдёт')

    def test_favourite_phrase_replaces_only_existing_equivalent(self):
        for original, expected in [('более-менее', 'болья менья'), ('более менее', 'болья менья'),
                                   ('Более–менее', 'Болья менья'), ('БОЛЕЕ-МЕНЕЕ', 'БОЛЬЯ МЕНЬЯ')]:
            with self.subTest(original=original):
                self.assertEqual(parody_text(original), expected)
        self.assertEqual(parody_text('ти провод сначала проверь'), 'ти провод сначала проверь')
        self.assertEqual(parody_text('более точный ответ менее понятен'), 'более точный ответ менее понятен')

    def test_numbers_code_quotes_and_request_literals_are_preserved(self):
        text = ('ты, пожалуйста, проверь 0,25 BTC и 1,5 часа\n'
                '```python\nprint("спасибо", "более-менее", [1, 2])\n```\n'
                'цитата «ты, спасибо, более-менее» и `пожалуйста, i = 1,2`')
        result = answer_style(text)
        self.assertTrue(result.startswith('ти проверь 0,25 BTC и 1,5 часа'))
        self.assertIn('print("спасибо", "более-менее", [1, 2])', result)
        self.assertIn('«ты, спасибо, более-менее»', result)
        self.assertIn('`пожалуйста, i = 1,2`', result)
        self.assertEqual(answer_style(result), result)

    def test_commas_without_spaces_do_not_glue_words_and_newlines_survive(self):
        self.assertEqual(parody_text('да,нет, может\n1. проверь,потом решай'),
                         'да нет может\n1. проверь потом решай')

    def test_normalization_does_not_bypass_duplicate_filter(self):
        self.assertEqual(answer_key('ти более-менее знаешь, как это работает'),
                         answer_key('ти болья менья знаеш как ето работает'))
        self.assertTrue(repeated_answer('ти более-менее знаешь как это работает',
                                       ['ти болья менья знаеш как ето работает']))

    def test_old_and_new_salary_receipts_remain_linkable(self):
        for text in ('поняв ету фразу запомнив спасибо за науку', 'поняв спасибо за оценку',
                     'поняв ету фразу запомнив', 'поняв оценку записав'):
            self.assertEqual(salary_acknowledgement(text + '\n+80 ₴ · банк: 160 ₴'), 1)

    def test_model_archive_and_send_all_apply_style_without_editing_request(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'bot.sqlite3')
            store.set('chat_id', -100)
            telegram = FakeTelegram()
            bot = Bot(dict(owner_id=42, model=MODEL, ollama_url='http://127.0.0.1:11434'),
                      telegram=telegram, store=store)
            job = dict(chat_id=-100, text='как, пожалуйста, сделать более-менее?',
                       automatic=False, reply_to=1, queued_at=time.time())
            raw = 'Спасибо за вопрос. Ты, пожалуйста, проверь кабель — там более-менее понятно.'
            with patch('bot.http_json', return_value={'message': {'content': raw}}) as model:
                bot.process_job(job)
            self.assertEqual(telegram.sent[0][1], 'Спасибо за вопрос. Ти, пожалуйста, проверь кабель — там более-менее понятно.')
            self.assertEqual(model.call_args.args[1]['messages'][1]['role'], 'user')
            self.assertIn('как, пожалуйста, сделать более-менее?', model.call_args.args[1]['messages'][1]['content'])
            with patch('bot.random_candidates', return_value=['ти, бля, более-менее ожив']):
                self.assertEqual(bot.generate(dict(job, text='', random_quote=True)), 'ти бля болья менья ожив')

