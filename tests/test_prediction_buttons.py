import unittest,time,json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from common import HTMLMessage,Telegram
from prediction_buttons import decorate,remember,handle
from test_bot import BotTests

class ButtonTests(unittest.TestCase):
 setUp=BotTests.setUp
 tearDown=BotTests.tearDown
 update=BotTests.update
 def setup_button(self):
  self.store.set('chat_id',-100);self.bot.bot_id=99;self.telegram.call=Mock()
  self.answer=decorate(HTMLMessage('Матчи'),[SimpleNamespace(id='soccer:1',home='Бавария',away='Кёльн',status='scheduled')],date(2026,11,20))
  remember(self.store,-100,900,self.answer)
  return dict(id='cb1',data='fp:0',message={'message_id':900,'from':{'id':99},'chat':{'id':-100}},**{'from':{'id':7,'first_name':'User'}})
 def test_callback_routes_exact_match_and_deduplicates(self):
  q=self.setup_button();self.bot.handle({'callback_query':q})
  job=self.bot.utility_jobs.get_nowait()
  self.assertEqual(job['utility']['match_id'],'soccer:1');self.assertEqual(job['reply_to'],900)
  q['from']['id']=8;self.bot.handle({'callback_query':q})
  self.assertTrue(self.bot.utility_jobs.empty());self.assertEqual(self.telegram.call.call_count,2)
 def test_foreign_expired_unknown_buttons(self):
  for field,value in [('data','fp:9'),('chat',-999),('sender',66),('message',901),('expired',True)]:
   q=self.setup_button()
   if field=='data':q['data']=value
   if field=='chat':q['message']['chat']['id']=value
   if field=='sender':q['message']['from']['id']=value
   if field=='message':q['message']['message_id']=value
   if field=='expired':
    saved=self.store.get('prediction:buttons:-100');saved['900']['created']=time.time()-8*86400;self.store.set('prediction:buttons:-100',saved)
   handle(self.bot,q);self.assertTrue(self.bot.utility_jobs.empty())
 def test_send_payload_contains_keyboard(self):
  self.setup_button();telegram=Telegram('test');telegram.call=Mock(return_value={})
  telegram.send(-100,self.answer)
  payload=telegram.call.call_args.kwargs
  self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'],'fp:0')
  self.assertEqual(payload['parse_mode'],'HTML')
 def test_finished_has_no_button(self):
  a=decorate(HTMLMessage('Матчи'),[SimpleNamespace(status='finished')],date.today())
  self.assertFalse(hasattr(a,'reply_markup'))
 def test_schedule_send_persists_buttons(self):
  self.setup_button()
  self.bot.handle(self.update('/football',number=910))
  job=self.bot.utility_jobs.get_nowait()
  with patch.object(self.bot.football,'answer',return_value=self.answer):self.bot.process_job(job)
  self.assertIn('10001',self.store.get('prediction:buttons:-100'))
 def test_goal_links_only_attached_same_chat_and_finished(self):
  from prediction_buttons import completed_goal_links
  chat=-100123456
  with self.store.db() as db:
   for mid,status,score,target in [(11,'pending',[1,0],chat),(12,'attached',[2,0],chat),(13,'attached',[3,0],chat),(14,'uncertain',[4,0],chat),(15,'attached',[5,0],-100999)]:
    db.execute('INSERT INTO goal_video_jobs VALUES (?,?,?,?,?,?,?)',(target,mid,'rostov',time.time(),time.time()+100,json.dumps({'goal':{'score':score}}),status))
  match=SimpleNamespace(id='rostov',status='finished')
  links=completed_goal_links(self.store,chat,[match])['rostov']
  self.assertEqual(links,'🎬 Голы: <a href="https://t.me/c/123456/12">2</a> · <a href="https://t.me/c/123456/13">3</a>')
  match.status='live';self.assertEqual(completed_goal_links(self.store,chat,[match]),{})
  match.status='finished';self.assertEqual(completed_goal_links(self.store,42,[match]),{})
if __name__=='__main__':unittest.main()
