import copy
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bot import Bot, Store
from common import APIError, HTMLMessage, MODEL
from football import (Football, FootballError, LIMIT, MOSCOW, error_reply, football_request,
                      match_line, provider_get)
from setup_football import configure, save_key
import test_bot as bot_tests


NOW = datetime(2026, 10, 8, 14, tzinfo=timezone.utc).timestamp()


def leagues():
    result = []
    for number, title, kind in ((235, 'Premier League', 'League'), (237, 'Cup', 'Cup'),
                                (236, 'First League', 'League'), (238, 'Super Cup', 'Cup')):
        result.append({'league': {'id': number, 'name': title, 'type': kind}, 'country': {'name': 'Russia'},
                       'seasons': [{'year': 2026, 'current': True, 'start': '2026-07-01', 'end': '2027-06-30'}]})
    return result


def fixture(number=1, league=235, status='NS', goals=None, kickoff=NOW + 60, penalties=None):
    return {'league': {'id': league}, 'fixture': {'id': number, 'timestamp': kickoff,
            'status': {'short': status, 'elapsed': 37 if status == '1H' else None}},
            'teams': {'home': {'name': 'Zenit Saint Petersburg'}, 'away': {'name': 'Spartak Moscow'}},
            'goals': {'home': goals[0] if goals is not None else None, 'away': goals[1] if goals is not None else None},
            'score': {'penalty': {'home': penalties[0] if penalties else None, 'away': penalties[1] if penalties else None}}}


class FootballTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db.sqlite3')
        self.now = NOW
        self.calls = []
        self.raw = [fixture(), fixture(number=2, league=237, kickoff=NOW + 300)]
        self.headers = {}
        self.football = Football({'football_api_key': 'test-key-only'}, self.store, self.transport, lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, key, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == 'leagues':
            rows = leagues()
        elif 'ids' in params:
            ids = set(map(int, params['ids'].split('-')))
            rows = [r for r in self.raw if r['fixture']['id'] in ids]
        else:
            rows = [r for r in self.raw if r['league']['id'] == params['league']]
        return {'errors': [], 'response': copy.deepcopy(rows), 'paging': {'total': 1}}, self.headers

    def due(self, advance=300):
        self.now += advance
        self.store.set('football_next', 0)

    def test_catalog_filters_only_two_competitions_and_uses_current_season(self):
        cache = self.football.schedule()
        self.assertEqual({r['competition'] for r in cache['rows']}, {'РПЛ', 'Кубок России'})
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[1][1]['season'], 2026)
        self.assertEqual(self.calls[1][1]['timezone'], 'Europe/Moscow')
        self.football.schedule()
        self.assertEqual(len(self.calls), 3)

    def test_missing_current_seasons_never_fall_back_to_old_data(self):
        self.football.transport = lambda *_: ({'response': [], 'errors': []}, {})
        with self.assertRaisesRegex(FootballError, '^competitions_missing$'):
            self.football.schedule()
        self.assertEqual(self.football.remaining(), 99)
        with self.assertRaises(FootballError):
            self.football.schedule()
        self.assertEqual(self.football.remaining(), 99)

    def test_plan_error_blocks_calls_until_daily_reset_and_preserves_budget(self):
        self.football.transport = lambda *_: ({'response': [], 'errors': {'plan': 'Season unavailable'}}, {})
        with self.assertRaisesRegex(FootballError, '^season_access$'):
            self.football.schedule()
        with self.assertRaises(FootballError):
            self.football.schedule()
        self.assertEqual(self.football.remaining(), 99)
        self.assertNotIn('Season unavailable', error_reply('season_access'))

    def test_key_missing_never_calls_provider(self):
        self.football.key = ''
        with self.assertRaisesRegex(FootballError, '^key_missing$'):
            self.football.answer({})
        self.assertEqual(self.calls, [])

    def test_no_live_polls_between_match_windows(self):
        self.raw = [fixture(kickoff=self.now + 3600)]
        self.football.tick(-100)
        self.assertEqual(len(self.calls), 3)
        self.due(120)
        self.football.tick(-100)
        self.assertEqual(len(self.calls), 3)

    def test_goals_corrections_final_and_penalties_are_deduplicated(self):
        self.football.tick(-100)
        self.raw[0] = fixture(status='1H', goals=[1, 0])
        self.due()
        events = self.football.tick(-100)
        self.assertEqual(len(events), 1)
        self.assertIn('Счёт изменился', events[0])
        self.assertIn('1 : 0', events[0])
        self.due()
        self.assertEqual(self.football.tick(-100), [])
        self.raw[0] = fixture(status='1H', goals=[0, 0])
        self.due()
        self.assertIn('Коррекция', self.football.tick(-100)[0])
        self.raw[0] = fixture(status='PEN', goals=[1, 1], penalties=[4, 3])
        self.due()
        events = self.football.tick(-100)
        self.assertEqual(len(events), 1)
        self.assertIn('Матч окончен', events[0])
        self.assertIn('пенальти 4 : 3', events[0])
        self.due()
        self.assertEqual(self.football.tick(-100), [])

    def test_startup_during_game_and_restart_never_replay_old_goals(self):
        self.raw[0] = fixture(status='1H', goals=[2, 1], kickoff=NOW - 1800)
        self.assertEqual(self.football.tick(-100), [])
        self.due()
        restarted = Football({'football_api_key': 'test-key-only'}, self.store, self.transport, lambda: self.now)
        self.assertEqual(restarted.tick(-100), [])
        self.raw[0] = fixture(status='2H', goals=[3, 1], kickoff=NOW - 1800)
        self.due()
        self.assertEqual(len(restarted.tick(-100)), 1)
        self.due()
        self.assertEqual(restarted.tick(-200), [])  # new chat needs its own baseline

    def test_manual_queries_do_not_consume_notification_events(self):
        self.football.tick(-100)
        self.raw[0] = fixture(status='1H', goals=[1, 0])
        self.due()
        self.assertIn('1 : 0', self.football.answer({}))
        self.assertEqual(len(self.football.tick(-100)), 1)

    def test_fresh_requests_share_cache_and_show_honest_timestamp(self):
        answer = self.football.answer({'competition': 'РПЛ', 'team': ('zenit',)})
        self.assertIsInstance(answer, HTMLMessage)
        self.assertIn('Зенит', answer)
        self.assertIn('API-Football', answer)
        self.assertIn('17:00 МСК', answer)
        self.assertNotIn('<b>Кубок России</b>', answer)
        count = len(self.calls)
        self.football.answer({})
        self.assertEqual(len(self.calls), count)

    def test_bulk_refresh_at_most_twenty_ids_and_drops_other_leagues(self):
        self.raw = [fixture(number=n, league=235, kickoff=NOW + 100) for n in range(1, 26)]
        self.raw.append(fixture(number=99, league=236))
        cache = self.football.schedule()
        self.due(160)
        self.football.refresh(cache)
        sizes = [len(params['ids'].split('-')) for endpoint, params in self.calls if 'ids' in params]
        self.assertEqual(sizes, [20, 5])
        self.assertNotIn(99, [r['id'] for r in cache['rows']])

    def test_incomplete_refresh_is_not_reported_as_no_matches(self):
        cache = self.football.schedule()
        self.raw = []
        self.due(300)
        with self.assertRaisesRegex(FootballError, '^incomplete$'):
            self.football.refresh(cache)

    def test_daily_hard_limit_and_ten_request_reserve_survive_new_client(self):
        self.store.set('football_quota', {'day': '2026-10-08', 'used': 90, 'remaining': 10, 'calls': []})
        with self.assertRaisesRegex(FootballError, '^reserve$'):
            self.football._request('leagues', {}, automatic=True)
        for _ in range(10):
            self.football._request('leagues', {})
            self.now += 61
        restarted = Football({'football_api_key': 'test-key-only'}, self.store, self.transport, lambda: self.now)
        with self.assertRaisesRegex(FootballError, '^quota$'):
            restarted._request('leagues', {})
        self.assertEqual(len(self.calls), 10)
        self.now = datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp()
        restarted._request('leagues', {})
        self.assertEqual(restarted.remaining(), 99)

    def test_header_limit_can_reduce_budget_but_never_increase_it(self):
        self.headers = {'x-ratelimit-requests-remaining': '3'}
        self.football._request('leagues', {})
        self.assertEqual(self.football.remaining(), 3)
        self.headers = {'x-ratelimit-requests-remaining': '5000'}
        self.football._request('leagues', {})
        self.assertEqual(self.football.remaining(), 2)

    def test_failed_request_counted_and_not_retried_or_logged_with_secret(self):
        def fail(*_):
            raise FootballError('unavailable')
        self.football.transport = fail
        for _ in range(2):
            with self.assertRaisesRegex(FootballError, '^unavailable$'):
                self.football._request('leagues', {})
        self.assertEqual(self.football.remaining(), 99)

    def test_minute_limit_and_backoff_do_not_issue_extra_requests(self):
        for _ in range(8):
            self.football._request('leagues', {})
        with self.assertRaisesRegex(FootballError, '^rate_limit$'):
            self.football._request('leagues', {})
        self.assertEqual(len(self.calls), 8)
        self.now += 61
        self.football._request('leagues', {})
        self.assertEqual(len(self.calls), 9)

    def test_adaptive_interval_budget_for_long_days_and_cup_batches(self):
        rows = self.football.schedule()['rows']
        self.assertGreaterEqual(self.football.interval(rows), 120)
        rows += [dict(rows[0], id=100, kickoff=NOW + 10 * 3600)]
        self.assertGreater(self.football.interval(rows), 300)
        self.store.set('football_quota', {'day': '2026-10-08', 'used': 89, 'remaining': 11, 'calls': []})
        self.assertGreater(self.football.interval(rows), 3600)

    def test_configuration_saves_secret_locally_preserving_model_and_token(self):
        path = Path(self.temp.name) / 'config.json'
        config = {'token': 'test-telegram-token', 'owner_id': 42, 'model': MODEL}
        path.write_text(json.dumps(config))
        remaining = configure('test-key-only', config, self.store, path, self.transport)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['token'], config['token'])
        self.assertEqual(saved['model'], MODEL)
        self.assertEqual(saved['football_api_key'], 'test-key-only')
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(remaining, 97)
        self.assertTrue(self.store.get('football_enabled'))

    def test_configuration_rejected_season_does_not_replace_old_key(self):
        path = Path(self.temp.name) / 'config.json'
        original = json.dumps({'football_api_key': 'old-test-key'})
        path.write_text(original)
        def denied(*_):
            return {'response': [], 'errors': {'plan': 'Season unavailable'}}, {}
        with self.assertRaises(FootballError):
            configure('new-test-key', {}, self.store, path, denied)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(self.store.get('football_enabled', False))

    def test_names_are_html_escaped(self):
        row = self.football._fixtures([fixture(status='1H', goals=[0, 0])], self.football.leagues())[0]
        row['home'] = '<b>evil</b>'
        self.assertIn('&lt;b&gt;', match_line(row))

    def test_natural_queries_and_commands_with_russian_team_cases(self):
        for text in ('Володька, как играет Зенит?', 'Бубус, какой счёт у Спартака?',
                     'Володька, что там футбол?', 'Володька, матчи РПЛ сегодня'):
            with self.subTest(text=text):
                self.assertEqual(football_request(text)['kind'], 'football')
        self.assertEqual(football_request('/football Кубок', '/football')['competition'], 'Кубок России')
        self.assertEqual(football_request('/football', '/football')['kind'], 'football')
        self.assertIsNone(football_request('сегодня Зенит играет плохо'))
        self.assertIsNone(football_request('Володька, какая погода в Орле?'))
        self.assertIsNone(football_request('/say футбол', '/say'))
        self.assertEqual(football_request('/football завтра', '/football')['kind'], 'fixed')

    def test_quota_claims_are_atomic_across_concurrent_clients(self):
        self.store.set('football_quota', {'day': '2026-10-08', 'used': 95, 'remaining': 5, 'calls': []})
        def call(_):
            client = Football({'football_api_key': 'test-key-only'}, self.store, self.transport, lambda: self.now)
            try:
                client._request('leagues', {})
                return True
            except FootballError:
                return False
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(call, range(12)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.football.remaining(), 0)

    def test_cross_midnight_fixture_still_gets_final_notification(self):
        self.now = datetime(2026, 10, 8, 20, 55, tzinfo=timezone.utc).timestamp()
        kickoff = self.now - 7200
        self.raw = [fixture(status='2H', goals=[1, 0], kickoff=kickoff)]
        self.football.tick(-100)
        self.raw = [fixture(status='FT', goals=[2, 0], kickoff=kickoff)]
        self.due(600)  # Moscow calendar day changed; the API day hasn't.
        events = self.football.tick(-100)
        self.assertEqual(len(events), 1)
        self.assertIn('Матч окончен', events[0])
        self.assertIn('2 : 0', events[0])
        self.assertEqual(self.store.get('football_schedule')['day'], '2026-10-09')

    def test_large_cup_day_fits_telegram_limit_keeps_scores_and_timestamp(self):
        self.raw = [fixture(number=n, league=237, status='NS', kickoff=NOW + 3600) for n in range(1, 201)]
        answer = self.football.answer({})
        self.assertLess(len(answer), 4096)
        self.assertIn('Зенит', answer)
        self.assertIn('Ещо матчей:', answer)
        self.assertIn('проверено', answer)

    def test_provider_http_error_is_safe_and_auth_is_header_only(self):
        import urllib.error
        caught = []
        def denied(request, timeout):
            caught.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, 'secret-provider-error', {}, None)
        with patch('football.urllib.request.urlopen', side_effect=denied):
            with self.assertRaisesRegex(FootballError, '^key_invalid$'):
                provider_get('test-secret', 'leagues', {'country': 'Russia'})
        self.assertNotIn('test-secret', caught[0].full_url)
        self.assertEqual(caught[0].get_header('X-apisports-key'), 'test-secret')

    def test_malformed_body_and_incomplete_pages_never_return_invented_scores(self):
        for data in ({'response': None}, {'response': [], 'paging': {'total': 2}}):
            self.store.set('football_pause', {})
            self.football.transport = lambda *_, data=data: (data, {})
            with self.assertRaises(FootballError):
                self.football.answer({})



class FootballBotTests(unittest.TestCase):
    setUp = bot_tests.BotTests.setUp
    tearDown = bot_tests.BotTests.tearDown
    update = bot_tests.BotTests.update
    bind = bot_tests.BotTests.bind

    def test_football_bypasses_model_queue_and_missing_key_returns_clear_response(self):
        self.bind()
        self.bot.handle(self.update('/football', user=7, number=2))
        self.assertEqual(self.bot.jobs.qsize(), 0)
        job = self.bot.utility_jobs.get_nowait()
        with patch.object(self.bot, 'generate', side_effect=AssertionError('LLM must not provide scores')):
            self.bot.process_job(job)
        self.assertIn('не настроен', self.telegram.sent[-1][1])

    def test_control_owner_only_separate_from_auto_and_cooldowns(self):
        self.bind()
        self.bot.football.key = 'test-key-only'
        self.store.set('automatic', True)
        self.bot.handle(self.update('/football_off', user=7, number=2))
        self.assertIsNone(self.store.get('football_enabled'))
        self.bot.handle(self.update('/football_off', number=3))
        self.assertFalse(self.store.get('football_enabled'))
        self.assertTrue(self.store.get('automatic'))
        self.bot.handle(self.update('/football_on', number=4))
        self.assertTrue(self.store.get('football_enabled'))
        self.assertEqual(self.bot.utility_jobs.qsize(), 0)

    def test_auto_notifications_independent_of_model_five_reply_limit(self):
        self.bind()
        self.store.set('football_enabled', True)
        self.store.set('automatic', False)
        with patch.object(self.bot.football, 'tick', return_value=[HTMLMessage('<b>1 : 0</b>')]) as tick:
            self.bot.football_tick()
        tick.assert_called_once_with(-100)
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.store.counts().get('automatic', 0), 0)
        self.store.set('football_enabled', False)
        with patch.object(self.bot.football, 'tick') as tick:
            self.bot.football_tick()
        tick.assert_not_called()

    def test_disable_or_rebind_during_fetch_suppresses_send(self):
        self.bind()
        self.store.set('football_enabled', True)
        def rebind(chat):
            self.store.set('chat_id', -200)
            return [HTMLMessage('1 : 0')]
        with patch.object(self.bot.football, 'tick', side_effect=rebind):
            self.bot.football_tick()
        self.assertEqual(self.telegram.sent, [])

    def test_uncertain_telegram_send_not_retried(self):
        self.bind()
        self.store.set('football_enabled', True)
        self.telegram.fail = True
        with patch.object(self.bot.football, 'tick', return_value=[HTMLMessage('1 : 0'), HTMLMessage('2 : 0')]):
            self.bot.football_tick()
        self.assertEqual(len(self.telegram.sent), 1)
