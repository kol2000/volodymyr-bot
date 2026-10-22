import dataclasses
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot import Store
from common import APIError, HTMLMessage, visible_text
from football_posts import FootballPosts
from goal_videos import Clip, GoalVideos
from live_football import Football, PREFIX
from test_bot import FakeTelegram
from test_goal_videos import MEDIA, MP4
from test_live_football import START, fixture, goal

URL = 'https://football.kulichki.net/trans/2026/25766-Test-match.htm'


class PostTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name)/'bot.sqlite3')
        self.store.set('chat_id',-100)
        self.store.set(PREFIX+'enabled',True)
        self.now = [START+1800]
        self.match = dataclasses.replace(fixture('live',(0,0)),id='fnk:2026:25766',
                                         label='30′ · матч идёт',url=URL,source='Футбол на Куличках')
        self.store.set(PREFIX+'seen:-100',{self.match.id:self.match.state()})
        self.telegram = FakeTelegram()
        self.edits, self.uploads, self.clips = [], [], []
        self.telegram.edit_text = lambda *a:self.edit('text',*a)
        self.telegram.edit_caption = lambda *a:self.edit('caption',*a)
        self.videos = GoalVideos(self.store,SimpleNamespace(recent=lambda:self.clips,errors={}),
                                download=lambda u:MP4,clock=lambda:self.now[0])
        self.posts = FootballPosts(self.store,self.videos,self.telegram,clock=lambda:self.now[0])
        self.source = SimpleNamespace(name='Футбол на Куличках',day=lambda d:([self.match],self.now[0]))
        self.football = Football(self.store,self.source,clock=lambda:self.now[0])

    def edit(self,kind,chat,number,text):
        self.edits.append((kind,chat,number,text))
        return dict(chat=dict(id=chat),message_id=number,video=dict(file_id='same'))

    def send(self,chat,text):
        sent = self.telegram.send(chat,text)
        self.store.add_message(sent,human=False,learnable=False,rateable=False)
        return sent

    def tick(self,scores=None,goals=None,cancellations=(),edit=True,**kwargs):
        self.now[0] += 22
        if scores is not None:
            kwargs.update(home_score=scores[0],away_score=scores[1])
        if goals is not None:
            kwargs['goals']=goals
        self.match=dataclasses.replace(self.match,cancellations=cancellations,**kwargs)
        self.football.poll(self.send,lambda c:c==-100,staged=self.posts)
        if edit:
            self.posts.poll(lambda c:c==-100)

    def rows(self):
        with self.store.db() as db:
            return db.execute('SELECT * FROM football_goal_posts ORDER BY id').fetchall()

    def video_row(self):
        with self.store.db() as db:
            return db.execute('SELECT * FROM goal_video_jobs ORDER BY message_id').fetchone()

    def attach(self):
        self.clips=[Clip('matchpremier',1,self.now[0],'Ростов — Акрон 1:0. Иван Игрок 30′',MEDIA)]
        def upload(chat,number,data,text):
            self.uploads.append((chat,number,data,text))
            return dict(chat=dict(id=chat),message_id=number,video=dict(file_id='one'))
        self.videos.poll(upload,lambda c:c==-100)

    def test_immediate_score_then_description_and_video_share_one_post(self):
        self.tick((1,0),goals=())
        self.assertEqual(len(self.telegram.sent),1)
        self.assertIn('1:0',self.telegram.sent[0][1])
        self.assertIn('Подробности гола',self.telegram.sent[0][1])
        self.assertIsNone(self.video_row())
        self.tick(goals=(goal(),))
        self.assertEqual(self.edits[0][:3],('text',-100,10001))
        self.assertIn('После прострела',self.edits[0][3])
        self.attach()
        self.assertEqual(self.uploads[0][:2],(-100,10001))
        self.assertIn(f'<a href="{URL}">Футбол на Куличках</a>',self.uploads[0][3])
        self.assertNotIn('https://',visible_text(self.uploads[0][3]))
        self.assertEqual(len(self.telegram.sent),1)

    def test_description_continues_growing_after_video_without_reupload(self):
        first = goal(description='ГОООЛ! Игрок 1:0!')
        self.tick((1,0),goals=(first,))
        self.attach()
        detailed = dataclasses.replace(first,description='ГОООЛ! Игрок 1:0! Пробил после навеса головой.')
        self.tick(goals=(detailed,))
        self.assertEqual(self.edits[0][:3],('caption',-100,10001))
        self.assertIn('после навеса головой',self.edits[0][3])
        self.tick(goals=(detailed,))
        self.assertEqual((len(self.edits),len(self.uploads),len(self.telegram.sent)),(1,1,1))

    def test_var_decrease_waits_for_real_reason_and_edits_same_post(self):
        self.tick((1,0),goals=(goal(),))
        self.tick((0,0),goals=())
        self.assertEqual(len(self.telegram.sent),1)
        self.assertIn('<s>30′ · Гол · 1:0</s>',self.edits[-1][3])
        self.assertIn('Пояснение причины ждём',self.edits[-1][3])
        self.assertNotIn('VAR',self.edits[-1][3])
        decision = dict(minute='32′',description='После VAR гол Игрока отменён из-за офсайда.',var=True)
        self.tick(cancellations=(decision,))
        self.assertIn('📺 VAR',self.edits[-1][3])
        self.assertIn('из-за офсайда',self.edits[-1][3])
        self.assertIn('Ростов — Акрон · 0:0',self.edits[-1][3])
        self.assertEqual(self.video_row()[-1],'cancelled')
        self.assertEqual(len(self.telegram.sent),1)

    def test_cancelled_video_keeps_video_and_strike_and_shortens_only_explanation(self):
        self.tick((1,0),goals=(goal(),))
        self.attach()
        reason='После проверки VAR гол отменён из-за игры рукой. '+'Детали эпизода. '*180
        self.tick((0,0),goals=(),cancellations=(dict(minute='31′',description=reason,var=True),))
        text=self.edits[-1][3]
        self.assertEqual(self.edits[-1][0],'caption')
        self.assertIn('<s>30′ · Гол · 1:0</s>',text)
        self.assertIn('📺 VAR',text)
        self.assertIn('из-за игры рукой',text)
        self.assertIn(f'href="{URL}"',text)
        self.assertLessEqual(len(visible_text(text).encode('utf-16-le'))//2,1024)
        self.assertEqual((len(self.telegram.sent),len(self.uploads)),(1,1))

    def test_var_verdict_before_board_correction_does_not_invent_actual_score(self):
        self.tick((1,0),goals=(goal(),))
        verdict=dict(minute='32′',description='VAR: гол Игрока отменён из-за офсайда.',var=True)
        self.tick(cancellations=(verdict,))
        self.assertIn('<s>30′ · Гол · 1:0</s>',self.edits[-1][3])
        self.assertIn('📺 VAR',self.edits[-1][3])
        self.assertIn('счёт уточняется',self.edits[-1][3])
        self.assertNotIn('Ростов — Акрон · 0:0',self.edits[-1][3])
        self.tick((0,0),goals=(),cancellations=(verdict,))
        self.assertIn('Ростов — Акрон · 0:0',self.edits[-1][3])
        self.assertEqual(len(self.telegram.sent),1)

    def test_later_full_var_explanation_replaces_short_verdict(self):
        self.tick((1,0),goals=(goal(),))
        short=dict(minute='31′',description='VAR: гол отменён.',var=True)
        self.tick((0,0),goals=(),cancellations=(short,))
        detailed=dict(minute='32′',description='VAR: гол Игрока отменён из-за офсайда перед пасом.',var=True)
        self.tick(cancellations=(detailed,short))
        self.assertIn('из-за офсайда перед пасом',self.edits[-1][3])
        self.assertEqual(len(self.telegram.sent),1)

    def test_later_real_goal_at_same_score_gets_a_new_post(self):
        self.tick((1,0),goals=(goal(),))
        self.tick((0,0),goals=())
        self.tick((1,0),goals=(goal(identifier='new',minute='40′'),),label='40′ · матч идёт')
        self.assertEqual(len(self.telegram.sent),2)
        self.assertEqual([json.loads(row[5])['cancelled'] for row in self.rows()],[True,False])

    def test_old_var_verdict_does_not_cancel_a_later_goal_by_same_player(self):
        self.tick((1,0),goals=(goal(),))
        old=dict(minute='32′',description='VAR: гол Игрока отменён из-за офсайда.',var=True)
        self.tick((0,0),goals=(),cancellations=(old,))
        self.tick((1,0),goals=(goal(identifier='new',minute='40′'),),label='40′ · матч идёт',cancellations=(old,))
        self.tick(cancellations=(old,))
        self.assertEqual([json.loads(row[5])['cancelled'] for row in self.rows()],[True,False])

    def test_partial_rollback_strikes_only_cancelled_goal_of_combined_card(self):
        self.tick((2,0),goals=(goal(),goal((2,0),'second',minute='33′')))
        self.tick((1,0),goals=(goal(),))
        self.assertIn('<s>33′ · Гол · 2:0</s>',self.edits[-1][3])
        self.assertIn('<b>30′ · Гол · 1:0</b>',self.edits[-1][3])
        self.assertNotIn('<s>30′',self.edits[-1][3])

    def test_var_caption_for_many_combined_goals_still_fits_and_keeps_cancelled_line(self):
        goals=tuple(goal((i,0),str(i),minute=f'{20+i}′',player='Очень длинное имя футболиста '*4) for i in range(1,11))
        self.tick((10,0),goals=goals)
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='attached'")
        self.tick((9,0),goals=goals[:-1])
        text=self.edits[-1][3]
        self.assertIn('<s>30′ · Гол · 10:0</s>',text)
        self.assertLessEqual(len(visible_text(text).encode('utf-16-le'))//2,1024)
        self.assertIn(f'href="{URL}"',text)

    def test_reason_of_another_cancelled_goal_is_not_reused(self):
        self.tick((1,0),goals=(goal(),))
        self.tick((0,0),goals=(),cancellations=(dict(minute='10′',description='Гол Петрова отменён после VAR.',var=True),))
        self.assertNotIn('Петрова',self.edits[-1][3])

    def test_restart_does_not_repeat_post_and_resumes_description(self):
        self.tick((1,0),goals=())
        self.posts=FootballPosts(self.store,self.videos,self.telegram,clock=lambda:self.now[0])
        self.football=Football(self.store,self.source,clock=lambda:self.now[0])
        self.tick(goals=(goal(),))
        self.assertEqual(len(self.telegram.sent),1)
        self.assertEqual(self.edits[-1][2],10001)

    def test_uncertain_send_is_never_reposted_or_edited(self):
        self.telegram.fail=True
        with self.assertRaises(APIError):
            self.tick((1,0),goals=())
        self.telegram.fail=False
        self.tick(goals=(goal(),))
        self.assertEqual(len(self.telegram.sent),1)
        self.assertEqual(self.rows()[0][8],'uncertain')
        self.assertEqual(self.edits,[])

    def test_text_edit_timeout_retries_latest_content_on_same_id(self):
        self.tick((1,0),goals=())
        self.telegram.edit_text=lambda *a:(_ for _ in ()).throw(APIError('timeout'))
        self.tick(goals=(goal(),))
        self.assertEqual(self.rows()[0][9],'idle')
        self.telegram.edit_text=lambda *a:self.edit('text',*a)
        self.now[0]+=31
        self.posts.poll(lambda c:True)
        self.assertEqual(self.edits[0][2],10001)
        self.assertEqual(len(self.telegram.sent),1)

    def test_successful_edit_with_lost_ack_is_recognized_on_idempotent_retry(self):
        self.tick((1,0),goals=())
        self.telegram.edit_text=lambda *a:(_ for _ in ()).throw(APIError('timeout'))
        self.tick(goals=(goal(),))
        self.telegram.edit_text=lambda *a:(_ for _ in ()).throw(APIError('unchanged',400,not_modified=True))
        self.now[0]+=31
        self.posts.poll(lambda c:True)
        self.assertEqual(self.rows()[0][6],self.rows()[0][7])
        self.assertEqual(self.video_row()[-1],'pending')
        self.assertEqual(len(self.telegram.sent),1)

    def test_var_edit_can_reconcile_unknown_upload_result_without_reupload(self):
        self.tick((1,0),goals=(goal(),))
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='uncertain'")
        self.tick((0,0),goals=())
        self.assertEqual(self.edits[-1][0],'caption')
        self.assertIn('<s>30′ · Гол · 1:0</s>',self.edits[-1][3])
        self.assertEqual(self.video_row()[-1],'attached')
        self.assertEqual((len(self.telegram.sent),len(self.uploads)),(1,0))

    def test_failed_unknown_upload_keeps_original_text_and_can_still_get_var_edit(self):
        self.tick((1,0),goals=(goal(),))
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='uncertain'")
        self.telegram.edit_caption=lambda *a:(_ for _ in ()).throw(APIError('no caption',400))
        self.tick((0,0),goals=())
        self.assertEqual(self.edits[-1][0],'text')
        self.assertIn('<s>30′ · Гол · 1:0</s>',self.edits[-1][3])
        self.assertEqual((len(self.telegram.sent),len(self.uploads)),(1,0))

    def test_flood_limit_defers_same_post_edit_without_duplicate_send(self):
        self.tick((1,0),goals=())
        self.telegram.edit_text=lambda *a:(_ for _ in ()).throw(APIError('rate',429,50))
        self.tick(goals=(goal(),))
        self.telegram.edit_text=lambda *a:self.edit('text',*a)
        self.posts.poll(lambda c:True)
        self.assertEqual(self.edits,[])
        self.now[0]+=51
        self.posts.poll(lambda c:True)
        self.assertEqual(len(self.edits),1)
        self.assertEqual(len(self.telegram.sent),1)

    def test_finish_post_is_sent_once_but_description_can_grow_after_final(self):
        self.tick((1,0),goals=(goal(description='ГОООЛ! 1:0!'),))
        self.tick(status='finished',label='Матч завершён')
        self.tick(goals=(goal(description='ГОООЛ! 1:0! Точный удар после розыгрыша углового.'),))
        self.assertEqual(len(self.telegram.sent),2)
        self.assertIn('после розыгрыша углового',self.edits[-1][3])
        self.assertTrue(self.source.follow_updates)

    def test_new_score_does_not_wait_for_video_upload_lock(self):
        self.tick((1,0),goals=(goal(),))
        done=threading.Event()
        errors=[]
        def watch():
            try:
                self.tick((2,0),goals=(goal(),goal((2,0),'second')),edit=False)
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        with self.videos.lock:
            worker=threading.Thread(target=watch)
            worker.start()
            completed=done.wait(3)
        worker.join(3)
        self.assertTrue(completed)
        self.assertEqual(errors,[])
        self.assertEqual(len(self.telegram.sent),2)

    def test_disabled_football_stops_description_edits(self):
        self.tick((1,0),goals=())
        self.tick(goals=(goal(),),edit=False)
        self.store.set(PREFIX+'enabled',False)
        self.posts.poll(lambda c:True)
        self.assertEqual(self.edits,[])

