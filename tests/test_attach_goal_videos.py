import dataclasses
import json
import re
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from attach_goal_videos import run
from bot import Store
from common import APIError, Telegram
from goal_videos import GoalVideos, caption, parse_channel, VideoError
from live_football import Goal, Match, MOSCOW, PREFIX, event_text
from test_goal_videos import MP4, quoted_post

DAY = date(2026, 10, 21)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'bot.sqlite3'
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.store.set(PREFIX+'enabled', True)
        GoalVideos(self.store)
        rows = json.loads((Path(__file__).parent/'fixtures/idmanvideo_goal_posts_20261021.json').read_text())
        self.clips = parse_channel(''.join(map(quoted_post, rows)), 'idmanvideo')
        grouped = {}
        self.messages = {}
        for row in rows:
            text = row['caption']
            home, away, hour = (('Лилль', 'Галатасарай', 19) if 'Lill' in text else
                                ('Комо', 'Манчестер Юнайтед', 19) if 'Komo' in text else
                                ('Бавария', 'Арсенал', 22) if 'Bavariya' in text else
                                ('Реал Мадрид', 'Лейпциг', 22))
            score = tuple(map(int, re.search(r'(\d+)\s*:\s*(\d+)', text).groups()))
            minute = re.search(r'(\d+(?:\+\d+)?)`', text)[1]+'′'
            goal = Goal(str(row['id']), score, minute, 'Игрок '+str(row['id']), 'goal', 'Описание этого гола.')
            grouped.setdefault((home,away,hour), []).append(goal)
            self.messages[row['id']] = (goal, datetime.fromisoformat(row['timestamp']).timestamp()-20)
        self.matches = {}
        selected = []
        for i, ((home,away,hour), goals) in enumerate(grouped.items()):
            kickoff = datetime(2026,10,21,hour,45 if hour==19 else 0,tzinfo=MOSCOW).timestamp()
            url = f'https://football.kulichki.net/trans/2026/{25763+i}-Test-match.htm'
            match = Match('fnk:2026:'+str(25763+i), 'Лига чемпионов', home,away,kickoff,
                          'finished','Матч завершён',*goals[-1].score,url,goals=tuple(goals),source='Футбол на Куличках')
            self.matches[url] = match
            row = dataclasses.asdict(match); row.pop('goals'); selected.append(row)
            with self.store.db() as db:
                for goal in goals:
                    number = int(goal.id)
                    _, stamp = self.messages[number]
                    original = str(event_text(dataclasses.replace(match, home_score=goal.score[0], away_score=goal.score[1]),
                                              ('goal', '⚽ Счет изменился'), stamp, (goal,)))
                    if number == 7210:
                        original += '\nФНК: '+url+'\n\nВидео: @idmanvideo\nhttps://t.me/idmanvideo/7210'
                    db.execute('INSERT INTO messages(chat_id,message_id,timestamp,text,human,body) VALUES (-100,?,?,?,0,?)',
                               (number,stamp,original,original))
        self.store.set(PREFIX+'fnk_selection', selected)
        with self.store.db() as db:
            db.execute('INSERT INTO goal_video_jobs VALUES (-100,7210,?,0,0,?,?)', ('last', '{}', 'attached'))
        self.edits, self.caption_edits, self.downloads, self.output = [], [], [], []
        self.telegram = SimpleNamespace(edit_video=self.edit, edit_caption=self.edit_caption)
        self.patches = [
            patch('attach_goal_videos.connected_telegram', return_value=self.telegram),
            patch('attach_goal_videos.fetch_html', side_effect=lambda u:u),
            patch('attach_goal_videos.parse_broadcast', side_effect=lambda u,s:self.matches[u]),
            patch('attach_goal_videos.channel_history', side_effect=lambda c,*a:self.clips if c=='idmanvideo' else []),
            patch('attach_goal_video.fetch_html', side_effect=lambda u:u),
            patch('attach_goal_video.parse_broadcast', side_effect=lambda u,s:self.matches[u]),
            patch('attach_goal_video.fetch_public', side_effect=self.download),
            patch('attach_goal_videos.time.sleep', return_value=None)]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.folder.cleanup()

    def download(self, url, **kwargs):
        self.downloads.append(url)
        return MP4+url.rsplit('=',1)[-1].encode()

    def edit(self, chat, number, data, text):
        self.edits.append((number,data,text))
        return dict(message_id=number,chat=dict(id=chat),video=dict(file_id='video'))

    def edit_caption(self, chat, number, text):
        self.caption_edits.append((number,text))
        return dict(message_id=number,chat=dict(id=chat),video=dict(file_id='same_video'))

    def execute(self, apply=True):
        return run(DAY,self.path,self.path.parent/'unused-config.json',apply=apply,emit=self.output.append)

    def test_all_twenty_posts_attach_nineteen_videos_and_only_edit_existing_real_caption(self):
        self.assertEqual(self.execute(),0)
        self.assertEqual(len(self.edits),19)
        self.assertEqual([e[0] for e in self.caption_edits],[7210])
        self.assertEqual(len(self.downloads),19)
        for number,data,text in self.edits:
            self.assertEqual(data, MP4+str(number).encode())
            self.assertNotIn('https://',text)
            self.assertNotIn('Видео:',text)
            self.assertEqual(text.count('проверено'),1)
        self.assertEqual(self.execute(),0)
        self.assertEqual(len(self.edits),19)
        self.assertEqual(len(self.caption_edits),1)
        self.assertEqual(len(self.downloads),19)

    def test_dry_plan_does_not_edit_or_download(self):
        self.assertEqual(self.execute(False),0)
        self.assertEqual(self.downloads,[])
        self.assertEqual(self.edits,[])
        self.assertEqual(self.caption_edits,[])
        self.mocks[0].assert_not_called()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM goal_video_jobs').fetchone()[0],1)

    def test_one_cdn_failure_allows_other_goals_then_rerun_only_missing_post(self):
        failed = [False]
        def download(url, **kwargs):
            if url.endswith('=7191') and not failed[0]:
                failed[0] = True
                raise VideoError('http_500')
            return self.download(url, **kwargs)
        self.mocks[6].side_effect = download
        self.assertEqual(self.execute(),1)
        self.assertEqual(len(self.edits),18)
        self.assertEqual(self.execute(),0)
        self.assertEqual(len(self.edits),19)
        self.assertEqual(len(self.caption_edits),1)

    def test_uncertain_caption_edit_is_not_repeated_and_never_reuploads_video(self):
        def fail(*args):
            self.caption_edits.append(args)
            raise APIError('timeout with private credentials')
        self.telegram.edit_caption = fail
        self.assertEqual(self.execute(),1)
        self.assertEqual(len(self.caption_edits),1)
        self.assertEqual(self.execute(),1)
        self.assertEqual(len(self.caption_edits),1)
        self.assertEqual(len(self.edits),19)
        self.assertNotIn('private credentials','\n'.join(self.output))


class CaptionTests(unittest.TestCase):
    def test_removes_circled_service_block_and_preserves_goal_and_single_original_footer(self):
        original = ('⚽ Счет изменился\nРеал Мадрид — Лейпциг · 4:1\n\n87′ · Гол · 4:1\nЯн Дьоманде\nОписание гола.\n\n'
                    'Футбол на Куличках · проверено 23:48:24 МСК\nФНК: https://football.kulichki.net/trans/file.htm\n\n'
                    'Видео: @idmanvideo\nhttps://t.me/idmanvideo/7210')
        text = caption(dict(original=original))
        self.assertIn('87′ · Гол · 4:1\nЯн Дьоманде\nОписание гола.',text)
        self.assertTrue(text.endswith('Футбол на Куличках · проверено 23:48:24 МСК'))
        self.assertNotIn('https://',text)
        self.assertNotIn('Видео:',text)
        self.assertNotIn('ФНК:',text)
        self.assertEqual(text.count('Футбол на Куличках'),1)

    def test_caption_only_client_calls_edit_caption_not_video_upload_or_send(self):
        client = Telegram('unused-token')
        with patch.object(client,'call',return_value={'message_id':55}) as call:
            self.assertEqual(client.edit_caption(-100,55,'короткая подпись'),{'message_id':55})
            call.assert_called_once_with('editMessageCaption',chat_id=-100,message_id=55,caption='короткая подпись')
