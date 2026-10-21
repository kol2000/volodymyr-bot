import dataclasses
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from goal_videos import Clip, matches_goal, page_url
from live_football import PREFIX, event_text
from replay_goal_videos import (card_goals, channel_history, jobs_for_posts,
                                read_history, run_replay)
from test_goal_videos import MEDIA, MP4, post
from test_kulichki_football import DAY, START, URL, broadcast, parse_broadcast, parse_home, home


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'bot.sqlite3'
        self.match = parse_broadcast(broadcast('finished', '1:0', goal=True), parse_home(home())[0])
        self.stamp = START + 3000
        self.body = event_text(self.match, ('goal', 'Гол!'), self.stamp, self.match.goals)
        row = dataclasses.asdict(self.match)
        row.pop('goals')
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); '
                             'CREATE TABLE messages(chat_id INTEGER,message_id INTEGER,timestamp REAL,text TEXT,human INTEGER,body TEXT); '
                             'CREATE TABLE goal_video_jobs(chat_id INTEGER,message_id INTEGER,status TEXT);')
            db.executemany('INSERT INTO settings VALUES (?,?)', [
                ('chat_id', '-100'), (PREFIX + 'fnk_selection', json.dumps([row]))])
            db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?)', [
                (-100, 55, self.stamp, self.body, 0, self.body),
                (-100, 56, self.stamp, self.body, 1, self.body),
                (-200, 57, self.stamp, self.body, 0, self.body)])

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_only_own_confirmed_posts_in_bound_chat_without_writes(self):
        before = self.path.read_bytes()
        selection, posts, status = read_history(self.path, DAY)
        self.assertEqual([p[0] for p in posts], [55])
        self.assertEqual(selection[0]['id'], self.match.id)
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM goal_video_jobs').fetchone()[0], 0)

    def test_missing_database_is_not_created(self):
        missing = self.path.parent / 'absent.sqlite3'
        with self.assertRaises(ValueError):
            read_history(missing, DAY)
        self.assertFalse(missing.exists())

    def test_midnight_rollover_uses_kickoff_day_and_keeps_after_midnight_posts(self):
        from datetime import datetime, time
        from live_football import MOSCOW
        after_midnight = datetime.combine(DAY + timedelta(days=1), time(0, 15), MOSCOW).timestamp()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE messages SET timestamp=? WHERE message_id=55', (after_midnight,))
        self.assertEqual(len(read_history(self.path, DAY)[1]), 1)
        self.assertEqual(read_history(self.path, DAY + timedelta(days=1))[0], [])

    def test_final_score_header_is_not_a_goal_card_and_latest_goal_is_target(self):
        self.assertEqual(card_goals(self.match, f'{self.match.home} — {self.match.away} · 1:0'), [])
        second = dataclasses.replace(self.match.goals[0], id='second', score=(2, 0), minute='70′')
        match = dataclasses.replace(self.match, home_score=2, goals=(*self.match.goals, second))
        body = event_text(match, ('goal', 'Голы!'), self.stamp, match.goals)
        jobs = jobs_for_posts(match, [(55, self.stamp, body)])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['goal']['score'], (2, 0))
        self.assertEqual(len(jobs[0]['goals']), 2)

    def test_old_bot_goal_minute_can_differ_by_one_after_source_correction(self):
        goal = self.match.goals[0]
        changed = dataclasses.replace(goal, minute='8′')
        match = dataclasses.replace(self.match, goals=(changed,))
        body = event_text(match, ('goal', 'Гол!'), self.stamp, (dataclasses.replace(goal, minute='7′'),))
        self.assertEqual(card_goals(match, body), [changed])
        self.assertEqual(card_goals(match, body.replace('7′', '6′')), [])

    def test_public_pagination_is_bounded_and_stops_at_repeated_cursor(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return post(number=20, stamp=START + 3000)
        result = channel_history('matchpremier', DAY, 4, fetch)
        self.assertEqual([c.post_id for c in result], [20])
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[-1].endswith('?before=20'))
        self.assertTrue(page_url(calls[-1]))
        for query in ['before=0', 'before=-1', 'before=1&url=evil', 'after=1', 'before=1#fragment']:
            self.assertFalse(page_url('https://t.me/s/idmanvideo?' + query))

    def test_replay_downloads_match_but_never_calls_telegram_or_writes_jobs(self):
        clip = Clip('idmanvideo', 7210, self.stamp + 20,
                    'Локомотив Акрон 1:0 Иван Игрок 45+2′', MEDIA)
        before, output = self.path.read_bytes(), []
        with patch('replay_goal_videos.fetch_html', return_value=broadcast('finished', '1:0', goal=True)), \
             patch('replay_goal_videos.channel_history', side_effect=lambda c, *a, **kw: [clip] if c == 'idmanvideo' else []), \
             patch('replay_goal_videos.fetch_public', return_value=MP4) as download, \
             patch('common.Telegram.call', side_effect=AssertionError('No Telegram calls')), \
             patch('common.http_video_edit', side_effect=AssertionError('No edits')):
            self.assertEqual(run_replay(DAY, self.path, download=True, emit=output.append), 0)
        self.assertEqual(download.call_count, 1)
        self.assertIn('с подходящим видео 1', '\n'.join(output))
        self.assertIn('MP4 получен', '\n'.join(output))
        self.assertEqual(before, self.path.read_bytes())

    def test_missing_date_does_not_fall_back_to_current_homepage(self):
        output = []
        with patch('replay_goal_videos.fetch_html', side_effect=AssertionError('No fallback')):
            self.assertEqual(run_replay(DAY + timedelta(days=1), self.path, emit=output.append), 1)
        self.assertIn('Другая дата не подставляется', '\n'.join(output))

    def test_missing_video_reports_album_and_minute_rejection_without_media_urls(self):
        goal = self.match.goals[0]
        album = Clip('idmanvideo', 7201, self.stamp + 20,
                     'Локомотив Акрон 1:0 Иван Игрок 45+2′', '')
        wrong_minute = dataclasses.replace(album, post_id=7211, text='Локомотив Акрон 1:0 Иван Игрок 55′', url=MEDIA)
        def history(channel, *args, skipped=None):
            if channel == 'idmanvideo':
                skipped.append((album, 'multiple_videos'))
                return [wrong_minute]
            return []
        output = []
        with patch('replay_goal_videos.fetch_html', return_value=broadcast('finished', '1:0', goal=True)), \
             patch('replay_goal_videos.channel_history', side_effect=history):
            self.assertEqual(run_replay(DAY, self.path, emit=output.append), 0)
        result = '\n'.join(output)
        self.assertIn('https://t.me/idmanvideo/7201', result)
        self.assertIn('несколько видео / альбом', result)
        self.assertIn('minute_mismatch', result)
        self.assertIn('минута в канале=55', result)
        self.assertNotIn(MEDIA, result)

    def test_real_azerbaijani_team_spellings_match_even_when_scorer_transliteration_differs(self):
        examples = [
            ('Комо', 'Манчестер Юнайтед', 'Анастасиос Дувикас', '49′', (2, 0),
             '"Komo" 2 : 0 "Mançester Yunayted" Anastasios Douvikas, 49`'),
            ('Лилль', 'Галатасарай', 'Набиль Бенталеб', '52′', (2, 3),
             '"Lill" 2 : 3 "Qalatasaray" Nabil Bentaleb (Avtoqol), 52`')]
        for home_name, away, player, minute, score, text in examples:
            job = dict(home=home_name, away=away, kickoff=START, created=START + 3600,
                       goal=dict(score=score, minute=minute, player=player))
            self.assertTrue(matches_goal(Clip('idmanvideo', 1, START + 3600, text, MEDIA), job, START + 3700))
            wrong = dict(job, home='Интер', away='Барселона', goal=dict(score=score, minute=minute, player='Другой Нападающий'))
            self.assertFalse(matches_goal(Clip('idmanvideo', 1, START + 3600, text, MEDIA), wrong, START + 3700))

    def test_one_minute_source_difference_requires_both_teams_and_exact_score(self):
        job = dict(home='Комо', away='Манчестер Юнайтед', kickoff=START, created=START + 300,
                   goal=dict(score=(1, 0), minute='3′', player='Нико Пас'))
        def accepts(text):
            return matches_goal(Clip('idmanvideo', 1, START + 300, text, MEDIA), job, START + 360)
        self.assertTrue(accepts('"Komo" 1:0 "Mançester Yunayted" Niko Paz 2`'))
        self.assertFalse(accepts('"Komo" 1:0 "Mançester Yunayted" Niko Paz 5`'))
        self.assertFalse(accepts('"Komo" "Mançester Yunayted" Niko Paz 2`'))
        self.assertFalse(accepts('"Komo" 1:0 Гол 2`'))
        self.assertFalse(accepts('"Komo" 2:0 "Mançester Yunayted" Niko Paz 2`'))
