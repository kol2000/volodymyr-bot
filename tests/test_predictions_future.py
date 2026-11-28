import sys,unittest
from pathlib import Path
from datetime import datetime,date
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import soccer365_football as s
import football_predictions as p
from live_football import MOSCOW,FootballError
NOW=datetime(2026,11,19,15,tzinfo=MOSCOW).timestamp()
FIX=Path(__file__).parent/'fixtures'/'prediction_future_20261119'
class FutureTests(unittest.TestCase):
 def test_date_words_and_reply(self):
  context=dict(day='2026-11-19',filter='Ливерпуль МЮ',match_id=None)
  for text in ('22 ноября','22 ноября Ливерпуль - Манчестер Юнайтед','дай прогноз Ливерпуль МЮ 22 ноября'):
   r=p.request(text,addressed=True,context=context,now=NOW)
   self.assertEqual(r['day'],'2026-11-22');self.assertTrue(r['explicit_day']);self.assertNotIn('ноября',r['filter'])
  self.assertEqual(p.request('31 февраля',addressed=True,context=context,now=NOW)['kind'],'fixed')
 def test_real_calendar_future_lookup_read_only(self):
  calls=[]
  def fetch(url):
   calls.append(url)
   if '/online/' in url:return (FIX/'2026-11-22.html').read_text()
   return (FIX/'liverpool.html').read_text()
  source=s.SupplementedScoreboard(clock=lambda:NOW,fetcher=fetch)
  rows=source.prediction_matches(date(2026,11,22),'Ливерпуль МЮ')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0].away,'Манчестер Юнайтед')
  self.assertEqual(rows[0].status,'scheduled');self.assertEqual(source.matches,{})
  self.assertEqual(source.selected,[])
  self.assertIn('&date=2026-11-22',calls[0])
 def test_upcoming_search_and_explicit_day_no_shift(self):
  from unittest.mock import Mock
  m=SimpleNamespace(id='x',home='Ливерпуль',away='Манчестер Юнайтед',competition='Чемпионат Англии',status='scheduled',kickoff=NOW+3*86400)
  source=SimpleNamespace(prediction_matches=Mock(side_effect=lambda d,q:[m] if d==date(2026,11,22) else []))
  service=p.Predictions(SimpleNamespace(source=source),fetcher=lambda u:'',clock=lambda:NOW)
  req=p.request('Ливерпуль или МЮ?',addressed=True,now=NOW)
  service.answer(req);self.assertEqual(req['_context']['match_id'],'x');self.assertEqual(req['day'],'2026-11-22')
  source.prediction_matches.reset_mock()
  req=p.request('прогноз Ливерпуль МЮ завтра',addressed=True,now=NOW)
  service.answer(req);self.assertEqual(source.prediction_matches.call_count,1);self.assertEqual(req['day'],'2026-11-20')
 def test_redirect_only_same_source_valid_paths(self):
  self.assertTrue(s.valid_url('https://soccer365.ru/live/game-manchester-yunayted-liverpul-smotret-onlayn/'))
  self.assertFalse(s.valid_url('https://other.invalid/live/game-foo/'))
if __name__=='__main__':
 suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(FutureTests)])
 for name in ['test_football_predictions.PredictionTests','test_football_predictions.PredictionRoutingTests','test_soccer365.SoccerTests','test_midnight_football.MidnightTests']:
  suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
 r=unittest.TextTestRunner().run(suite);sys.exit(not r.wasSuccessful())
