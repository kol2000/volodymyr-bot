import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch,Mock
import football_predictions as p
from live_football import MOSCOW
class CrossSourceTests(unittest.TestCase):
 def test_id_translation_requires_unique_pair_date_and_other_source(self):
  now=datetime(2026,11,28,10,tzinfo=MOSCOW).timestamp()
  m=SimpleNamespace(id='s365:2478724',home='ЦСКА Москва',away='Зенит',kickoff=now+3600,status='scheduled',competition='Премьер-лига России')
  for matches,mid,day,ok in [([m],'fnk:2026:25971','2026-11-28',True),([m],'s365:wrong','2026-11-28',False),([m,m],'fnk:2026:25971','2026-11-28',False),([m],'fnk:2026:25971','2026-11-29',False)]:
   service=p.Predictions(SimpleNamespace(source=SimpleNamespace(prediction_matches=lambda d,q:matches)),clock=lambda:now)
   req=dict(day=day,filter='ЦСКА — Зенит',match_id=mid)
   with patch('forecast_service.build',return_value={}) as build,patch('forecast_service.register'),patch('forecast_service.render',return_value='verified'),patch.object(service,'fallback',return_value='fallback'):
    self.assertEqual(service.answer(req),'verified' if ok else 'fallback');self.assertEqual(build.called,ok)
 def test_stats_names(self):
  self.assertEqual(p.canonical('ЦСКА Москва'),p.canonical('ЦСКА'))
  self.assertNotEqual(p.canonical('ЦСКА София'),p.canonical('ЦСКА'))
 def test_model_prompt_has_no_sample_score(self):
  import test_bot
  f=test_bot.BotTests();f.setUp()
  try:
   with patch.object(f.bot,'call_model',return_value={'message':{'content':'{"score":[0,3]}'}}) as call:
    self.assertIn('0,3',f.bot.prediction_model({},{}))
    prompt=call.call_args.args[1]['messages'][0]['content']
    self.assertNotIn('[2,1]',prompt);self.assertIn('массив из двух целых чисел',prompt)
  finally:f.tearDown()

 def test_statistical_score_matches_selected_outcome(self):
  for hs,aws in [((2,1),(3,1)),((1,3),(2,1)),((1,1),(1,1))]:
   home=[dict(home='A',away='X',score=hs) for _ in range(8)]
   away=[dict(home='B',away='X',score=aws) for _ in range(8)]
   r=p.estimate('A','B',home,away);h,a=r['score']
   self.assertEqual(0 if h>a else 2 if h<a else 1,r['winner'])
   self.assertEqual(sum(r['probabilities']),100)
