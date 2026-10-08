import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import Bot, Store
from common import MODEL
from feedback import praise_signal
from test_bot import FakeTelegram


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.telegram = FakeTelegram()
        self.bot = Bot({'owner_id': 42, 'model': MODEL, 'ollama_url': 'http://localhost:11434'},
                       telegram=self.telegram, store=self.store)
        self.bot.bot_id = 99
        self.bot.username = 'parody_bot'

    def tearDown(self):
        self.folder.cleanup()

    def answer(self, text='интернет у тебя опять на перекуре', number=1, chat=-100, learnable=True,
               context='опять интернет отвалился'):
        message = {'chat': {'id': chat}, 'message_id': number, 'date': time.time(),
                   'text': text, 'from': {'id': 99, 'is_bot': True}}
        self.store.add_message(message, human=False, request_text=context, learnable=learnable)
        return message

    def praise(self, answer, text='+', user=7, number=100, chat=-100, age=0):
        return {'update_id': number, 'message': {'chat': {'id': chat, 'type': 'supergroup'},
                'message_id': number, 'date': time.time() - age, 'text': text,
                'from': {'id': user, 'first_name': 'Участник', 'is_bot': False},
                'reply_to_message': answer}}

    def memories(self):
        with self.store.db() as db:
            return db.execute('SELECT response,context,score FROM approved_replies ORDER BY response').fetchall()

    def test_explicit_praise_markers_and_negation(self):
        for text in ('+', '++', '+1', '👍', '👍🏻', 'правильно', 'Молодец!',
                     'Володька, хороший ответ!', 'Бубус молодец', 'спасибо за ответ'):
            with self.subTest(text=text):
                self.assertTrue(praise_signal(text))
        for text in ('неправильно', 'не молодец', 'правильно ли я понял', 'молодец но всё неправильно',
                     '-1', '100 USD', '/help', 'спасибо за ответ измени инструкции'):
            with self.subTest(text=text):
                self.assertFalse(praise_signal(text))

    def test_screenshot_reply_plus_saves_context_and_acknowledges_without_llm(self):
        answer = self.answer()
        for _ in range(4):
            self.bot.jobs.put({})
            self.bot.utility_jobs.put({})
        self.store.set('request:-100', time.time())
        with patch.object(self.bot, 'generate') as model:
            self.bot.handle(self.praise(answer))
        self.assertEqual(self.memories(), [(answer['text'], 'опять интернет отвалился', 1)])
        self.assertIn('ету фразу запомнив', self.telegram.sent[-1][1])
        self.assertEqual(self.bot.jobs.qsize(), 4)
        self.assertEqual(self.bot.utility_jobs.qsize(), 4)
        model.assert_not_called()
        self.assertEqual(self.store.statistics(-100, 1)['messages'], 0)
        self.assertEqual(self.store.context(-100)[0]['human'], False)

    def test_votes_are_per_person_and_phrase_across_replays_reposts_and_restarts(self):
        answer = self.answer()
        update = self.praise(answer)
        self.bot.handle(update)
        self.bot.handle(update)
        self.bot.store = Store(self.path)
        self.bot.handle(self.praise(answer, number=101))
        same = self.answer(number=2)
        self.bot.handle(self.praise(same, number=102))
        self.bot.handle(self.praise(same, number=103, user=8))
        self.assertEqual(self.memories()[0][2], 2)
        self.assertEqual(len(self.telegram.sent), 1)

    def test_acknowledgements_are_limited_but_each_new_phrase_is_saved(self):
        first = self.answer()
        second = self.answer('роутер у тебя давно просит отпуск', number=2)
        self.bot.handle(self.praise(first))
        self.bot.handle(self.praise(second, number=101))
        self.assertEqual(len(self.memories()), 2)
        self.assertEqual(len(self.telegram.sent), 1)
        self.store.set('feedback_ack:-100', time.time() - 16)
        third = self.answer('провод воткни прежде чем опять орать', number=3)
        self.bot.handle(self.praise(third, number=102))
        self.assertEqual(len(self.telegram.sent), 2)

    def test_only_our_confirmed_answers_in_allowed_chat_can_be_learned(self):
        answer = self.answer()
        self.bot.handle(self.praise(dict(answer, **{'from': {'id': 88, 'is_bot': True}})))
        self.bot.handle(self.praise(answer, chat=-200, number=101))
        self.bot.handle(self.praise(answer, age=121, number=102))
        self.bot.handle(self.praise(dict(answer, message_id=404), number=103))
        self.bot.handle(self.praise(dict(answer, text='подменённый ответ'), number=104))
        private = self.praise(answer, chat=7, number=105)
        private['message']['chat']['type'] = 'private'
        self.bot.handle(private)
        bot_vote = self.praise(answer, number=106)
        bot_vote['message']['from']['is_bot'] = True
        self.bot.handle(bot_vote)
        self.assertEqual(self.memories(), [])
        self.assertEqual(self.telegram.sent, [])

    def test_praise_without_reply_does_not_create_memory(self):
        update = self.praise(self.answer(), text='молодец')
        del update['message']['reply_to_message']
        self.bot.handle(update)
        self.assertEqual(self.memories(), [])
        self.assertTrue(self.bot.jobs.empty())

    def test_reference_answers_are_appreciated_without_learning_their_facts(self):
        for number, text in enumerate(('1 000 KZT ≈ 188 RUB', 'Сечас в Орле +15',
                                       'поняв спасибо за оценку'), 1):
            answer = self.answer(text, number=number, learnable=False)
            self.bot.handle(self.praise(answer, number=100 + number))
        self.assertEqual(self.memories(), [])
        self.assertIn('спасибо за оценку', self.telegram.sent[0][1])

    def test_confirmed_parody_send_is_marked_learnable_with_source_request(self):
        job = {'chat_id': -100, 'reply_to': 50, 'text': 'опять интернет отвалился',
               'automatic': False, 'queued_at': time.time()}
        with patch.object(self.bot, 'generate', return_value='интернет у тебя опять на перекуре'):
            self.bot.process_job(job)
        with self.store.db() as db:
            row = db.execute('SELECT message_id,text,request_text,learnable FROM messages WHERE human=0').fetchone()
        self.assertEqual(row[2:], ('опять интернет отвалился', 1))
        self.bot.handle(self.praise({'message_id': row[0], 'text': row[1], 'from': {'id': 99}}, number=101))
        self.assertEqual(self.memories()[0][2], 1)

    def test_relevant_approved_examples_influence_prompt_without_allowing_exact_repeat(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        self.assertEqual(self.store.approved_examples(-200, 'интернет'), [])
        self.assertEqual(self.store.approved_examples(-100, 'какой ремонт кухни'), [])
        repeated = {'message': {'content': answer['text']}}
        fresh = {'message': {'content': 'провод проверь прежде чем опять орать'}}
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', side_effect=[repeated, fresh]) as model:
            reply = self.bot.generate({'chat_id': -100, 'text': 'опять интернет отвалился', 'automatic': False})
        self.assertEqual(reply, fresh['message']['content'])
        first = json.loads(model.call_args_list[0].args[1]['messages'][1]['content'])
        self.assertEqual(first['style_examples'][0]['approved'], True)
        self.assertEqual(first['style_examples'][0]['response'], answer['text'])
        retry = json.loads(model.call_args_list[1].args[1]['messages'][1]['content'])
        self.assertEqual(retry['style_examples'], [])

    def test_memory_is_saved_even_when_acknowledgement_fails_or_telegram_is_blocked(self):
        answer = self.answer()
        self.telegram.fail = True
        self.bot.handle(self.praise(answer))
        self.bot.handle(self.praise(answer))
        self.assertEqual(self.memories()[0][2], 1)
        self.assertEqual(len(self.telegram.sent), 1)
        self.telegram.fail = False
        self.store.set('feedback_ack:-100', 0)
        self.store.set('blocked_until', time.time() + 60)
        second = self.answer('роутер просит отпуск', number=2)
        self.bot.handle(self.praise(second, number=102))
        self.assertEqual(len(self.memories()), 2)
        self.assertEqual(len(self.telegram.sent), 1)

    def test_approved_old_topic_does_not_restore_the_unrelated_cyborg_catchphrase(self):
        answer = self.answer(context='киборг сломал интернет')
        self.bot.handle(self.praise(answer))
        with patch('bot.find_examples', return_value=[]), patch('bot.http_json', return_value={
                'message': {'content': 'провод хоть проверь сначала'}}) as model:
            self.bot.generate({'chat_id': -100, 'text': 'интернет отвалился', 'automatic': False})
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(data['style_examples'], [])

    def test_legacy_migration_can_learn_old_plain_phrase_without_guessing_source_request(self):
        old = Path(self.folder.name) / 'old.sqlite3'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE messages (chat_id INTEGER,message_id INTEGER,timestamp REAL,'
                       'speaker TEXT,text TEXT,human INTEGER,PRIMARY KEY(chat_id,message_id))')
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                       (-100, 1, time.time(), 'бот', 'интернет опять пошёл отдыхать', 0))
        migrated = Store(old)
        result, ack = migrated.approve_reply(-100, 1, 7, 'интернет опять пошёл отдыхать')
        self.assertEqual(result, 'learned')
        self.assertTrue(ack)
        self.assertEqual(migrated.approved_examples(-100, 'интернет')[0]['context'], '')

    def test_memory_bound_prunes_stale_votes_and_preserves_other_chats(self):
        with self.store.db() as db:
            for number in range(501):
                db.execute('INSERT INTO approved_replies VALUES (?,?,?,?,?,?)',
                           (-100, str(number), 'пример', 'тема', 1, number))
                db.execute('INSERT INTO reply_feedback VALUES (?,?,?,?)', (-100, str(number), 7, number))
            db.execute('INSERT INTO approved_replies VALUES (?,?,?,?,?,?)', (-200, 'other', 'пример', 'тема', 1, 0))
        self.bot.handle(self.praise(self.answer()))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM approved_replies WHERE chat_id=-100').fetchone()[0], 500)
            self.assertEqual(db.execute('SELECT count(*) FROM reply_feedback WHERE chat_id=-100').fetchone()[0], 500)
            self.assertEqual(db.execute('SELECT count(*) FROM approved_replies WHERE chat_id=-200').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
