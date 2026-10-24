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
        minute = {'live': '1', 'second': '46', 'break': '45', 'finished': '90'}[status]
        rows = f'<tr><td>{minute}</td><td><img src="/trans/{marker}"></td><td>Статус игры.</td></tr>'
    if goal:
        rows += '<tr><td>+2</td><td><img src="/trans/GOOal.gif"></td><td>ГОООЛ! Игрок! 1:0! Пробил со штрафного в угол.</td></tr>'
    return title + board + summary + '<table id="table15">' + rows + '<tr><td>-</td><td></td><td>Анонс.</td></tr></table>'


def mixed_scorers_broadcast():
    # Foreign players are plain text; only the home scorer has a profile link.
    entries = [('Таборда', 'Висенте Таборда', 6, '0:1'),
               ('Дессерс', 'Сириль Дессерс', 24, '0:2'),
               ('Дессерс', 'Сириль Дессерс', 47, '0:3'),
               ('Кангва', 'Кингс Кангва', 57, '0:4'),
               ('Растодер', 'Эльмин Растодер', 75, '0:5'),
               ('<a href="/players/32657.htm">Бекболат</a>', 'Исмаил Бекболат', 79, '1:5')]
    summary, rows = [], []
    for name, full, minute, score in entries:
        penalty = minute == 24
        qualifier = ' - с пенальти' if penalty else ''
        summary.append(f'{name}, {minute}{qualifier} ({score})')
        icon = 'pengoal.gif' if penalty else 'GOOal.gif'
        rows.append(f'<tr><td>{minute}</td><td><img src="/trans/{icon}"></td>'
                    f'<td>!!! ГООООЛ! {full}, {score}! Описание эпизода {score}.</td></tr>')
    header = '<table><tr><td><img src="/trans/goal.gif">' + '; '.join(summary) + '</td></tr></table>'
    return broadcast('second', '1:5').replace('<table id="table15">',
              header + '<table id="table15">' + ''.join(rows))


class ParsingTests(unittest.TestCase):
    def test_plain_and_linked_scorers_and_repeated_penalty_scorer(self):
        match = parse_broadcast(mixed_scorers_broadcast(), parse_home(home())[0])
        self.assertEqual([g.score for g in match.goals],
                         [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 5)])
        self.assertEqual([g.player for g in match.goals],
                         ['Висенте Таборда', 'Сириль Дессерс', 'Сириль Дессерс',
                          'Кингс Кангва', 'Эльмин Растодер', 'Исмаил Бекболат'])
        self.assertEqual(match.goals[1].kind, 'penalty-goal')
        self.assertEqual(len(goals_between(match, [0, 0])), 6)

    def test_var_verdict_reason_and_added_minute_are_read_from_broadcast(self):
        description='После VAR гол Игрока отменён — офсайд перед передачей.'
        html=broadcast('second').replace('<table id="table15">',
             '<table id="table15"><tr><td>+2</td><td></td><td>'+description+'</td></tr>')
        match=parse_broadcast(html,parse_home(home())[0])
        self.assertEqual(match.cancellations,(dict(minute='90+2′',description=description,var=True),))

    def test_var_check_or_non_cancellation_is_not_treated_as_cancelled_goal(self):
        for description in ['VAR проверяет гол на офсайд.', 'Гол не отменён после VAR.',
                            'Гол могут отменить после проверки VAR.', 'Судья не стал отменять гол.']:
            with self.subTest(description=description):
                html=broadcast('live').replace('<table id="table15">',
                     '<table id="table15"><tr><td>32</td><td></td><td>'+description+'</td></tr>')
                self.assertEqual(parse_broadcast(html,parse_home(home())[0]).cancellations,())

    def test_explicit_cancellation_with_blank_minute_is_preserved(self):
        html=broadcast('live').replace('<table id="table15">',
             '<table id="table15"><tr><td>-</td><td></td><td>Гол Игрока отменён после VAR из-за фола.</td></tr>')
        decision=parse_broadcast(html,parse_home(home())[0]).cancellations[0]
        self.assertEqual(decision['minute'],'')
        self.assertTrue(decision['var'])

    def test_var_icon_and_explicit_referee_cancellation_are_used(self):
        html=broadcast('live').replace('<table id="table15">',
             '<table id="table15"><tr><td>32</td><td><img src="/trans/VAR.gif"></td>'
             '<td>Арбитр отменяет гол Игрока из-за офсайда.</td></tr>')
        decision=parse_broadcast(html,parse_home(home())[0]).cancellations[0]
        self.assertTrue(decision['var'])

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

    def test_period_whistles_with_empty_minutes_are_not_dropped(self):
        for status, minute in [('break', '45'), ('finished', '90')]:
            for blank in ['', '-', '—']:
                with self.subTest(status=status, blank=blank):
                    html = broadcast(status).replace(f'<td>{minute}</td>', f'<td>{blank}</td>')
                    match = parse_broadcast(html, parse_home(home())[0])
                    self.assertEqual(match.status, status)

    def test_stale_first_half_marker_cannot_reopen_break_after_second_half(self):
        selected = parse_home(home())[0]
        for head in [('<td>90</td><td><img src="/trans/0.gif"></td><td>Игра продолжается.</td>'),
                     ('<td>+4</td><td><img src="/trans/end1.gif"></td><td>Статус обновляется.</td>')]:
            with self.subTest(head=head):
                # The newest row precedes the second-half kickoff and old halftime.
                html = broadcast('break', '4:1').replace('<table id="table15">',
                    '<table id="table15"><tr>' + head + '</tr>'
                    '<tr><td>46</td><td><img src="/trans/2t.gif"></td><td>Второй тайм.</td></tr>')
                match = parse_broadcast(html, selected)
                self.assertEqual(match.status, 'live')
                self.assertIn('90', match.label)

    def test_second_half_minutes_override_old_break_without_kickoff_marker(self):
        html = broadcast('break', '4:1').replace('<table id="table15">',
              '<table id="table15"><tr><td>90+4</td><td></td><td>Игра идёт.</td></tr>')
        match = parse_broadcast(html, parse_home(home())[0])
        self.assertEqual((match.status, match.label), ('live', '90+4′ · матч идёт'))

    def test_wrong_end1_at_ninetieth_minute_does_not_infer_final_or_break(self):
        html = broadcast('break', '4:1').replace('<td>45</td>', '<td>90</td>')
        match = parse_broadcast(html, parse_home(home())[0])
        self.assertEqual(match.status, 'live')

    def test_first_half_stoppage_time_is_still_a_real_break(self):
        html = broadcast('break').replace('<td>45</td>', '<td>45+4</td>')
        match = parse_broadcast(html, parse_home(home())[0])
        self.assertEqual(match.status, 'break')

    def test_screenshot_sequence_has_only_one_final_card_after_second_half(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'bot.sqlite3')
            store.set('chat_id', -100)
            store.set(PREFIX + 'enabled', True)
            now = [START + 6600]
            selected = parse_home(home())[0]
            row = parse_broadcast(broadcast('second', '4:1'), selected)
            store.set(PREFIX + 'seen:-100', {row.id: dict(row.state(), announced_score=[4, 1])})
            source = KulichkiScoreboard()
            source.day = lambda day: ([row], now[0])
            football = Football(store, source, clock=lambda: now[0])
            sent = []
            # While the editor posts the final whistle, the old break is still
            # present, but its second-half kickoff marker is temporarily missing.
            html = broadcast('break', '4:1').replace('<table id="table15">',
                   '<table id="table15"><tr><td>90+4</td><td></td><td>Доигрывают.</td></tr>')
            row = parse_broadcast(html, selected)
            football.poll(lambda chat, message: sent.append(message), lambda chat: True)
            self.assertEqual(sent, [])
            now[0] += 22
            row = parse_broadcast(broadcast('finished', '4:1'), selected)
            football.poll(lambda chat, message: sent.append(message), lambda chat: True)
            now[0] += 301
            football = Football(Store(Path(folder) / 'bot.sqlite3'), source, clock=lambda: now[0])
            football.poll(lambda chat, message: sent.append(message), lambda chat: True)
            self.assertEqual(len(sent), 1)
            self.assertIn('Матч завершён', sent[0])
            self.assertNotIn('Перерыв', sent[0])

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

    def test_cska_rostov_third_goal_uses_fnk_penalty_goal_icon(self):
        # FNK uses pengoal.gif for a scored penalty, not pen.gif. The penalty
        # award immediately below it is a separate event with no goal score.
        selected = dict(id='fnk:2026:25712', home='ЦСКА', away='Ростов',
                        clock='20:45', competition='Кубок России',
                        url=BASE + '/trans/2026/25712-CSKA-Rostov-online-translyaciyu-matcha-smotret.htm')
        html = broadcast('live', '3:0', day=14, a='ЦСКА', b='Ростов')
        summary, rows = '<table><tr><td><img src="/trans/goal.gif">', ''
        for score, name, minute in [(1, 'Аззи', 2), (2, 'Кармо', 5), (3, 'Обляков', 11)]:
            qualifier = ' - с пенальти' if score == 3 else ''
            summary += f'<a href="/players/{score}.htm">{name}</a>, {minute}{qualifier} ({score}:0); '
            icon = 'pengoal.gif' if score == 3 else 'GOOal.gif'
            rows += (f'<tr><td>{minute}</td><td><img src="/trans/{icon}"></td>'
                     f'<td>ГООООЛ!!! {name}, {score}:0. Удар в угол ворот.</td></tr>')
        summary += '</td></tr></table><a href="/players/3.htm">Иван Обляков</a>'
        rows += '<tr><td>10</td><td><img src="/trans/pen.gif"></td><td>Пенальти!</td></tr>'
        html = html.replace('<table id="table15">', summary + '<table id="table15">' + rows)
        match = parse_broadcast(html, selected)
        self.assertEqual(len(goals_between(match, [0, 0])), 3)
        goal = goals_between(match, [2, 0])[0]
        self.assertEqual((goal.player, goal.minute, goal.kind, goal.score),
                         ('Иван Обляков', '11′', 'penalty-goal', (3, 0)))
        self.assertIn('Удар в угол ворот', goal.description)

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
    def test_moscow_midnight_refreshes_home_and_discards_prefetched_empty_day(self):
        self.now[0]=datetime(2026,10,13,23,59,58,tzinfo=MOSCOW).timestamp()
        source=self.source()
        self.assertEqual(source.day(date(2026,10,14))[0],[])
        self.html=home([('25708','ЦСКА','Спартак','16:00')])
        self.pages['25708']=broadcast(day=14,a='ЦСКА',b='Спартак')
        self.now[0]+=3
        matches,_=source.day(date(2026,10,14))
        self.assertEqual([(m.home,m.away,m.status) for m in matches],[('ЦСКА','Спартак','scheduled')])
        self.assertEqual(datetime.fromtimestamp(matches[0].kickoff,MOSCOW).hour,16)
        self.assertEqual(len([u for u in self.calls if '/trans/' not in u]),2)

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

    def test_final_score_does_not_freeze_short_goal_description_when_following_posts(self):
        self.now[0]=START+7200
        self.pages['25707']=broadcast('finished','1:0',goal=True).replace('Пробил со штрафного в угол.','')
        source=self.source()
        source.follow_updates=True
        matches,_=source.day(DAY)
        self.assertNotIn('со штрафного',matches[0].goals[0].description)
        self.pages['25707']=broadcast('finished','1:0',goal=True)
        self.now[0]+=22
        matches,_=source.day(DAY)
        self.assertIn('со штрафного',matches[0].goals[0].description)
        self.assertEqual(len(self.calls),3)

    def test_recent_finished_match_survives_midnight_until_details_can_be_updated(self):
        self.now[0]=START+7200
        self.pages['25707']=broadcast('finished','1:0',goal=True)
        self.source().day(DAY)
        saved=self.store.get(PREFIX+'fnk_selection')
        saved[0]['kickoff']=datetime(2026,10,13,23,30,tzinfo=MOSCOW).timestamp()
        self.store.set(PREFIX+'fnk_selection',saved)
        self.now[0]=datetime(2026,10,14,0,30,tzinfo=MOSCOW).timestamp()
        self.html=home([])
        source=self.source()
        source.follow_updates=True
        matches,_=source.day(DAY)
        self.assertEqual([m.id for m in matches],['fnk:2026:25707'])

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


