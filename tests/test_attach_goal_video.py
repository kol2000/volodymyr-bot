import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot import Store
from common import APIError
from goal_videos import Clip, KEY, VideoError
from live_football import PREFIX
from attach_goal_video import run, verified_plan, AttachError
from test_goal_videos import MEDIA, MP4
from test_kulichki_football import DAY, START, URL, broadcast, parse_broadcast, parse_home, home


class AttachTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'bot.sqlite3'
        self.config = self.path.parent / 'config.json'
        self.config.write_text(json.dumps(dict(token='test-secret-token', owner_id=42)))
        self.store = Store(self.path)
        self.store.set('chat_id', -100)
        self.store.set(PREFIX+'enabled', True)
        from goal_videos import GoalVideos
        GoalVideos(self.store)
        self.match = parse_broadcast(broadcast('finished', '1:0', goal=True), parse_home(home())[0])
        self.original = 'Локомотив — Акрон · 1:0\n45+2′ · Гол · 1:0\nИван Игрок'
        with self.store.db() as db:
            db.execute('INSERT INTO messages(chat_id,message_id,timestamp,text,human,body) VALUES (?,?,?,?,0,?)',
                       (-100, 55, START+3000, self.original, self.original))
        self.clip = Clip('idmanvideo', 7210, START+3000, 'Локомотив Акрон 1:0 Иван Игрок 45+2′', MEDIA)
        self.edits, self.output, self.calls = [], [], []
        self.client = SimpleNamespace(call=self.call, edit_video=self.edit)
        self.patches = [
            patch('attach_goal_video.verified_plan', side_effect=self.plan),
            patch('attach_goal_video.Telegram', return_value=self.client),
            patch('attach_goal_video.fetch_public', return_value=MP4),
            patch('attach_goal_video.fetch_html', return_value='fixture'),
            patch('attach_goal_video.parse_broadcast', side_effect=lambda *a: self.match)]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.folder.cleanup()

    def call(self, method):
        self.calls.append(method)
        self.assertEqual(method, 'getMe')
        return dict(is_bot=True, username='test_bot')

    def edit(self, chat, message, data, text):
        self.edits.append((chat, message, data, text))
        return dict(chat=dict(id=chat), message_id=message, video=dict(file_id='confirmed'))

    def plan(self, *args):
        with self.store.db() as db:
            original = db.execute('SELECT body FROM messages WHERE message_id=55').fetchone()[0]
        goal = dataclasses.asdict(self.match.goals[0])
        job = dict(home=self.match.home, away=self.match.away, competition=self.match.competition,
                   kickoff=START, created=START+3000, goal=goal, goals=[goal], original=original,
                   match_url=URL, message_id=55)
        return self.match, job, {}, self.clip, original

    def status(self):
        with self.store.db() as db:
            row = db.execute('SELECT status FROM goal_video_jobs WHERE message_id=55').fetchone()
        return row[0] if row else None

    def execute(self, apply=True):
        return run(DAY, self.path, self.config, 55, 'idmanvideo/7210', apply=apply, emit=self.output.append)

    def test_dry_run_does_not_open_config_download_write_or_call_telegram(self):
        self.config.unlink()
        self.assertEqual(self.execute(False), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.edits, [])
        self.mocks[2].assert_not_called()
        self.assertIsNone(self.status())

    def test_edits_exact_original_id_once_and_preserves_other_worker_jobs(self):
        with self.store.db() as db:
            db.execute('INSERT INTO goal_video_jobs VALUES (-100,99,?,0,0,?,?)', ('other', '{}', 'editing'))
        self.assertEqual(self.execute(), 0)
        self.assertEqual([e[:3] for e in self.edits], [(-100, 55, MP4)])
        self.assertEqual(self.status(), 'attached')
        self.assertEqual(self.execute(), 1)
        self.assertEqual(len(self.edits), 1)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM goal_video_jobs WHERE message_id=99').fetchone()[0], 'editing')
        self.assertNotIn('test-secret-token', '\n'.join(self.output))

    def test_timeout_is_reserved_as_uncertain_and_not_retried(self):
        def timeout(*args):
            self.edits.append(args)
            raise APIError('sensitive https://example/token=test-secret-token')
        self.client.edit_video = timeout
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.status(), 'uncertain')
        self.assertEqual(self.execute(), 1)
        self.assertEqual(len(self.edits), 1)
        self.assertNotIn('test-secret-token', '\n'.join(self.output))

    def test_process_interruption_leaves_manual_reservation_and_blocks_repeat(self):
        def interrupted(*args): raise KeyboardInterrupt
        self.client.edit_video = interrupted
        with self.assertRaises(KeyboardInterrupt): self.execute()
        self.assertEqual(self.status(), 'manual_editing')
        self.assertEqual(self.execute(), 1)

    def test_rebinding_during_download_prevents_edit_and_reservation(self):
        def download(*args, **kwargs):
            self.store.set('chat_id', -200)
            return MP4
        self.mocks[2].side_effect = download
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.edits, [])
        self.assertIsNone(self.status())

    def test_goal_cancelled_after_download_prevents_edit(self):
        self.mocks[4].side_effect = None
        self.mocks[4].return_value = dataclasses.replace(self.match, home_score=0, goals=())
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.edits, [])
        self.assertIsNone(self.status())

    def test_off_setting_is_respected(self):
        self.store.set(KEY+'enabled', False)
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.status())

    def test_download_failure_leaves_post_untouched_and_can_be_retried(self):
        self.mocks[2].side_effect = [VideoError('http_500'), MP4]
        self.assertEqual(self.execute(), 1)
        self.assertIsNone(self.status())
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.edits), 1)

    def test_unexpected_telegram_result_is_uncertain(self):
        self.client.edit_video = lambda *args: dict(message_id=999, chat=dict(id=-100), video=dict(file_id='x'))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.status(), 'uncertain')

    def test_existing_pending_job_is_not_stolen(self):
        with self.store.db() as db:
            db.execute('INSERT INTO goal_video_jobs VALUES (-100,55,?,0,0,?,?)', ('other', '{}', 'pending'))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.status(), 'pending')
        self.assertEqual(self.edits, [])

    def cancelled(self, status='cancelled', expires=0, match_id=None, changed_goal=None):
        job = self.plan()[1]
        if changed_goal:
            job['goal'].update(changed_goal)
        with self.store.db() as db:
            db.execute('INSERT INTO goal_video_jobs VALUES (-100,55,?,0,?,?,?)',
                       (match_id or self.match.id, expires, json.dumps(job), status))

    def recover(self, apply=True):
        return run(DAY, self.path, self.config, 55, 'idmanvideo/7210', apply=apply,
                   emit=self.output.append, resume_cancelled=True)

    def test_cancelled_recovery_edits_once_and_dry_run_preserves_job(self):
        self.cancelled()
        self.assertEqual(self.recover(False), 0)
        self.assertEqual(self.status(), 'cancelled')
        self.assertEqual(self.edits, [])
        self.assertEqual(self.recover(), 0)
        self.assertEqual(self.status(), 'attached')
        self.assertEqual(self.recover(), 1)
        self.assertEqual(len(self.edits), 1)

    def test_recovery_refuses_other_states_unexpired_or_different_goal(self):
        import time
        variants = [dict(status=s) for s in ('pending','editing','manual_editing','attached','uncertain','failed')]
        variants += [dict(expires=time.time()+3600), dict(match_id='other'), dict(changed_goal=dict(score=[2, 0]))]
        for variant in variants:
            with self.store.db() as db:
                db.execute('DELETE FROM goal_video_jobs WHERE message_id=55')
            self.cancelled(**variant)
            self.assertEqual(self.recover(), 1, variant)
            self.assertEqual(self.edits, [])
        self.assertEqual(self.calls, [])

    def test_recovery_rechecks_state_during_download_and_respects_used_clip(self):
        self.cancelled()
        def changed(*args, **kwargs):
            with self.store.db() as db:
                db.execute("UPDATE goal_video_jobs SET status='uncertain' WHERE message_id=55")
            return MP4
        self.mocks[2].side_effect = changed
        self.assertEqual(self.recover(), 1)
        self.assertEqual(self.status(), 'uncertain')
        self.assertEqual(self.edits, [])
        self.mocks[2].side_effect = None
        self.mocks[2].return_value = MP4
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='cancelled' WHERE message_id=55")
            db.execute('INSERT INTO goal_video_used VALUES (-100,?,?,0)', (self.clip.link, 'old-digest'))
        self.assertEqual(self.recover(), 1)
        self.assertEqual(self.status(), 'cancelled')
        self.assertEqual(self.edits, [])

    def test_recovery_timeout_is_uncertain_and_never_retried(self):
        self.cancelled()
        def timeout(*args):
            self.edits.append(args)
            raise APIError('timeout')
        self.client.edit_video = timeout
        self.assertEqual(self.recover(), 1)
        self.assertEqual(self.status(), 'uncertain')
        self.assertEqual(self.recover(), 1)
        self.assertEqual(len(self.edits), 1)

    def test_previously_used_video_is_not_attached_to_another_post(self):
        with self.store.db() as db:
            db.execute('INSERT INTO goal_video_used VALUES (-100,?,?,0)', (self.clip.link, 'digest'))
        self.assertEqual(self.execute(), 1)
        self.assertIsNone(self.status())
        self.assertEqual(self.edits, [])

    def test_read_only_plan_requires_real_bot_post_and_exact_source_goal(self):
        self.patches[0].stop()
        from live_football import event_text
        original = str(event_text(self.match, ('goal', 'Гол!'), START+3000, self.match.goals))
        row = dataclasses.asdict(self.match); row.pop('goals')
        self.store.set(PREFIX+'fnk_selection', [row])
        with self.store.db() as db:
            db.execute('UPDATE messages SET body=?,text=? WHERE message_id=55', (original, original))
        with patch('attach_goal_video.channel_history', return_value=[self.clip]):
            plan = verified_plan(DAY, self.path, 55, 'idmanvideo/7210', 4)
            self.assertEqual(plan[3].post_id, 7210)
            with self.assertRaises(AttachError): verified_plan(DAY, self.path, 56, 'idmanvideo/7210', 4)
            with self.assertRaises(AttachError): verified_plan(DAY, self.path, 55, 'idmanvideo/7211', 4)
        self.assertIsNone(self.status())
