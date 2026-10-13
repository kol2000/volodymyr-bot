import dataclasses
import tempfile
import unittest
from datetime import date, datetime
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from bot import Store
from live_football import Football, FootballError, MOSCOW, PREFIX, event_text, goals_between
from kulichki_football import (BASE, NAME, KulichkiScoreboard, fetch_html, parse_home,
                               parse_broadcast)

DAY = date(2026, 10, 13)
START = datetime(2026, 10, 13, 18, 30, tzinfo=MOSCOW).timestamp()
URL = BASE + '/trans/2026/25707-Lokomotiv-Akron-online-translyaciyu-matcha-smotret.htm'


def home(rows=None):
    rows = rows if rows is not None else [('25707', 'Локомотив', 'Акрон', '18:30')]
    table = '<table><tr><th>Онлайн трансляции.</th></tr><tr><th>Кубок России. Путь РПЛ.</th></tr>'
    for identifier, a, b, clock in rows:
        table += (f'<tr><td>{clock}. <a href="/trans/2026/{identifier}-Lokomotiv-Akron-online-translyaciyu-matcha-smotret.htm">'
                  f'{a} - {b} - 0:0</a></td><td><a href="/trans/2026/{identifier}-Lokomotiv-Akron-online-translyaciyu-matcha-smotret.htm">Онлайн</a></td></tr>')
    return table + '</table><a href="/trans/2026/999-other.htm">Спартак Кс - Челябинск - 1:0</a>'


def broadcast(status='scheduled', score='0:0', day=13, a='Локомотив', b='Акрон', goal=False):
    title = f'<title>Онлайн трансляция матча {a} - {b}: {day} октября 2026, Кубок России на ФНК</title>'
    board = f'<table><tr><td>{a} - {b}<font size="6">{score}</font></td></tr></table>'
    summary = ('<table><tr><td><img src="/trans/goal.gif"><a href="/players/10.htm">Игрок</a>, '
               '45+2 (1:0)</td></tr></table><a href="/players/10.htm">Иван Игрок</a>') if goal else ''
    marker = {'scheduled': '', 'live': '1t.gif', 'second': '2t.gif', 'break': 'end1.gif', 'finished': 'end2.gif'}[status]
    rows = ''
    if marker:
        rows = f'<tr><td>90</td><td><img src="/trans/{marker}"></td><td>Статус игры.</td></tr>'
    if goal:
        rows += '<tr><td>+2</td><td><img src="/trans/GOOal.gif"></td><td>ГОООЛ! Игрок! 1:0! Пробил со штрафного в угол.</td></tr>'
    return title + board + summary + '<table id="table15">' + rows + '<tr><td>-</td><td></td><td>Анонс.</td></tr></table>'


class ParsingTests(unittest.TestCase):
    def test_only_homepage_editorial_table_not_news_or_all_cup_games(self):
        rows = parse_home(home())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'fnk:2026:25707')
        self.assertNotIn('Челябинск', str(rows))

    def test_european_competitions_are_included_when_selected(self):
        html = home([('25707', 'Арсенал', 'Лилль', '22:00')]).replace('Кубок России. Путь РПЛ.', 'Лига чемпионов.')
        rows = parse_home(html)
        self.assertEqual(rows[0]['competition'], 'Лига чемпионов.')

    def test_preview_zero_never_becomes_live_by_elapsed_clock(self):
        selected = parse_home(home())[0]
        match = parse_broadcast(broadcast(), selected)
        self.assertEqual((match.status, match.home_score, match.away_score), ('scheduled', None, None))
        self.assertEqual(match.kickoff, START)

    def test_clock_period_break_and_final_come_from_broadcast_markers(self):
        for status, expected in [('live', 'live'), ('second', 'live'), ('break', 'break'), ('finished', 'finished')]:
            with self.subTest(status=status):
                match = parse_broadcast(broadcast(status), parse_home(home())[0])
                self.assertEqual(match.status, expected)

    def test_goal_uses_same_score_header_player_and_added_time_not_prose_guess(self):
        match = parse_broadcast(broadcast('finished', '1:0', goal=True), parse_home(home())[0])
        goals = goals_between(match, [0, 0])
        self.assertEqual(len(goals), 1)
        self.assertEqual((goals[0].minute, goals[0].player, goals[0].score), ('45+2′', 'Иван Игрок', (1, 0)))
        self.assertIn('Пробил со штрафного', goals[0].description)
        self.assertIn(NAME, event_text(match, ('score', 'Счет изменился'), START, goals))
        self.assertNotIn('Спорт-Экспресс', event_text(match, ('score', 'Счет изменился'), START, goals))

    def test_missing_description_does_not_attach_another_goal(self):
        html = broadcast('live', '1:0', goal=True).replace('Игрок! 1:0!', 'Игрок! 2:0!')
        match = parse_broadcast(html, parse_home(home())[0])
        self.assertEqual(match.goals, ())

    def test_own_goal_and_penalty_qualifier_after_minute(self):
        for qualifier, kind in [('автогол', 'own-goal'), ('пенальти', 'penalty-goal')]:
            html = broadcast('live', '1:0', goal=True).replace('45+2 (1:0)', f'45+2 - {qualifier} (1:0)')
            html = html.replace('GOOal.gif', 'owngoal.gif' if kind == 'own-goal' else 'pen.gif')
            match = parse_broadcast(html, parse_home(home())[0])
            self.assertEqual(match.goals[0].kind, kind)

    def test_same_scorer_multiple_goals_does_not_duplicate_header_events(self):
        html = broadcast('live', '2:0', goal=True).replace('45+2 (1:0)',
             '45+2 (1:0); <a href="/players/10.htm">Игрок</a>, 80 (2:0)')
        html = html.replace('<tr><td>-</td>', '<tr><td>80</td><td><img src="/trans/GOOal.gif"></td>'
                            '<td>ГОООЛ! Игрок! 2:0! Удар в угол.</td></tr><tr><td>-</td>')
        match = parse_broadcast(html, parse_home(home())[0])
        self.assertEqual(len(match.goals), 2)
        self.assertEqual(len(goals_between(match, [0, 0])), 2)

    def test_wrong_match_title_and_missing_score_fail_closed(self):
        selected = parse_home(home())[0]
        for html in [broadcast(a='Спартак'), broadcast().replace('size="6"', 'size="4"'),
                     broadcast().replace('0:0', '99:0'), '<html>captcha</html>']:
            with self.subTest(html=html):
                with self.assertRaises(FootballError):
                    parse_broadcast(html, selected)

    def test_daily_selection_block_must_exist_and_urls_stay_on_fnk(self):
        for html in ['<html>captcha</html>', home().replace('/trans/2026/25707-', 'https://other.example/trans/2026/25707-'),
                     home().replace('18:30.', '25:30.')]:
            with self.assertRaises(FootballError):
                parse_home(html)
        self.assertEqual(parse_home(home([])), [])


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.now = [START - 900]
        self.html = home()
        self.pages = {'25707': broadcast()}
        self.calls = []
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, url):
        self.calls.append(url)
        if '/trans/' not in url:
            return self.html
        if '/999-' in url:
            self.fail('Unselected match was fetched')
        identifier = url.split('/2026/', 1)[1].split('-', 1)[0]
        value = self.pages[identifier]
        if isinstance(value, Exception):
            raise value
        return value

    def source(self):
        return KulichkiScoreboard(self.store, fetch=self.fetch, clock=lambda: self.now[0])

    def test_default_football_source_is_fnk_without_changing_enabled_or_bank(self):
        self.store.set(PREFIX + 'enabled', True)
        self.store.set('salary_bank', 800)
        football = Football(self.store)
        self.assertIsInstance(football.source, KulichkiScoreboard)
        self.assertTrue(football.enabled())
        self.assertEqual(self.store.get('salary_bank'), 800)

    def test_cache_and_direct_updates_use_only_selected_pages_and_unique_requests(self):
        source = self.source()
        initial = source.day(DAY)
        self.now[0] += 9
        self.assertEqual(source.day(DAY), initial)
        self.assertEqual(len(self.calls), 2)
        self.pages['25707'] = broadcast('live', '1:0', goal=True)
        self.now[0] += 11
        matches, _ = source.day(DAY)
        self.assertEqual(matches[0].home_score, 1)
        self.assertNotEqual(self.calls[1], self.calls[2])
        self.assertTrue(all(url.startswith(BASE) for url in self.calls))

    def test_list_changes_after_five_minutes_and_old_unstarted_match_is_removed(self):
        source = self.source()
        source.day(DAY)
        self.html = home([('25708', 'Краснодар', 'Факел', '20:45')])
        self.pages['25708'] = broadcast(a='Краснодар', b='Факел')
        self.now[0] += 300
        matches, _ = source.day(DAY)
        self.assertEqual([m.home for m in matches], ['Краснодар'])

    def test_active_selection_survives_restart_and_midnight_homepage_rollover(self):
        self.now[0] = START + 60
        self.pages['25707'] = broadcast('live')
        self.source().day(DAY)
        self.html = home([('25708', 'Краснодар', 'Факел', '01:00')])
        self.pages['25708'] = broadcast(day=14, a='Краснодар', b='Факел')
        # Simulate a late kickoff to exercise rollover with a match still in progress.
        saved = self.store.get(PREFIX + 'fnk_selection')
        saved[0]['kickoff'] = datetime(2026, 10, 13, 23, 30, tzinfo=MOSCOW).timestamp()
        self.store.set(PREFIX + 'fnk_selection', saved)
        self.now[0] = datetime(2026, 10, 14, 0, 1, tzinfo=MOSCOW).timestamp()
        self.pages['25707'] = broadcast('second')
        source = self.source()
        matches, _ = source.day(DAY)
        self.assertEqual([m.home for m in matches], ['Локомотив'])
        tomorrow, _ = source.day(date(2026, 10, 14))
        self.assertEqual([m.home for m in tomorrow], ['Краснодар'])

    def test_unavailable_direct_page_never_uses_homepage_preview_as_score(self):
        source = self.source()
        source.day(DAY)
        self.pages['25707'] = FootballError('network_error')
        self.now[0] += 20
        with self.assertRaisesRegex(FootballError, 'network_error'):
            source.day(DAY)

    def test_one_failed_match_does_not_hide_other_selected_matches(self):
        self.html = home([('25707', 'Локомотив', 'Акрон', '18:30'), ('25708', 'Краснодар', 'Факел', '20:45')])
        self.pages['25707'] = FootballError('network_error')
        self.pages['25708'] = broadcast(a='Краснодар', b='Факел')
        source = self.source()
        matches, _ = source.day(DAY)
        self.assertEqual([m.home for m in matches], ['Краснодар'])
        self.assertEqual(source.last_error, 'network_error')

    def test_provider_switch_clears_old_match_tracking_without_old_notifications(self):
        self.now[0] = START
        self.store.set('chat_id', -100)
        self.store.set(PREFIX + 'enabled', True)
        self.store.set(PREFIX + 'seen:-100', {'old-se': {'status': 'live', 'score': [0, 0], 'kickoff': START}})
        self.pages['25707'] = broadcast('live')
        football = Football(self.store, self.source(), clock=lambda: self.now[0])
        sent = []
        football.poll(lambda c, t: sent.append(t), lambda c: True)
        self.assertEqual(len(sent), 1)
        self.assertIn(NAME, sent[0])
        self.assertEqual(list(self.store.get(PREFIX + 'seen:-100')), ['fnk:2026:25707'])
        self.now[0] += 20
        football.poll(lambda c, t: sent.append(t), lambda c: True)
        self.assertEqual(len(sent), 1)


class FetchTests(unittest.TestCase):
    def test_windows_1251_decoding_and_foreign_redirect_rejection(self):
        class Response:
            url = BASE + '/'
            headers = Message()
            headers['Content-Type'] = 'text/html; charset=windows-1251'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return 'Онлайн трансляции.'.encode('windows-1251')
        with patch('kulichki_football.urllib.request.urlopen', return_value=Response()):
            self.assertEqual(fetch_html(BASE + '/'), 'Онлайн трансляции.')
        response = Response()
        response.url = 'https://other.example/'
        with patch('kulichki_football.urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(FootballError, 'unexpected_redirect'):
                fetch_html(BASE + '/')

