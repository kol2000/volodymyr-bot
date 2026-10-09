import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import Store
from community import community_request
from feedback import rating_signal, salary_acknowledgement
import test_feedback as feedback_tests


class SalaryTests(unittest.TestCase):
    setUp = feedback_tests.FeedbackTests.setUp
    tearDown = feedback_tests.FeedbackTests.tearDown
    answer = feedback_tests.FeedbackTests.answer
    praise = feedback_tests.FeedbackTests.praise
    memories = feedback_tests.FeedbackTests.memories

    def test_signed_markers_are_single_votes_not_money_amounts_or_ordinary_praise(self):
        for text in ('+', '++', '+1', 'плюс', '👍🏼', 'Володька, плюс!'):
            with self.subTest(text=text):
                self.assertEqual(rating_signal(text), 1)
        for text in ('-', '−', '–', '—', '-1', '−1', 'минус', '👎', 'Бубус, минус'):
            with self.subTest(text=text):
                self.assertEqual(rating_signal(text), -1)
        for text in ('молодец', 'верно', '80 гривен', '-80 UAH', 'плюс 100', '-1+2', 'спасибо'):
            with self.subTest(text=text):
                self.assertEqual(rating_signal(text), 0)

    def test_plus_and_minus_change_bank_by_eighty_and_show_balance_without_model(self):
        answer = self.answer()
        with patch.object(self.bot, 'generate') as model:
            self.bot.handle(self.praise(answer))
            self.store.set('feedback_ack:-100', 0)
            self.bot.handle(self.praise(answer, text='−', user=8, number=101))
        self.assertEqual(self.store.salary(-100), {'balance': 0, 'plus': 1, 'minus': 1})
        self.assertIn('+80 ₴ · банк: 80 ₴', self.telegram.sent[0][1])
        self.assertIn('−80 ₴ · банк: 0 ₴', self.telegram.sent[1][1])
        self.assertEqual(self.memories()[0][2], 1)
        model.assert_not_called()

    def test_bank_can_go_negative(self):
        self.bot.handle(self.praise(self.answer(), text='-', number=100))
        self.assertEqual(self.store.salary(-100), {'balance': -80, 'plus': 0, 'minus': 1})
        self.assertIn('банк: -80 ₴', self.telegram.sent[0][1])
        self.assertEqual(self.memories(), [])

    def receipt(self, index=-1):
        chat, text, _ = self.telegram.sent[index]
        return {'chat': {'id': chat}, 'message_id': 10000 + index + 1 if index >= 0 else 10000 + len(self.telegram.sent),
                'date': time.time(), 'text': text, 'from': {'id': 99, 'is_bot': True}}

    def test_screenshot_minus_on_salary_receipt_debits_original_reply(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        receipt = self.receipt()
        self.store.set('feedback_ack:-100', 0)
        self.bot.handle(self.praise(receipt, text='-', user=8, number=101))
        self.assertEqual(self.store.salary(-100), {'balance': 0, 'plus': 1, 'minus': 1})
        self.assertIn('−80 ₴ · банк: 0 ₴', self.telegram.sent[-1][1])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT message_id FROM salary_votes WHERE direction=-1').fetchone()[0], 1)

    def test_plus_on_receipt_counts_but_same_person_cannot_rate_it_and_original_twice(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        receipt = self.receipt()
        self.store.set('feedback_ack:-100', 0)
        self.bot.handle(self.praise(receipt, user=8, number=101))
        latest = self.receipt()
        self.bot.handle(self.praise(answer, user=8, number=102))
        self.bot.handle(self.praise(latest, user=8, number=103))
        self.assertEqual(self.store.salary(-100), {'balance': 160, 'plus': 2, 'minus': 0})
        self.assertEqual(len(self.telegram.sent), 2)
        self.assertEqual(self.memories()[0][2], 2)

    def test_acknowledgement_chain_and_restart_share_one_original(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer, text='молодец'))
        receipt = self.receipt()
        self.bot.store = Store(self.path)
        self.store.set('feedback_ack:-100', 0)
        self.bot.handle(self.praise(receipt, text='-', user=8, number=101))
        latest = self.receipt()
        self.bot.store = Store(self.path)
        self.store.set('feedback_ack:-100', 0)
        self.bot.handle(self.praise(latest, text='+', user=9, number=102))
        self.assertEqual(self.store.salary(-100), {'balance': 0, 'plus': 1, 'minus': 1})
        with self.store.db() as db:
            self.assertEqual({row[0] for row in db.execute('SELECT answer_id FROM feedback_links')}, {1})

    def test_legacy_receipt_recovers_only_from_unique_confirmed_vote(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        receipt = self.receipt()
        with self.store.db() as db:
            db.execute('DELETE FROM feedback_links')
        self.store.set('feedback_ack:-100', 0)
        self.bot.handle(self.praise(receipt, text='-', user=8, number=101))
        self.assertEqual(self.store.salary(-100), {'balance': 0, 'plus': 1, 'minus': 1})

    def test_ambiguous_legacy_receipt_does_not_guess_or_adjust_existing_balance(self):
        self.bot.handle(self.praise(self.answer()))
        receipt = self.receipt()
        self.bot.handle(self.praise(self.answer(number=2), user=9, number=101))
        with self.store.db() as db:
            db.execute('DELETE FROM feedback_links')
        self.bot.handle(self.praise(receipt, text='-', user=8, number=102))
        self.assertEqual(self.store.salary(-100), {'balance': 160, 'plus': 2, 'minus': 0})
        self.assertIn('исходную реплику', self.telegram.sent[-1][1])

    def test_receipt_recognition_does_not_accept_arbitrary_reports_or_other_bot(self):
        for text in ['виртуальний банк 160 ₴', '+80 ₴ · банк: 160 ₴', 'поняв спасибо за оценку\n+100 ₴ · банк: 160 ₴']:
            self.assertEqual(salary_acknowledgement(text), 0)
        self.bot.handle(self.praise(self.answer()))
        receipt = self.receipt()
        receipt['from']['id'] = 88
        self.bot.handle(self.praise(receipt, text='-', user=8, number=101))
        self.assertEqual(self.store.salary(-100), {'balance': 80, 'plus': 1, 'minus': 0})

    def test_altered_quote_and_cross_group_cannot_use_saved_link(self):
        self.bot.handle(self.praise(self.answer()))
        receipt = self.receipt()
        for quoted, chat in [(dict(receipt, text='подмена'), -100), (receipt, -200)]:
            self.assertEqual(self.store.rate_reply(chat, quoted['message_id'], 8, quoted['text'], -1)[0], 'ignored')
        self.assertEqual(self.store.salary(-100)['balance'], 80)

    def test_many_people_ratings_during_ack_pause_still_count_both_signs(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        receipt = self.receipt()
        for user in range(8, 12):
            self.bot.handle(self.praise(receipt, text='+' if user % 2 else '-', user=user, number=100 + user))
        self.assertEqual(self.store.salary(-100), {'balance': 80, 'plus': 3, 'minus': 2})
        self.assertEqual(len(self.telegram.sent), 1)

    def test_concurrent_votes_on_original_and_receipt_cannot_double_pay(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        receipt = self.receipt()
        def vote(number):
            target = receipt if number % 2 else answer
            return self.store.rate_reply(-100, target['message_id'], 8, target['text'], -1)[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(vote, range(12)))
        self.assertEqual(results.count('rated'), 1)
        self.assertEqual(self.store.salary(-100), {'balance': 0, 'plus': 1, 'minus': 1})

    def test_same_person_has_only_one_salary_vote_on_each_answer_across_restarts(self):
        answer = self.answer()
        update = self.praise(answer)
        self.bot.handle(update)
        self.bot.handle(update)
        self.bot.store = Store(self.path)
        self.bot.handle(self.praise(answer, text='++', number=101))
        self.bot.handle(self.praise(answer, text='-', number=102))
        self.assertEqual(self.store.salary(-100), {'balance': 80, 'plus': 1, 'minus': 0})
        self.assertEqual(len(self.telegram.sent), 1)
        self.bot.handle(self.praise(self.answer(number=2), number=103))
        self.assertEqual(self.store.salary(-100)['balance'], 160)

    def test_words_of_praise_still_teach_without_paying_salary(self):
        answer = self.answer()
        self.bot.handle(self.praise(answer, text='молодец'))
        self.assertEqual(self.memories()[0][2], 1)
        self.assertEqual(self.store.salary(-100)['balance'], 0)
        self.bot.handle(self.praise(answer, number=101))
        self.assertEqual(self.store.salary(-100)['balance'], 80)
        self.assertEqual(self.memories()[0][2], 1)

    def test_bank_commands_are_public_local_reports_and_owner_private_uses_group_balance(self):
        self.bot.handle(self.praise(self.answer()))
        self.store.set('request:-100', 0)
        update = self.praise({}, text='/bank', number=101)
        del update['message']['reply_to_message']
        self.bot.handle(update)
        with patch.object(self.bot, 'generate') as model:
            self.bot.process_job(self.bot.utility_jobs.get_nowait())
        report = self.telegram.sent[-1][1]
        self.assertIn('<b>80 ₴</b>', report)
        self.assertIn('Плюсов: 1 · Минусов: 0', report)
        model.assert_not_called()
        self.store.set('request:42', 0)
        private = self.praise({}, text='/bank', user=42, chat=42, number=102)
        private['message']['chat']['type'] = 'private'
        del private['message']['reply_to_message']
        self.bot.handle(private)
        self.bot.process_job(self.bot.utility_jobs.get_nowait())
        self.assertEqual(self.telegram.sent[-1][0], 42)
        self.assertIn('<b>80 ₴</b>', self.telegram.sent[-1][1])

    def test_natural_salary_queries_and_plain_unaddressed_balance(self):
        for text in ('Володька, сколько заработал?', 'Бубус, зарплата', 'Володька баланс',
                     'Володька, сколько гривен в банке?', 'Бубус репутация'):
            with self.subTest(text=text):
                self.assertEqual(community_request(text), {'kind': 'bank'})
        self.assertIsNone(community_request('баланс'))
        self.assertEqual(community_request('/bank', '/bank'), {'kind': 'bank'})

    def test_only_confirmed_bot_answers_accept_votes_and_bank_reports_cannot_pay_themselves(self):
        answer = self.answer()
        for number, quoted in enumerate((dict(answer, message_id=404), dict(answer, text='подмена'),
                                        dict(answer, **{'from': {'id': 88}})), 100):
            self.bot.handle(self.praise(quoted, number=number))
        self.bot.handle(self.praise(answer, age=121, number=104))
        self.bot.handle(self.praise(answer, chat=-200, number=105))
        update = self.praise(answer, text='+', number=106)
        del update['message']['reply_to_message']
        self.bot.handle(update)
        self.assertEqual(self.store.salary(-100)['balance'], 0)
        report = self.answer('виртуальний банк 0 гривен', number=2, learnable=False)
        self.bot.handle(self.praise(report, number=107))
        self.assertEqual(self.store.salary(-100)['balance'], 0)

    def test_reference_answer_gets_salary_but_does_not_teach_changing_facts(self):
        message = {'chat': {'id': -100}, 'message_id': 1, 'date': time.time(),
                   'text': '1000 KZT ≈ 188 RUB', 'from': {'id': 99}}
        self.store.add_message(message, human=False, rateable=True)
        self.bot.handle(self.praise(message))
        self.assertEqual(self.store.salary(-100)['balance'], 80)
        self.assertEqual(self.memories(), [])

    def test_ack_failure_or_pause_does_not_lose_money_or_repeat_the_credit(self):
        self.telegram.fail = True
        answer = self.answer()
        self.bot.handle(self.praise(answer))
        self.bot.handle(self.praise(answer))
        self.telegram.fail = False
        self.bot.handle(self.praise(answer, user=8, number=102))
        self.assertEqual(self.store.salary(-100), {'balance': 160, 'plus': 2, 'minus': 0})
        self.assertEqual(len(self.telegram.sent), 1)

    def test_concurrent_repeated_votes_cannot_double_credit(self):
        answer = self.answer()
        def vote(_):
            return self.store.rate_reply(-100, 1, 7, answer['text'], 1)[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(vote, range(12)))
        self.assertEqual(statuses.count('rated'), 1)
        self.assertEqual(self.store.salary(-100), {'balance': 80, 'plus': 1, 'minus': 0})

    def test_balance_survives_vote_cleanup_and_old_history_cannot_be_paid_again(self):
        now = time.time()
        with self.store.db() as db:
            db.execute('INSERT INTO salary_accounts VALUES (?,?,?,?)', (-100, 80, 1, 0))
            db.execute('INSERT INTO salary_votes VALUES (?,?,?,?,?)', (-100, 1, 7, 1, now - 31 * 86400))
        old = self.answer(number=1)
        with self.store.db() as db:
            db.execute('UPDATE messages SET timestamp=? WHERE message_id=1', (now - 31 * 86400,))
        self.bot.handle(self.praise(self.answer(number=2), number=101))
        self.assertEqual(self.store.salary(-100)['balance'], 160)
        self.bot.handle(self.praise(old, number=102))
        self.assertEqual(self.store.salary(-100)['balance'], 160)
        self.assertEqual(Store(self.path).salary(-100)['balance'], 160)

    def test_existing_training_memory_does_not_create_retroactive_salary(self):
        answer = self.answer()
        self.store.approve_reply(-100, 1, 7, answer['text'])
        self.assertEqual(Store(self.path).salary(-100), {'balance': 0, 'plus': 0, 'minus': 0})
        self.assertEqual(self.memories()[0][2], 1)

    def test_owner_private_praise_does_not_create_hidden_salary_or_change_group_bank(self):
        answer = self.answer(chat=42)
        for number, text in enumerate(('+', '-'), 100):
            update = self.praise(answer, text=text, user=42, chat=42, number=number)
            update['message']['chat']['type'] = 'private'
            self.bot.handle(update)
        self.assertEqual(self.store.salary(42)['balance'], 0)
        self.assertEqual(self.store.salary(-100)['balance'], 0)


if __name__ == '__main__':
    unittest.main()
