import unittest,dataclasses
from unittest.mock import patch
from live_football import scoreboard_text
from test_live_football import fixture,DAY,START
class PageTests(unittest.TestCase):
 def answer(self):
  matches=[dataclasses.replace(fixture('scheduled',(None,None)),id=str(i),home='Команда'+str(i)) for i in range(28)]
  return scoreboard_text(matches,DAY,START)
 def test_all_matches_buttons_and_limits(self):
  answer=self.answer();pages=[answer,*answer.continuations]
  ids=[r['match_id'] for p in pages for r in p.prediction_buttons.values()]
  self.assertEqual(ids,[str(i) for i in range(28)])
  self.assertTrue(all(len(p.encode('utf-16-le'))//2<4096 for p in pages))
  self.assertTrue(all('Ещё матчей' not in p for p in pages))
 def test_worker_sends_and_remembers_all_pages(self):
  import test_bot
  f=test_bot.BotTests();f.setUp()
  try:
   f.bind();f.bot.pending_requests.clear();f.bot.handle(f.update('/football',number=100))
   job=f.bot.utility_jobs.get_nowait();answer=self.answer()
   with patch.object(f.bot.football,'answer',return_value=answer),patch('bot.time.sleep'):
    f.bot.process_job(job)
   self.assertEqual(len(f.telegram.sent),3)
   self.assertEqual(len(f.store.get('prediction:buttons:-100')),3)
  finally:f.tearDown()
 def test_failure_does_not_replay_first_page(self):
  import test_bot
  from common import APIError
  f=test_bot.BotTests();f.setUp()
  try:
   f.bind();f.bot.pending_requests.clear();f.bot.handle(f.update('/football',number=100));job=f.bot.utility_jobs.get_nowait()
   original=f.telegram.send
   def send(*args):
    if len(f.telegram.sent):raise APIError('unknown delivery')
    return original(*args)
   with patch.object(f.bot.football,'answer',return_value=self.answer()),patch.object(f.telegram,'send',side_effect=send),patch('bot.time.sleep'):
    f.bot.process_job(job)
    self.assertEqual(f.telegram.send.call_count,2)
   self.assertEqual(len(f.telegram.sent),1)
  finally:f.tearDown()
