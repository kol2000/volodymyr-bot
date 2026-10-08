import dataclasses
from datetime import date, datetime, timedelta, timezone
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import Bot, Store
from common import APIError, HTMLMessage, MODEL
from live_football import (ACTIVE, BASE, FINISHED, PREFIX, Football, FootballError,
                           MOSCOW, MONTHS, Match, Scoreboard, changes, fetch_html,
                           football_request, parse_page, scoreboard_text, status_code)
from test_bot import FakeTelegram


DAY = date(2026, 10, 9)
START = datetime(2026, 10, 9, 19, 30, tzinfo=MOSCOW).timestamp()


def card(identifier='444320', home='Ростов', away='Акрон', status='не начался', score='- : -', clock='19:30'):
    # The observed source renders --status-not-started even for finished matches.
    # Tests deliberately require the visible status, not that CSS modifier.
    return f'''<a class="se-matchcenter-matches__match se-matchcenter-matches__match--status-not-started"
                href="{BASE}/football/n/russia/premier/match-rostov-akron-{identifier}">
      <div class="se-matchcenter-matches__match-date">{clock}</div>
      <div class="se-matchcenter-matches__match-status">{status}</div>
      <div class="se-matchcenter-matches__match-team__name">{home}</div>
      <div class="se-matchcenter-matches__match-score">{score}&nbsp;</div>
      <div class="se-matchcenter-matches__match-team__name">{away}</div>
      <div class="se-matchcenter-matches__match-score__column"><p>99</p><p>88</p></div>
    </a>'''


def page(groups=None, day=DAY):
    if groups is None:
        groups = [('Чемпионат России. Премьер-лига', card())]
    return (f'<html><div class="se-matchcenter-sports-list">'
            f'<div class="se-matchcenter-sports-list__date">{day.day:02d} {MONTHS[day.month-1]} {day.year}</div>'
            + ''.join(f'<div class="se-competition-titled-block"><div class="se-competition-titled-block__title">'
                      f'<h2>{title}</h2></div>{cards}</div>' for title, cards in groups) + '</div></html>')


def fixture(status='scheduled', scores=(None, None), **kwargs):
    return Match('444320', 'РПЛ', 'Ростов', 'Акрон', START, status,
                 {'scheduled': 'не начался', 'live': "12'", 'break': 'перерыв',
                  'finished': 'завершен', 'penalties': 'пенальти', 'paused': 'отложен',
                  'unknown': 'неизвестно'}[status], *scores,
                 BASE + '/football/n/russia/premier/match-rostov-akron-444320', **kwargs)


class ParsingTests(unittest.TestCase):
    def test_preview_zero_is_not_a_live_score(self):
        match = parse_page(page(groups=[('Чемпионат России. Премьер-лига', card(score='0:0'))]), DAY)[0]
        self.assertEqual(match.status, 'scheduled')
        self.assertIsNone(match.home_score)
        rendered = scoreboard_text([match], DAY, START - 3600)
        self.assertIn('19:30 МСК', rendered)
        self.assertNotIn('Акрон · 0:0', rendered)
        self.assertIn('ещё не начался', rendered)

    def test_only_exact_supported_competitions_no_other_russian_teams(self):
        groups = [('Чемпионат России. Молодежная лига', card('1')),
                  ('Россия. Лига PARI (ФНЛ)', card('2')),
                  ('Чемпионат России. Премьер-лига', card('3')),
                  ('FONBET Кубок России', card('4')),
                  ('Кубок России. Женщины', card('5'))]
        matches = parse_page(page(groups), DAY)
        self.assertEqual([m.id for m in matches], ['3', '4'])
        self.assertEqual([m.competition for m in matches], ['РПЛ', 'Кубок России'])

    def test_score_comes_from_main_card_not_duplicate_column_or_ads(self):
        match = parse_page(page(groups=[('Кубок России', card(status='завершен', score='1 : 1 (пен. 4:3)'))]), DAY)[0]
        self.assertEqual(match.status, FINISHED)
        self.assertEqual((match.home_score, match.away_score, match.extra), (1, 1, '(пен. 4:3)'))

    def test_live_minute_compensated_time_periods_and_postponement(self):
        labels = {"12'": 'live', "90+3’": 'live', "45′": 'live', '2-й тайм': 'live',
                  '1 тайм': 'live', 'доп. тайм': 'live', '23 мин.': 'live', 'перерыв': 'break',
                  'пенальти': 'penalties', 'окончен': 'finished', 'отложен': 'paused',
                  'отменен': 'paused', 'прерван': 'paused', '???': 'unknown'}
        for label, expected in labels.items():
            with self.subTest(label=label):
                self.assertEqual(status_code(label), expected)

    def test_invalid_or_blocked_response_and_wrong_date_fail_closed(self):
        for html in ['<html>Вход через SberID</html>', '<html>captcha</html>', page(day=DAY - timedelta(days=1))]:
            with self.subTest(html=html):
                with self.assertRaises(FootballError):
                    parse_page(html, DAY)

    def test_valid_empty_day(self):
        self.assertEqual(parse_page(page([]), DAY), [])

    def test_bad_clock_missing_teams_malformed_live_score_are_rejected(self):
        for row in [card(clock='25:00'), card(clock=''), card(home=''),
                    card(status="12'", score='-:-'), card(status='завершен', score='error'),
                    card(score='99:0'), card(status='')]:
            with self.subTest(row=row):
                with self.assertRaises(FootballError):
                    parse_page(page([('Кубок России', row)]), DAY)

    def test_untrusted_match_urls_are_rejected(self):
        for url in ['http://127.0.0.1/', 'https://other.site/match-one-444320', BASE + '/football/n/russia/premier/match-one-444320?secret=x']:
            with self.subTest(url=url):
                html = page().replace(f'{BASE}/football/n/russia/premier/match-rostov-akron-444320', url)
                with self.assertRaises(FootballError):
                    parse_page(html, DAY)

    def test_conflicting_duplicates_fail_closed(self):
        with self.assertRaises(FootballError):
            parse_page(page([('Кубок России', card() + card(status="12'", score='1:0'))]), DAY)

    def test_football_html_escapes_team_names(self):
        match = dataclasses.replace(fixture('live', (1, 0)), home='<b>bad & name</b>')
        rendered = scoreboard_text([match], DAY, START)
        self.assertIn('&lt;b&gt;bad &amp; name&lt;/b&gt;', rendered)

    def test_requests_use_moscow_date_and_explicit_team_filter(self):
        now = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc).timestamp()
        for text, command, addressed in [('/football Ростов - Акрон', '/football', False),
                                         ('Володька, какой счет Ростов Акрон?', '', False),
                                         ('Володька, как играет Ростов?', '', False),
                                         ('счёт матча Ростов — Акрон', '', True)]:
            request = football_request(text, command, addressed, now=now)
            self.assertEqual(request['day'], '2026-10-09')
            self.assertIn('Ростов', request['filter'])
        self.assertEqual(football_request('/football завтра', '/football', now=now)['day'], '2026-10-10')
        self.assertEqual(football_request('/football вчера', '/football', now=now)['day'], '2026-10-08')
        self.assertEqual(football_request('/football 13.10 Кубок', '/football', now=now)['day'], '2026-10-13')

    def test_invalid_dates_and_unaddressed_currency_are_not_football(self):
        for value in ['/football 31.02', '/football сегодня завтра', '/football 09.10 10.10']:
            self.assertEqual(football_request(value, '/football')['kind'], 'football_invalid')
        for value in ['100 RUB', 'курс доллара', 'какой счет Ростов Акрон']:
            self.assertIsNone(football_request(value))
        self.assertIsNone(football_request('/weather Орёл', '/weather', True))

    def test_team_and_competition_filters(self):
        matches = [fixture(), dataclasses.replace(fixture(), id='2', competition='Кубок России', home='ЦСКА')]
        self.assertIn('Ростов', scoreboard_text(matches, DAY, START, 'Ростов Акрон'))
        self.assertNotIn('ЦСКА', scoreboard_text(matches, DAY, START, 'Ростов Акрон'))
        self.assertNotIn('Ростов —', scoreboard_text(matches, DAY, START, 'кубок'))
        self.assertIn('подходящих матчей нет', scoreboard_text(matches, DAY, START, 'Зенит'))

    def test_many_long_match_cards_fit_one_telegram_message(self):
        matches = [dataclasses.replace(fixture('finished', (1, 0)), id=str(number),
                   home='&' * 80, away='<' * 80, extra='x' * 80, label='z' * 80) for number in range(16)]
        rendered = scoreboard_text(matches, DAY, START)
        self.assertLess(len(rendered), 4096)
        self.assertIn('Ещё матчей:', rendered)


class SourceTests(unittest.TestCase):
    def test_cache_shares_requests_and_keeps_original_check_time(self):
        now = [1000]
        calls = []
        source = Scoreboard(fetch=lambda url: calls.append(url) or page(), clock=lambda: now[0])
        first = source.day(DAY)
        now[0] += 59
        self.assertEqual(source.day(DAY), first)
        self.assertEqual(len(calls), 1)
        now[0] += 1
        self.assertEqual(source.day(DAY)[1], now[0])
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].endswith('/09-10-2026/'))

    def test_failed_refresh_does_not_return_stale_score(self):
        now = [1000]
        source = Scoreboard(fetch=lambda url: page(), clock=lambda: now[0])
        source.day(DAY)
        now[0] += 61
        source.fetch = lambda url: '<html>blocked</html>'
        with self.assertRaises(FootballError):
            source.day(DAY)
        with self.assertRaises(FootballError):
            source.day(DAY)
        self.assertEqual(source.requests, 2)
        self.assertEqual(source.last_error, 'unexpected_page')

    def test_concurrent_manual_and_monitor_calls_share_fetch(self):
        calls = []
        source = Scoreboard(fetch=lambda url: calls.append(url) or page())
        threads = [threading.Thread(target=lambda: source.day(DAY)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(calls), 1)

    def test_http_errors_and_stale_response_are_fixed_reasons(self):
        import urllib.error
        from email.message import Message
        class Response:
            url = BASE + '/live/football/09-10-2026/'
            headers = Message()
            headers['Content-Type'] = 'text/html; charset=utf-8'
            headers['Age'] = '181'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return b'test'
        with patch('live_football.urllib.request.urlopen', return_value=Response()):
            with self.assertRaisesRegex(FootballError, 'stale_page'):
                fetch_html(Response.url)
        with patch('live_football.urllib.request.urlopen', side_effect=urllib.error.HTTPError('https://secret', 403, 'blocked', {}, None)):
            with self.assertRaisesRegex(FootballError, '^http_403$'):
                fetch_html(Response.url)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100)
        self.now = [START - 600]
        self.rows = [fixture()]
        self.source = Scoreboard(fetch=lambda url: page(), clock=lambda: self.now[0])
        self.source.day = lambda day: (self.rows, self.now[0])
        self.football = Football(self.store, self.source, clock=lambda: self.now[0])
        self.sent = []

    def tearDown(self):
        self.temp.cleanup()

    def enable(self):
        return self.football.answer({'kind': 'football_switch', 'enabled': True}, -100)

    def poll(self, rows=None, advance=91, fail=False):
        self.now[0] += advance
        if rows is not None:
            self.rows = rows
        def send(chat, message):
            self.sent.append((chat, message))
            if fail:
                raise APIError('uncertain')
        self.football.poll(send, lambda chat: chat == self.store.get('chat_id'))

    def test_disabled_poll_has_no_network(self):
        with patch.object(self.source, 'day') as fetch:
            self.poll()
            fetch.assert_not_called()

    def test_start_goal_half_time_final_each_once_and_unchanged_minutes_silent(self):
        self.enable()
        self.now[0] = START
        self.poll([fixture('live', (0, 0))])
        self.poll([fixture('live', (1, 0))])
        self.poll([dataclasses.replace(fixture('live', (1, 0)), label="43'")])
        self.poll([fixture('break', (1, 0))])
        self.poll([fixture('break', (1, 0))])
        self.poll([fixture('live', (1, 0))])
        self.poll([fixture('finished', (1, 0))])
        self.poll([fixture('finished', (1, 0))], advance=901)
        self.assertEqual(len(self.sent), 4)
        for title, (_, message) in zip(['Матч начался', 'Счет изменился', 'Перерыв', 'Матч завершён'], self.sent):
            self.assertIn(title, message)
            self.assertIsInstance(message, HTMLMessage)

    def test_correction_decreases_score_without_claiming_another_goal(self):
        self.enable()
        self.now[0] = START
        self.poll([fixture('live', (1, 0))])
        self.poll([fixture('live', (0, 0))])
        self.assertIn('Счет изменился', self.sent[-1][1])
        self.assertNotIn('Гол!', self.sent[-1][1])

    def test_uncertain_send_is_reserved_and_not_replayed_after_restart(self):
        self.enable()
        self.now[0] = START
        with self.assertRaises(APIError):
            self.poll([fixture('live', (1, 0))], fail=True)
        self.football = Football(Store(Path(self.temp.name) / 'bot.sqlite3'), self.source, clock=lambda: self.now[0])
        self.poll([fixture('live', (1, 0))])
        self.assertEqual(len(self.sent), 1)

    def test_baseline_does_not_announce_finished_matches_or_past_goals(self):
        self.rows = [fixture('finished', (3, 2))]
        self.enable()
        self.poll(advance=901)
        self.assertEqual(self.sent, [])
        self.rows = [fixture('live', (2, 1))]
        self.football.answer({'kind': 'football_switch', 'enabled': False}, -100)
        self.enable()
        self.poll(advance=901)
        self.assertEqual(self.sent, [])

    def test_first_seen_live_is_reported_as_current_score_not_new_goal(self):
        self.store.set(PREFIX + 'enabled', True)
        self.now[0] = START + 1800
        self.poll([fixture('live', (2, 1))])
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Матч сейчас идёт', self.sent[0][1])

    def test_score_and_finish_in_same_poll_are_one_message(self):
        self.store.set(PREFIX + 'enabled', True)
        self.store.set(PREFIX + 'seen:-100', {'444320': fixture('live', (0, 0)).state()})
        self.now[0] = START + 7200
        self.poll([fixture('finished', (0, 1))])
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Счет изменился', self.sent[0][1])
        self.assertIn('Матч завершён', self.sent[0][1])

    def test_unknown_status_and_regression_from_final_do_not_send(self):
        self.enable()
        self.now[0] = START + 7200
        self.poll([fixture('unknown', (2, 1))])
        self.assertEqual(self.sent, [])
        self.poll([fixture('finished', (2, 1))])
        self.poll([fixture('live', (2, 1))], advance=901)
        self.assertEqual(len(self.sent), 1)

    def test_disable_immediately_stops_polling(self):
        self.enable()
        self.football.answer({'kind': 'football_switch', 'enabled': False}, -100)
        with patch.object(self.source, 'day') as fetch:
            self.poll([fixture('live', (1, 0))])
            fetch.assert_not_called()

    def test_network_error_keeps_state_silent_and_backs_off(self):
        self.enable()
        before = self.store.get(PREFIX + 'seen:-100')
        with patch.object(self.source, 'day', side_effect=FootballError('network_error')) as fetch:
            self.poll([fixture('live', (1, 0))])
            self.assertEqual(self.store.get(PREFIX + 'seen:-100'), before)
            self.poll()
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.sent, [])
        self.assertIn('network_error', self.football.answer({'kind': 'football_status'}, -100))

    def test_older_concurrent_snapshot_does_not_roll_score_back(self):
        self.enable()
        self.now[0] = START + 1800
        self.store.set(PREFIX + 'seen:-100', {'444320': dict(fixture('live', (1, 1)).state(), checked=self.now[0])})
        with patch.object(self.source, 'day', return_value=([fixture('live', (1, 0))], self.now[0] - 60)):
            self.poll()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(PREFIX + 'seen:-100')['444320']['score'], [1, 1])

    def test_late_match_is_checked_on_original_date_after_midnight(self):
        self.store.set(PREFIX + 'enabled', True)
        late = dataclasses.replace(fixture('live', (0, 0)), kickoff=START + 4 * 3600)
        self.store.set(PREFIX + 'seen:-100', {late.id: late.state()})
        self.now[0] = datetime(2026, 10, 10, 0, 30, tzinfo=MOSCOW).timestamp()
        def fetch(day):
            return ([dataclasses.replace(late, status='finished', label='завершен')] if day == DAY else [], self.now[0])
        with patch.object(self.source, 'day', side_effect=fetch) as check:
            self.poll()
            self.assertEqual([call.args[0] for call in check.call_args_list], [DAY, DAY + timedelta(days=1)])
        self.assertEqual(len(self.sent), 1)

    def test_multiple_simultaneous_matches_have_independent_persistent_state(self):
        second = dataclasses.replace(fixture(), id='1234', competition='Кубок России', home='ЦСКА')
        self.rows = [fixture(), second]
        self.enable()
        self.now[0] = START
        live_second = dataclasses.replace(second, status='live', label="12'", home_score=0, away_score=0)
        self.poll([fixture('live', (0, 0)), live_second])
        self.poll([fixture('live', (1, 0)), dataclasses.replace(live_second, away_score=1)])
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(set(self.store.get(PREFIX + 'seen:-100')), {'444320', '1234'})

    def test_paused_and_penalty_transitions(self):
        self.enable()
        self.now[0] = START
        self.poll([fixture('paused')])
        self.poll([fixture('live', (1, 1))])
        self.poll([fixture('penalties', (1, 1))])
        self.poll([fixture('penalties', (1, 1), extra='пен. 4:3')])
        self.poll([fixture('finished', (1, 1), extra='пен. 4:3')])
        self.assertEqual(len(self.sent), 5)
        self.assertIn('Серия пенальти', self.sent[2][1])
        self.assertIn('4:3', self.sent[-1][1])

    def test_rebinding_during_fetch_does_not_send_to_old_group(self):
        self.enable()
        def fetch(day):
            self.store.set('chat_id', -200)
            return [fixture('live', (1, 0))], self.now[0]
        with patch.object(self.source, 'day', side_effect=fetch):
            self.poll()
        self.assertEqual(self.sent, [])

    def test_telegram_retry_after_does_not_consume_unsent_event(self):
        self.enable()
        before = self.store.get(PREFIX + 'seen:-100')
        self.store.set('blocked_until', self.now[0] + 1000)
        self.poll([fixture('live', (1, 0))])
        self.assertEqual(self.store.get(PREFIX + 'seen:-100'), before)


class BotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100)
        self.telegram = FakeTelegram()
        self.bot = Bot({'owner_id': 42, 'model': MODEL, 'ollama_url': 'http://localhost'},
                       self.telegram, self.store)
        self.bot.username, self.bot.bot_id = 'parody_bot', 99

    def tearDown(self):
        self.temp.cleanup()

    def update(self, message, user=42, chat=-100):
        import time
        return {'message': {'chat': {'id': chat, 'type': 'supergroup'},
                            'from': {'id': user, 'is_bot': False, 'first_name': 'Tester'},
                            'date': time.time(), 'message_id': 123, 'text': message}}

    def test_football_command_and_natural_request_use_utility_without_model(self):
        for message in ['/football Ростов', 'Володька, какой счет Ростов Акрон?']:
            with self.subTest(message=message):
                self.store.set('request:-100', 0)
                with self.store.db() as db:
                    db.execute('DELETE FROM messages')
                    db.execute('DELETE FROM sends')
                self.bot.handle(self.update(message, user=7))
                job = self.bot.utility_jobs.get_nowait()
                self.assertEqual(job['utility']['kind'], 'football')
                with patch.object(self.bot.football.source, 'day', return_value=([fixture()], START - 3600)), patch('bot.http_json') as model:
                    self.bot.process_job(job)
                    model.assert_not_called()
                self.assertIn('19:30 МСК', self.telegram.sent[-1][1])
                self.assertFalse(self.store.get(PREFIX + 'enabled', False))

    def test_non_owner_cannot_enable_and_wrong_group_ignored(self):
        self.bot.handle(self.update('/football_on', user=7))
        job = self.bot.utility_jobs.get_nowait()
        self.assertEqual(job['utility']['kind'], 'fixed')
        self.assertFalse(self.store.get(PREFIX + 'enabled', False))
        self.bot.handle(self.update('/football_on', chat=-200))
        self.assertTrue(self.bot.utility_jobs.empty())

    def test_unavailable_source_returns_explicit_error_without_llm_or_parody(self):
        self.bot.handle(self.update('/football'))
        job = self.bot.utility_jobs.get_nowait()
        with patch.object(self.bot.football.source, 'day', side_effect=FootballError('http_403')), patch('bot.http_json') as model, patch.object(self.bot, 'fallback_answer') as fallback:
            self.bot.process_job(job)
            model.assert_not_called()
            fallback.assert_not_called()
        self.assertIn('счет сечас не подтверждён', self.telegram.sent[-1][1])
        self.assertEqual(self.store.get(PREFIX + 'error'), 'http_403')

    def test_source_failure_does_not_enable_notifications(self):
        self.bot.handle(self.update('/football_on'))
        job = self.bot.utility_jobs.get_nowait()
        with patch.object(self.bot.football.source, 'day', side_effect=FootballError('network_error')):
            self.bot.process_job(job)
        self.assertFalse(self.store.get(PREFIX + 'enabled', False))

    def test_uncertain_notification_is_not_retried_and_not_trainable(self):
        self.telegram.fail = True
        self.bot.send_football(-100, HTMLMessage('<b>Счет 1:0</b>'))
        self.assertEqual(len(self.telegram.sent), 1)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE human=0').fetchone()[0], 0)
        self.telegram.fail = False
        self.bot.send_football(-100, HTMLMessage('<b>Счет 1:0</b>'))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT learnable,rateable FROM messages WHERE human=0').fetchone(), (0, 0))


if __name__ == '__main__':
    unittest.main()
