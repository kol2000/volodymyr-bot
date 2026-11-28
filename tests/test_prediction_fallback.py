import json,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from datetime import datetime
from football_predictions import Predictions,request
from live_football import MOSCOW,FootballError
NOW=datetime(2026,11,20,21,53,tzinfo=MOSCOW).timestamp()
class FallbackTests(unittest.TestCase):
 def service(self,matches=None,fetcher=None):
  match=SimpleNamespace(id='bayern',competition='Чемпионат Германии',home='Бавария Мюнхен',away='Кёльн',status='scheduled',kickoff=NOW+2200)
  return Predictions(SimpleNamespace(source=SimpleNamespace(day=lambda d:([match] if matches is None else matches,NOW))),fetcher=fetcher or (lambda url:''),clock=lambda:NOW)
 def req(self,text='бубус дай прогноз на матч Бавария — Кельн'):
  return request(text,now=NOW,addressed=True)
 def test_screenshot_followup(self):
  req=self.req('Бубус вола не еби дай прогноз на Бавария Кельн')
  self.assertEqual(req['filter'],'Бавария Кельн')
  model=Mock(return_value='{"score":[3,1]}')
  out=self.service().answer(req,model)
  self.assertIn('3:1',out);self.assertEqual(req['_context']['match_id'],'bayern');model.assert_called_once()
 def test_unknown_clubs_still_route(self):
  self.assertEqual(self.req('бубус прогноз Мальме — Хеккен')['kind'],'football_prediction')
 def test_unlisted_pair(self):
  req=self.req('бубус прогноз Мальме — Хеккен')
  out=self.service([]).answer(req,lambda d:dict(home='Мальме',away='Хеккен',score=[2,1]))
  self.assertIn('2:1',out);self.assertIn('не подтверждени',out)
 def test_bad_model_neutral_score(self):
  for value in ('no json',None,dict(score=[99,-1]),dict(score=[True,2])):
   out=self.service().answer(self.req(),lambda d:value)
   self.assertIn('1:1',out);self.assertIn('базовий',out)
 def test_stats_failure(self):
  def fail(url):raise FootballError('offline')
  out=self.service(fetcher=fail).answer(self.req(),lambda d:dict(score=[2,0]))
  self.assertIn('2:0',out)
 def test_no_fabricated_pair(self):
  out=self.service([]).answer(self.req('бубус дай прогноз'),lambda d:dict(home='',away='',score=[1,1]))
  self.assertIn('обе команди',out)
 def test_model_cannot_change_verified_teams(self):
  out=self.service().answer(self.req(),lambda d:dict(home='Иное',away='Нечто',score=[2,0]))
  self.assertNotIn('Иное',out);self.assertIn('Бавария',out)
 def test_too_few_results_use_model(self):
  service=self.service()
  with patch('football_predictions.team_index',return_value={'бавария мюнхен':'https://example/home','кельн':'https://example/away'}),patch('football_predictions.results',return_value=[]):
   out=service.answer(self.req(),lambda d:dict(score=[3,0]))
  self.assertIn('3:0',out)
 def test_schedule_unavailable(self):
  service=self.service()
  service.football.source.day=Mock(side_effect=FootballError('offline'))
  out=service.answer(self.req(),lambda d:dict(home='Бавария',away='Кёльн',score=[2,1]))
  self.assertIn('2:1',out);self.assertIn('не подтверждени',out)
 def test_live_label_and_finished_not_predicted(self):
  match=SimpleNamespace(id='bayern',competition='Чемпионат Германии',home='Бавария',away='Кёльн',status='live',kickoff=NOW-60)
  model=Mock(return_value=dict(score=[2,1]))
  out=self.service([match]).answer(self.req(),model)
  self.assertIn('по ходу матча',out)
  match.status='finished';model.reset_mock()
  self.assertIn('завершён',self.service([match]).answer(self.req(),model));model.assert_not_called()
if __name__=='__main__':unittest.main()
