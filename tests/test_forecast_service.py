import json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from datetime import date
from live_football import Match,Goal,FootballError
from football_predictions import Predictions
import forecast_service as service
from forecast_cycle import report

class ForecastTests(unittest.TestCase):
 def match(self,competition='Кубок России'):
  return Match('s365:123',competition,'Динамо Москва','Краснодар',2000000000,'scheduled','',None,None,'https://soccer365.ru/games/123/')
 def forecast(self,competition='Кубок России'):
  engine=Predictions(None,fetcher=lambda u: (_ for _ in ()).throw(FootballError('offline')),clock=lambda:1900000000)
  return service.build(engine,self.match(competition),model=lambda d:{'score':[2,1]})
 def test_all_competitions(self):
  for c in ['Чемпионат Англии','Чемпионат Испании','Чемпионат Италии','Чемпионат Франции','Чемпионат Германии','Премьер-лига России','Премьер-лига, 15-й тур','Кубок России','Лига чемпионов УЕФА','Лига Европы УЕФА','Лига наций УЕФА','Товарищеские матчи (сборные)','Чемпионат мира - Квалификация']:
   with self.subTest(c=c),patch.object(service,'national_estimate',return_value=None):
    r=self.forecast(c);self.assertEqual(r['score'],[2,1]);self.assertIn('90',str(service.render(r)));self.assertIsNone(r['probabilities'])
 def test_no_fake_probabilities_or_future(self):
  r=self.forecast();self.assertEqual(r['method'],'model_estimate')
  with self.assertRaises(ValueError):service.validate(dict(r,created=r['kickoff']))
  with self.assertRaises(ValueError):service.validate(dict(r,probabilities=[float('nan'),0,1]))
 def test_moscow_source_names(self):
  from soccer365_football import identity
  for name in ('ЦСКА','Спартак','Локомотив'):
   self.assertEqual(identity(name+' Москва'),identity(name))
  self.assertNotEqual(identity('Динамо Москва'),identity('Динамо Махачкала'))
 def test_unknown_and_youth(self):
  for c in ['Лига чемпионов женщины','Чемпионат Европы U21','Премьер-лига U-19','Клубный чемпионат мира']:
   self.assertIsNone(service.competition_kind(c))
 def test_immutable_and_conflict(self):
  r=self.forecast()
  with tempfile.TemporaryDirectory() as temp:
   self.assertEqual(service.register(r,temp),r)
   self.assertEqual(service.register(dict(r,score=[5,0]),temp),r)
   self.assertEqual(service.register(dict(r,match_id='fnk:2026:123'),temp),r)
   self.assertEqual(len(list((Path(temp)/'observations').glob('*.json'))),1)
   with self.assertRaises(ValueError):service.register(dict(r,home='Другой клуб'),temp)
   path=next((Path(temp)/'observations').glob('*.json'));data=json.loads(path.read_text());data['forecast']['score']=[9,0];path.write_text(json.dumps(data))
   with self.assertRaises(ValueError):service.read_observation(path)
 def test_result_regular_and_extra_time(self):
  r=self.forecast();m=self.match();goals=(Goal('1',(1,0),'45+2′','A','goal',''),Goal('2',(1,1),'90+5′','B','goal',''),Goal('3',(2,1),'110′','A','goal',''))
  final=replace(m,status='finished',home_score=2,away_score=1,goals=goals)
  with tempfile.TemporaryDirectory() as temp:
   service.register(r,temp)
   self.assertEqual(service.settle(r,final,temp,now=m.kickoff+10000),'recorded')
   self.assertEqual(service.settle(r,final,temp,now=m.kickoff+10000),'existing')
   saved=json.loads(next((Path(temp)/'results').glob('*.json')).read_text());self.assertEqual(saved['score_90'],[1,1])
   self.assertEqual(report(temp)['russian_cup']['settled'],1)
 def test_ambiguous_cup_and_postponement(self):
  r=self.forecast();m=replace(self.match(),status='finished',home_score=0,away_score=0)
  with tempfile.TemporaryDirectory() as temp:
   self.assertEqual(service.settle(r,m,temp,now=m.kickoff+10000),'regulation_unconfirmed')
   self.assertEqual(service.settle(r,replace(m,kickoff=m.kickoff+86400),temp,now=m.kickoff+10000),'schedule_changed')
   self.assertFalse((Path(temp)/'results').exists())
 def test_nations_no_target_or_future(self):
  rows=[dict(match_date='2026-01-01',home='France',away='England',home_goals=1,away_goals=0,neutral=True,id=i) for i in range(6)]
  with patch('football_nations_v10.load',return_value=rows),patch.object(Path,'exists',return_value=True):
   a=service.national_estimate('Франция','Англия',date(2026,2,1))
   self.assertIsNotNone(a)
   rows.append(dict(rows[0],match_date='2026-02-01',home_goals=20))
   b=service.national_estimate('Франция','Англия',date(2026,2,1))
   self.assertEqual(a,b)
if __name__=='__main__':unittest.main()
