import dataclasses
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bot import Store
from football_policy import excluded_competition
from football_posts import FootballPosts
from goal_videos import GoalVideos
from kulichki_football import KulichkiScoreboard, parse_home
from live_football import Football, MOSCOW, PREFIX, select_matches
from test_kulichki_football import home
from test_live_football import START, fixture, goal


class TournamentPolicyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / 'bot.sqlite3')
        self.store.set('chat_id', -100)
        self.store.set(PREFIX + 'enabled', True)
        self.now = START + 1800
        self.day = datetime.fromtimestamp(self.now, MOSCOW).date()
        self.match = dataclasses.replace(fixture('live', (1, 0)), competition='Первая лига, 15-й тур',
                                         id='fnk:2026:25707', source='Футбол на Куличках',
                                         url='https://football.kulichki.net/trans/2026/25707-Test-match.htm')

    def test_all_domestic_names_are_excluded_but_other_tournaments_are_preserved(self):
        for title in ['Первая лига, 15-й тур', 'Первая лига', 'Россия. Первая лига',
                      'МЕЛБЕТ — Первая лига', 'Лига PARI (ФНЛ)', 'Россия. ФНЛ',
                      'Россия. First League', 'Первый дивизион', 'Россия. 1-я лига']:
            self.assertTrue(excluded_competition(title), title)
        for title in ['РПЛ', 'Кубок России. Путь РПЛ.', 'Кубок России. Путь регионов.',
                      'Лига чемпионов', 'Россия. Вторая лига', 'ФНЛ-2',
                      'Казахстан. Первая лига', 'Первая лига Казахстана']:
            self.assertFalse(excluded_competition(title), title)

    def test_screenshot_four_games_are_removed_by_tournament_not_by_club_names(self):
        games = [('25701', 'СКА-Хабаровск', 'Енисей', '08:00'),
                 ('25702', 'Уфа', 'Нижний Новгород', '14:00'),
                 ('25703', 'Текстильщик', 'Сочи', '16:00'),
                 ('25704', 'Арсенал', 'КАМАЗ', '17:00')]
        html = home(games).replace('Кубок России. Путь РПЛ.', 'Первая лига, 15-й тур')
        self.assertEqual(parse_home(html), [])
        cup = ('<tr><th>Кубок России. Путь регионов.</th></tr><tr><td>18:00. '
               '<a href="/trans/2026/25705-Test-match.htm">СКА-Хабаровск - Енисей - 0:0</a></td></tr>')
        rows = parse_home(html.replace('</table>', cup + '</table>'))
        self.assertEqual([row['id'] for row in rows], ['fnk:2026:25705'])

    def test_restored_active_games_and_cached_games_never_resume_polling(self):
        self.store.set(PREFIX + 'fnk_selection', [dataclasses.asdict(self.match)])
        fetch = Mock(side_effect=AssertionError('excluded broadcast must not be fetched'))
        source = KulichkiScoreboard(self.store, fetch=fetch, clock=lambda: self.now)
        self.assertEqual(source.matches, {})
        source.cache[self.day] = (self.now, [self.match], self.now)
        self.assertEqual(source.day(self.day)[0], [])
        source.cache.clear()
        source.home_at = self.now
        source.home_list = [source.selection(self.match)]
        source.matches[self.match.id] = self.match
        self.assertEqual(source.day(self.day)[0], [])
        self.assertEqual(source.matches, {})
        fetch.assert_not_called()

    def test_notifications_and_scoreboards_ignore_excluded_matches_from_any_source(self):
        source = SimpleNamespace(day=lambda day: ([self.match], self.now))
        football = Football(self.store, source, clock=lambda: self.now)
        send, staged = Mock(), Mock()
        football.poll(send, lambda chat: True, staged=staged)
        send.assert_not_called()
        staged.observe.assert_not_called()
        cup = dataclasses.replace(self.match, competition='Кубок России')
        self.assertEqual(select_matches([self.match, cup], ''), [cup])
        self.assertEqual(select_matches([self.match], 'Ростов'), [])

    def test_old_deferred_post_edits_and_goal_videos_are_blocked(self):
        clips = Mock()
        videos = GoalVideos(self.store, clips, download=Mock(), clock=lambda: self.now)
        telegram = Mock()
        posts = FootballPosts(self.store, videos, telegram, clock=lambda: self.now)
        previous = dataclasses.replace(self.match, home_score=0, away_score=0).state()
        send = Mock(return_value=dict(chat=dict(id=-100), message_id=100))
        posts.observe(-100, previous, self.match, self.now, send, lambda chat: True)
        send.assert_not_called()
        videos.enqueue(-100, self.match, [goal((1, 0))], 100, 'old')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM goal_video_jobs').fetchone()[0], 0)
        # Simulate a post and pending video saved before the exclusion existed.
        cup = dataclasses.replace(self.match, competition='Кубок России', goals=(goal((1, 0)),))
        self.store.set(PREFIX + 'seen:-100', {cup.id: cup.state()})
        posts.observe(-100, previous, cup, self.now, send, lambda chat: True)
        with self.store.db() as db:
            row = db.execute('SELECT id,payload FROM football_goal_posts').fetchone()
            self.assertIsNotNone(row)
            payload = json.loads(row[1])
            payload['match']['competition'] = self.match.competition
            db.execute('UPDATE football_goal_posts SET payload=?,desired=? WHERE id=?',
                       (json.dumps(payload), 'needs-edit', row[0]))
            row = db.execute('SELECT payload FROM goal_video_jobs').fetchone()
            self.assertIsNotNone(row)
            payload = json.loads(row[0])
            payload['competition'] = self.match.competition
            db.execute('UPDATE goal_video_jobs SET payload=?', (json.dumps(payload),))
        posts.poll(lambda chat: True)
        telegram.edit_text.assert_not_called()
        telegram.edit_caption.assert_not_called()
        edit = Mock()
        videos.poll(edit, lambda chat: True)
        edit.assert_not_called()
        clips.recent.assert_not_called()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM goal_video_jobs').fetchone()[0], 'cancelled')
