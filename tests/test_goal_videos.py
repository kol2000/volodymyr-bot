import dataclasses
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot import Bot, Store
from common import APIError, HTMLMessage, MODEL, Telegram, http_video_edit, visible_text
from goal_videos import (Clip, GoalVideos, INTERVAL, KEY, TTL, PublicClips, VideoError,
                         caption, fetch_public, goal_match_reason, matches_goal, media_url, parse_channel)
from live_football import Football, PREFIX, football_request
from test_bot import FakeTelegram
from test_live_football import START, fixture, goal

MEDIA = 'https://cdn4.telesco.pe/file/public-goal.mp4'
MP4 = b'\x00\x00\x00\x18ftypisom' + b'x' * 40


def post(channel='matchpremier', number=1, body='Ростов — Акрон 1:0. Иван Игрок 30′',
         video=MEDIA, stamp=START + 1800):
    date = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    return (f'<div class="tgme_widget_message js-widget_message" data-post="{channel}/{number}">'
            f'<div class="tgme_widget_message_text js-message_text">{body}</div>'
            f'<video src="{video}"></video><time datetime="{date}"></time></div>')


def quoted_post(row):
    quote = ''.join('<a class="tgme_widget_message_reply js-widget_message_reply">'
                    '<div class="tgme_widget_message_text js-message_text">' + escape(text) + '</div>'
                    '</a>' for text in row['quotes'])
    return (f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="idmanvideo/{row["id"]}">'
            + quote + '<div class="tgme_widget_message_text js-message_text">' + escape(row['caption']) + '</div>'
            + f'<video src="{MEDIA}?clip={row["id"]}"></video><time></time><time datetime="{row["timestamp"]}"></time></div>')


class ParserTests(unittest.TestCase):
    def test_reply_quote_cannot_label_current_video_or_supply_its_date(self):
        current = '"Real Madrid" 4:1 "Leypsiq" Yan Diomande, 87`'
        quoted = '"Real Madrid" 3:1 "Leypsiq" Tidiam Gomis, 85`'
        quote = ('<a class="tgme_widget_message_reply js-widget_message_reply">'
                 '<div class="tgme_widget_message_text js-message_text">' + quoted + '</div>'
                 '<video src="' + MEDIA + '?quoted=1"></video>'
                 '<time datetime="2000-01-01T00:00:00Z"></time></a>')
        html = post(channel='idmanvideo', number=7210, body=current).replace(
            '<div class="tgme_widget_message_text js-message_text">', quote +
            '<div class="tgme_widget_message_text js-message_text">', 1)
        clips = parse_channel(html, 'idmanvideo')
        self.assertEqual(len(clips), 1)
        self.assertEqual((clips[0].text, clips[0].timestamp, clips[0].url), (current, START + 1800, MEDIA))

    def test_quote_without_own_caption_never_becomes_video_caption(self):
        html = post(body='').replace('<div class="tgme_widget_message_text js-message_text"></div>',
            '<a class="tgme_widget_message_reply"><div class="tgme_widget_message_text">Ростов Акрон 1:0 30′</div></a>')
        self.assertEqual(parse_channel(html, 'matchpremier'), [])

    def test_multiple_nonquoted_captions_fail_closed(self):
        html = post().replace('<video', '<div class="tgme_widget_message_text">Ростов Акрон 2:0 40′</div><video', 1)
        skipped = []
        self.assertEqual(parse_channel(html, 'matchpremier', skipped), [])
        self.assertEqual(skipped[0][1], 'ambiguous_caption')

    def test_all_twenty_observed_goal_captions_match_own_video_and_never_previous_goal(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/idmanvideo_goal_posts_20261021.json').read_text())
        expected = {
            7190: ('Лилль', 'Галатасарай', (0, 1), '2′'),
            7191: ('Комо', 'Манчестер Юнайтед', (1, 0), '3′'),
            7192: ('Лилль', 'Галатасарай', (1, 1), '8′'),
            7193: ('Лилль', 'Галатасарай', (2, 1), '39′'),
            7194: ('Лилль', 'Галатасарай', (2, 2), '44′'),
            7195: ('Комо', 'Манчестер Юнайтед', (2, 0), '49′'),
            7196: ('Лилль', 'Галатасарай', (2, 3), '52′'),
            7197: ('Комо', 'Манчестер Юнайтед', (3, 0), '57′'),
            7198: ('Лилль', 'Галатасарай', (2, 4), '61′'),
            7199: ('Комо', 'Манчестер Юнайтед', (4, 0), '76′'),
            7200: ('Комо', 'Манчестер Юнайтед', (4, 1), '90+1′'),
            7201: ('Бавария', 'Арсенал', (0, 1), '25′'),
            7202: ('Бавария', 'Арсенал', (1, 1), '35′'),
            7203: ('Реал Мадрид', 'Лейпциг', (1, 0), '44′'),
            7204: ('Реал Мадрид', 'Лейпциг', (2, 0), '52′'),
            7205: ('Бавария', 'Арсенал', (2, 1), '61′'),
            7207: ('Бавария', 'Арсенал', (2, 2), '82′'),
            7208: ('Реал Мадрид', 'Лейпциг', (3, 0), '83′'),
            7209: ('Реал Мадрид', 'Лейпциг', (3, 1), '85′'),
            7210: ('Реал Мадрид', 'Лейпциг', (4, 1), '87′')}
        clips = parse_channel(''.join(map(quoted_post, rows)), 'idmanvideo')
        self.assertEqual(len(clips), 20)
        jobs = {}
        for clip in clips:
            home, away, score, minute = expected[clip.post_id]
            jobs[clip.post_id] = dict(home=home, away=away, kickoff=clip.timestamp-7200,
                                     created=clip.timestamp, goal=dict(score=score, minute=minute, player='Игрок ФНК'))
        for clip in clips:
            with self.subTest(post=clip.post_id):
                self.assertEqual(clip.text, next(r['caption'] for r in rows if r['id'] == clip.post_id))
                self.assertEqual(clip.url, MEDIA + f'?clip={clip.post_id}')
                accepted = [n for n, j in jobs.items() if matches_goal(clip, j, max(c.timestamp for c in clips))]
                self.assertEqual(accepted, [clip.post_id])

    def test_reads_native_clip_date_caption_and_id(self):
        clips = parse_channel(post(), 'matchpremier')
        self.assertEqual(len(clips), 1)
        self.assertEqual((clips[0].post_id, clips[0].url, clips[0].timestamp), (1, MEDIA, START + 1800))
        self.assertEqual(clips[0].link, 'https://t.me/matchpremier/1')

    def test_unreadable_preview_is_an_error_not_an_empty_success(self):
        for html in ['<title>Site Unavailable</title>', '<html>Sign in</html>']:
            with self.assertRaisesRegex(VideoError, 'channel_preview_unavailable'):
                parse_channel(html, 'matchpremier')

    def test_skips_external_links_albums_wrong_channel_and_missing_dates(self):
        for html in [post(video='https://evil.example/clip.mp4'), post(channel='other'),
                     post().replace('<video', '<video src="' + MEDIA + '"></video><video', 1),
                     post().replace('datetime=', 'invalid='),
                     post().replace('<video src="' + MEDIA + '"></video>', '<a href="https://site.example">видео</a>'),
                     post().replace('data-post=', 'data-protected="1" data-post=')]:
            with self.subTest(html=html):
                self.assertEqual(parse_channel(html, 'matchpremier'), [])

    def test_signed_urls_are_unescaped_and_duplicate_posts_deduplicated(self):
        clips = parse_channel(post(video=MEDIA + '?a=1&amp;b=2') * 2, 'matchpremier')
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].url, MEDIA + '?a=1&b=2')

    def test_only_public_telegram_media_hosts_are_accepted(self):
        self.assertTrue(media_url(MEDIA))
        for value in ['http://cdn4.telesco.pe/file/x', 'https://127.0.0.1/file/x',
                      'https://cdn4.telesco.pe.evil/file/x', 'https://x@cdn4.telesco.pe/file/x',
                      'https://cdn4.telesco.pe:444/file/x', 'https://cdn4.telesco.pe/private/x']:
            self.assertFalse(media_url(value))

    def test_skipped_goal_diagnostics_have_only_public_links_and_fixed_reasons(self):
        skipped = []
        html = post(number=1).replace('<video', '<video src="' + MEDIA + '"></video><video', 1)
        html += post(number=2).replace('<video src="' + MEDIA + '"></video>', '')
        html += post(number=3, video='https://elsewhere.example/private.mp4?secret=abc')
        html += post(number=4).replace('data-post=', 'data-protected="1" data-post=')
        self.assertEqual(parse_channel(html, 'matchpremier', skipped), [])
        self.assertEqual([r for _, r in skipped], ['multiple_videos', 'no_native_video',
                                                 'unsupported_media_url', 'protected_post'])
        self.assertTrue(all(c.url == '' and c.link.startswith('https://t.me/matchpremier/') for c, _ in skipped))

    def test_one_source_failure_does_not_hide_other_channel(self):
        calls, now = [], [START]
        def fetch(url):
            calls.append(url)
            return '<title>Site Unavailable</title>' if 'matchpremier' in url else post(channel='idmanvideo')
        source = PublicClips(fetch=fetch, clock=lambda: now[0])
        self.assertEqual([c.channel for c in source.recent()], ['idmanvideo'])
        self.assertIn('matchpremier', source.errors)
        source.recent()
        self.assertEqual(len(calls), 2)
        now[0] += INTERVAL
        source.recent()
        self.assertEqual(len(calls), 4)


class VideoFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.store.set(PREFIX + 'enabled', True)
        self.now = [START + 1800]
        self.match = fixture('live', (1, 0), goals=(goal(),))
        self.store.set(PREFIX + 'seen:-100', {self.match.id: self.match.state()})
        self.clip = Clip('matchpremier', 1, self.now[0], 'Ростов — Акрон 1:0. Иван Игрок 30′', MEDIA)
        self.source = SimpleNamespace(recent=lambda: [self.clip], errors={})
        self.videos = GoalVideos(self.store, self.source, download=lambda url: MP4, clock=lambda: self.now[0])
        self.edits = []

    def tearDown(self):
        self.folder.cleanup()

    def queue(self, message=55, match=None, goals=None, original='<b>Ростов — Акрон · 1:0</b>\n30′ · Иван Игрок'):
        self.videos.enqueue(-100, match or self.match, goals or (goal(),), message, original)

    def row(self):
        with self.store.db() as db:
            return db.execute('SELECT * FROM goal_video_jobs ORDER BY message_id LIMIT 1').fetchone()

    def poll(self, edit=None):
        self.videos.poll(edit or (lambda *args: self.edits.append(args)), lambda c: c == -100)


class WorkerTests(VideoFixture):
    def test_domestic_narrative_clips_edit_five_original_cards(self):
        from live_football import Match, Goal
        data = json.loads((Path(__file__).parent / 'fixtures/matchpremier_goal_posts_20261024.json').read_text(encoding='utf-8'))
        matches = [Match(**dict(m, goals=tuple(Goal(**dict(g, score=tuple(g['score']))) for g in m['goals'])))
                   for m in data['matches']]
        self.store.set(PREFIX + 'seen:-100', {m.id: m.state() for m in matches})
        expected = {30103: (0, 0), 30104: (0, 1), 30105: (0, 2), 30107: (0, 3), 30110: (1, 0)}
        for post in sorted(data['posts'], key=lambda p: p['post_id']):
            if post['post_id'] not in expected:
                continue
            clip = Clip('matchpremier', post['post_id'], post['timestamp'], post['text'], MEDIA+str(post['post_id']))
            index, target = expected[clip.post_id]
            match = matches[index]
            self.now[0] = clip.timestamp+1
            self.videos.enqueue(-100, match, (match.goals[target],), clip.post_id, 'Исходная карточка')
            self.source.recent = lambda: [clip]
            self.videos.download = lambda url: MP4+url.encode()
            self.poll()
        self.assertEqual([e[1] for e in self.edits], list(expected))

    def test_later_goal_refreshes_pending_context_without_changing_earlier_target(self):
        self.queue()
        second = dataclasses.replace(goal(), id='second', score=(2, 0), minute='60′')
        match = dataclasses.replace(self.match, home_score=2, goals=(*self.match.goals, second))
        self.queue(56, match=match, goals=(second,))
        earlier = json.loads(self.row()[5])
        self.assertEqual(earlier['goal']['score'], [1, 0])
        self.assertEqual(earlier['match_score'], [2, 0])
        self.assertEqual(len(earlier['match_goals']), 2)

    def test_description_refreshed_during_download_is_used_for_video_caption(self):
        self.queue()
        def download(url):
            self.queue(original='Ростов — Акрон · 1:0\n30′ · Иван Игрок\nНовое подробное описание эпизода.')
            return MP4
        self.videos.download=download
        self.poll()
        self.assertIn('Новое подробное описание',self.edits[0][3])

    def test_var_cancellation_during_download_prevents_video_edit(self):
        self.queue()
        def download(url):
            self.videos.cancel()
            return MP4
        self.videos.download=download
        self.poll()
        self.assertEqual(self.edits,[])
        self.assertEqual(self.row()[-1],'cancelled')

    def test_two_real_goal_posts_attach_their_own_videos_not_quoted_predecessors(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/idmanvideo_goal_posts_20261021.json').read_text())
        clips = parse_channel(''.join(quoted_post(r) for r in rows if r['id'] in (7209, 7210)), 'idmanvideo')
        self.now[0] = max(c.timestamp for c in clips) + 30
        first = dataclasses.replace(goal(), score=(3, 1), minute='85′', player='Тидьям Гомис')
        last = dataclasses.replace(goal(), id='last', score=(4, 1), minute='87′', player='Ян Дьоманде')
        match = dataclasses.replace(self.match, home='Реал Мадрид', away='Лейпциг',
                                    kickoff=self.now[0]-7200, home_score=4, away_score=1, goals=(first,last))
        self.store.set(PREFIX + 'seen:-100', {match.id: match.state()})
        self.source.recent = lambda: clips
        self.videos.download = lambda url: MP4 + url.rsplit('=', 1)[-1].encode()
        self.queue(55, match=match, goals=(first,), original='Реал Мадрид — Лейпциг 3:1')
        self.queue(56, match=match, goals=(last,), original='Реал Мадрид — Лейпциг 4:1')
        self.poll()
        self.assertEqual([(e[1], e[2]) for e in self.edits], [(55, MP4+b'7209'), (56, MP4+b'7210')])

    def test_attaches_to_original_id_once_across_restart(self):
        self.queue()
        self.poll()
        self.assertEqual(self.edits[0][:3], (-100, 55, MP4))
        self.assertIn('Иван Игрок', self.edits[0][3])
        self.assertNotIn('https://', self.edits[0][3])
        self.assertIn('Футбол на Куличках', self.edits[0][3])
        self.assertEqual(self.row()[-1], 'attached')
        self.videos = GoalVideos(Store(self.path), self.source, download=lambda url: MP4, clock=lambda: self.now[0])
        self.now[0] += 31
        self.poll()
        self.assertEqual(len(self.edits), 1)

    def test_video_appearing_later_edits_existing_post(self):
        self.queue()
        self.source.recent = lambda: []
        self.poll()
        self.assertEqual(self.edits, [])
        self.source.recent = lambda: [self.clip]
        self.now[0] += 31
        self.poll()
        self.assertEqual(self.edits[0][1], 55)

    def test_cancelled_goal_never_gets_video(self):
        self.queue()
        self.store.set(PREFIX + 'seen:-100', {self.match.id: fixture('live', (0, 0)).state()})
        self.poll()
        self.assertEqual(self.edits, [])
        self.assertEqual(self.row()[-1], 'cancelled')

    def test_rebinding_expiry_and_opt_out_cancel_or_stop_work(self):
        self.queue()
        self.store.set('chat_id', -200)
        self.poll()
        self.assertEqual(self.row()[-1], 'cancelled')
        self.assertFalse(self.edits)
        self.store.set('chat_id', -100)
        self.queue(56)
        self.videos.answer(dict(kind='football_video_switch', enabled=False), -100)
        self.videos.answer(dict(kind='football_video_switch', enabled=True), -100)
        self.queue(57)
        self.now[0] += TTL + 1
        self.poll()
        self.assertFalse(self.edits)

    def test_video_published_after_sixty_four_minutes_is_still_attached(self):
        self.queue()
        self.now[0] += 3849
        self.clip = dataclasses.replace(self.clip, timestamp=self.now[0])
        self.source.recent = lambda: [self.clip]
        self.poll()
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(self.row()[-1], 'attached')

    def test_cancellation_while_downloading_is_rechecked_before_edit(self):
        self.queue()
        def download(url):
            self.store.set(PREFIX + 'seen:-100', {self.match.id: fixture('live', (0, 0)).state()})
            return MP4
        self.videos.download = download
        self.poll()
        self.assertFalse(self.edits)

    def test_timeout_does_not_send_replacement_or_repeat_edit(self):
        self.queue()
        attempts = []
        def edit(*args):
            attempts.append(args)
            raise APIError('timeout')
        self.poll(edit)
        self.assertEqual(self.row()[-1], 'uncertain')
        self.now[0] += 31
        self.poll(edit)
        self.assertEqual(len(attempts), 1)

    def test_rate_limit_can_retry_same_message_after_explicit_rejection(self):
        self.queue()
        def reject(*args):
            raise APIError('limit', 429, 60)
        self.poll(reject)
        self.assertEqual(self.row()[-1], 'pending')
        self.assertEqual(self.store.get('blocked_until'), self.now[0] + 60)
        self.now[0] += 61
        self.poll()
        self.assertEqual(self.row()[-1], 'attached')

    def test_crash_during_edit_becomes_uncertain_after_restart(self):
        self.queue()
        self.videos.mark(self.row(), 'editing')
        self.videos = GoalVideos(self.store, self.source, download=lambda url: MP4, clock=lambda: self.now[0])
        self.poll()
        self.assertFalse(self.edits)
        self.assertEqual(self.row()[-1], 'uncertain')

    def test_duplicate_post_and_video_bytes_cannot_attach_twice(self):
        self.queue()
        self.poll()
        self.queue(56)
        self.clip = dataclasses.replace(self.clip, post_id=2)
        calls = []
        self.videos.download = lambda url: calls.append(url) or MP4
        self.now[0] += 31
        self.poll()
        self.now[0] += 31
        self.poll()
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(calls), 1)

    def test_ambiguous_caption_is_not_attached_to_either_match(self):
        self.queue()
        other = dataclasses.replace(self.match, id='2', home='Другой', away='Клуб')
        self.queue(56, match=other)
        self.store.set(PREFIX + 'seen:-100', {self.match.id: self.match.state(), other.id: other.state()})
        self.clip = dataclasses.replace(self.clip, text='Гол! Иван Игрок 30′ 1:0')
        self.poll()
        self.assertFalse(self.edits)

    def test_first_channel_failure_uses_second_source(self):
        self.queue()
        self.source.recent = lambda: [dataclasses.replace(self.clip, channel='idmanvideo')]
        self.source.errors = {'matchpremier': 'network_error'}
        self.poll()
        self.assertEqual(len(self.edits), 1)
        self.assertNotIn('@idmanvideo', self.edits[0][3])

    def test_long_caption_is_bounded_and_retains_match_goal_and_source(self):
        self.queue(original='полное описание ' * 300)
        job = json.loads(self.row()[5])
        job['goal']['description'] = 'очень длинное описание ' * 100
        text = caption(job, self.clip)
        self.assertLessEqual(len(visible_text(text).encode('utf-16-le')) // 2, 1024)
        self.assertIn('Ростов — Акрон', text)
        self.assertIn('Иван Игрок', text)
        self.assertIn('Футбол на Куличках', text)
        self.assertNotIn('https://', text)

    def test_goals_are_queued_only_after_confirmed_fnk_card_send(self):
        self.store.set(PREFIX + 'seen:-100', {self.match.id: fixture('live', (0, 0)).state()})
        football = Football(self.store, SimpleNamespace(day=lambda d: ([self.match], self.now[0])), clock=lambda: self.now[0])
        football.poll(lambda c, t: {'message_id': 55}, lambda c: True, self.videos.enqueue)
        self.assertEqual(self.row()[1], 55)
        self.poll()
        self.assertEqual(len(self.edits), 1)

    def test_uncertain_original_send_has_no_message_to_edit(self):
        self.store.set(PREFIX + 'seen:-100', {self.match.id: fixture('live', (0, 0)).state()})
        football = Football(self.store, SimpleNamespace(day=lambda d: ([self.match], self.now[0])), clock=lambda: self.now[0])
        football.poll(lambda c, t: None, lambda c: True, self.videos.enqueue)
        self.assertIsNone(self.row())


class MatchingTests(VideoFixture):
    # These tests use real source caption shapes, including abbreviated single-team posts.
    def test_score_minute_teams_and_cancelled_post_validation(self):
        self.queue()
        job = json.loads(self.row()[5])
        self.assertTrue(matches_goal(self.clip, job, self.now[0]))
        for text in ['Ростов — Акрон 2:0 Игрок 30′', 'Ростов — Акрон 1:0 Игрок 60′',
                     'Краснодар — Факел 1:0 30′', 'Ростов — Акрон 1:0 30′ гол отменён',
                     'Обзор Ростов — Акрон 1:0 30′']:
            self.assertFalse(matches_goal(dataclasses.replace(self.clip, text=text), job, self.now[0]))
        self.assertFalse(matches_goal(dataclasses.replace(self.clip, timestamp=START - 600), job, self.now[0]))

    def test_matchpremier_narrative_without_score_requires_team_minute_and_goal(self):
        self.queue()
        job = json.loads(self.row()[5])
        job.update(home='Родина', away='Оренбург')
        job['goal'].update(minute='90′', score=[2, 2])
        text = 'РОДИНА СРАВНЯЛА НА 90 МИНУТЕ! СНАЧАЛА ШТАНГА, А ПОТОМ В ВОРОТА!'
        self.assertTrue(matches_goal(dataclasses.replace(self.clip, text=text), job, self.now[0]))
        self.assertFalse(matches_goal(dataclasses.replace(self.clip, text='Родина атакует на 90 минуте'), job, self.now[0]))

    def test_azerbaijani_names_and_added_minutes(self):
        self.queue()
        job = json.loads(self.row()[5])
        job.update(home='Карабах', away='Твенте')
        job['goal'].update(minute='90+4′', score=[1, 3])
        text = '"Qarabağ" 1 : 3 "Tvente" (2:3)—Vegorst 94`'
        self.assertTrue(matches_goal(dataclasses.replace(self.clip, text=text), job, self.now[0]))


class NarrativeCaptionTests(unittest.TestCase):
    def setUp(self):
        data = json.loads((Path(__file__).parent / 'fixtures/matchpremier_goal_posts_20261024.json').read_text(encoding='utf-8'))
        self.clips = {p['post_id']: Clip('matchpremier', p['post_id'], p['timestamp'], p['text'], MEDIA)
                      for p in data['posts']}
        self.jobs = []
        for match in data['matches']:
            for goal in match['goals']:
                self.jobs.append(dict(home=match['home'], away=match['away'], competition=match['competition'],
                    kickoff=match['kickoff'], created=match['kickoff'], goal=goal,
                    match_goals=match['goals'], match_score=[match['home_score'], match['away_score']]))
        self.now = max(c.timestamp for c in self.clips.values()) + 60

    def test_five_observed_domestic_goal_videos_have_unique_correct_matches(self):
        expected = {30103: ('Артем Шуманский', (1, 0)), 30104: ('Ярослав Гладышев', (1, 1)),
                    30105: ('Кристиян Бистрович', (2, 1)), 30107: ('Максим Болдырев', (3, 1)),
                    30110: ('Герман Онугха', (0, 1)), 30106: None, 30109: None}
        for number, clip in self.clips.items():
            found = [(j['goal']['player'], tuple(j['goal']['score'])) for j in self.jobs if matches_goal(clip, j, self.now)]
            self.assertEqual(found, [expected[number]] if expected[number] else [], number)

    def test_scoreless_caption_needs_complete_domestic_context_and_native_channel(self):
        job = next(j for j in self.jobs if j['goal']['player'] == 'Ярослав Гладышев')
        clip = self.clips[30104]
        for changed in [dict(match_goals=[]), dict(match_score=[4, 1]),
                        dict(competition='Чемпионат Англии'), dict(match_goals=job['match_goals'][1:])]:
            self.assertFalse(matches_goal(clip, dict(job, **changed), self.now), changed)
        self.assertFalse(matches_goal(dataclasses.replace(clip, channel='idmanvideo'), job, self.now))
        self.assertFalse(matches_goal(dataclasses.replace(clip, timestamp=clip.timestamp+3600), job, self.now+3600))

    def test_contradictions_negation_replays_and_team_only_do_not_attach(self):
        for text in ['Гладышев не сравнял.', 'Акрон впереди!', 'Первый гол! Пенальти Родины не забит.',
                     'Повтор: Гладышев сравнял.', 'Гол Бистровича. Родина впереди!',
                     'Акрону помог рикошет.', 'Третий пропущенный у Родины.',
                     'Разбор: Акрону помог рикошет. У Лунёва не было шансов.']:
            clip = dataclasses.replace(self.clips[30105], text=text)
            self.assertFalse(any(matches_goal(clip, j, self.now) for j in self.jobs), text)

    def test_repeated_scorer_equalizers_remain_ambiguous_even_if_one_card_is_waiting(self):
        job = next(j for j in self.jobs if j['goal']['player'] == 'Ярослав Гладышев')
        goals = [dict(g) for g in job['match_goals']]
        goals[-1].update(score=[2, 2], minute='65′', player='Ярослав Гладышев')
        changed = dict(job, match_goals=goals, match_score=[2, 2])
        self.assertEqual(goal_match_reason(self.clips[30104], changed, self.now), 'ambiguous_narrative_goal')

    def test_first_half_added_time_does_not_include_half_time_break(self):
        job = next(j for j in self.jobs if j['goal']['player'] == 'Герман Онугха')
        goal = dict(job['goal'], minute='45+2′')
        job = dict(job, goal=goal, match_goals=[goal])
        clip = dataclasses.replace(self.clips[30110], timestamp=job['kickoff']+50*60)
        self.assertTrue(matches_goal(clip, job, clip.timestamp+60))

    def test_replay_jobs_preserve_full_context_for_scoreless_captions(self):
        from live_football import Match, Goal, event_text
        from replay_goal_videos import jobs_for_posts
        data = json.loads((Path(__file__).parent / 'fixtures/matchpremier_goal_posts_20261024.json').read_text(encoding='utf-8'))
        row = data['matches'][0]
        match = Match(**dict(row, goals=tuple(Goal(**dict(g, score=tuple(g['score']))) for g in row['goals'])))
        body = event_text(match, ('goal', 'Гол!'), self.clips[30107].timestamp, (match.goals[-1],))
        jobs = jobs_for_posts(match, [(55, self.clips[30107].timestamp, body)])
        self.assertEqual(len(jobs), 1)
        self.assertTrue(matches_goal(self.clips[30107], jobs[0], self.now))


class UploadTests(unittest.TestCase):
    def test_slow_video_upload_does_not_hold_text_send_lock(self):
        telegram = Telegram('test-token')
        def upload(*args):
            self.assertFalse(telegram.send_lock.locked())
            return {'message_id': 55}
        with patch('common.http_video_edit', side_effect=upload):
            telegram.edit_video(-100, 55, MP4, 'caption')

    def test_multipart_edits_same_post_with_video_and_caption_not_send_message(self):
        response = io.BytesIO(b'{"ok":true,"result":{"message_id":55}}')
        with patch('common.urllib.request.urlopen', return_value=response) as request:
            result = Telegram('test-token').edit_video(-100, 55, MP4, 'Ростов 1:0')
        self.assertEqual(result['message_id'], 55)
        req = request.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/editMessageMedia'))
        self.assertIn(b'name="message_id"\r\n\r\n55', req.data)
        self.assertIn(b'attach://clip', req.data)
        self.assertIn(MP4, req.data)
        self.assertNotIn(b'forwardMessage', req.data)

    def test_multipart_html_caption_counts_visible_characters_and_preserves_link(self):
        text=HTMLMessage('<b>'+'x'*970+'</b>\n<a href="https://football.kulichki.net/trans/'+ 'a'*120+'.htm">ФНК</a>')
        response=io.BytesIO(b'{"ok":true,"result":{"message_id":55}}')
        with patch('common.urllib.request.urlopen',return_value=response) as request:
            Telegram('test-token').edit_video(-100,55,MP4,text)
        self.assertIn(b'"parse_mode": "HTML"',request.call_args.args[0].data)

    def test_upload_rejects_invalid_file_and_oversized_caption_before_network(self):
        with patch('common.urllib.request.urlopen') as network:
            for data, text in [(b'not a video', 'caption'), (MP4, 'x' * 1025)]:
                with self.assertRaises(APIError):
                    http_video_edit('https://example.invalid', -100, 55, data, text)
            network.assert_not_called()

    def test_public_downloader_rejects_invalid_content_and_oversize(self):
        for data, content_type, length, reason in [(b'html-not-video', 'text/html', '14', 'unexpected_content_type'),
                (b'x' * 16, 'video/mp4', '16', 'not_mp4'),
                (MP4, 'video/mp4', str(46 * 1024 * 1024), 'video_too_large')]:
            headers = Message()
            headers['Content-Type'], headers['Content-Length'] = content_type, length
            response = io.BytesIO(data)
            response.url, response.headers = MEDIA, headers
            opener = SimpleNamespace(open=lambda *args, **kwargs: response)
            with patch('goal_videos.urllib.request.build_opener', return_value=opener):
                with self.assertRaisesRegex(VideoError, reason):
                    fetch_public(MEDIA, video=True)

    def test_video_commands_are_routed_and_switch_is_owner_only(self):
        self.assertEqual(football_request('/football_videos_on', '/football_videos_on'), dict(kind='football_video_switch', enabled=True))
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'bot.sqlite3')
            store.set('chat_id', -100)
            bot = Bot(dict(owner_id=42, model=MODEL), FakeTelegram(), store)
            import time
            bot.handle({'message': dict(chat={'id': -100, 'type': 'supergroup'},
                         **{'from': {'id': 7}}, date=time.time(), message_id=1, text='/football_videos_off')})
            self.assertEqual(bot.utility_jobs.get_nowait()['utility']['kind'], 'fixed')
            self.assertTrue(store.get(KEY + 'enabled', True))


if __name__ == '__main__':
    unittest.main()
