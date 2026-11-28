import json
import unittest
from pathlib import Path
from datetime import date,datetime
from types import SimpleNamespace
from unittest.mock import Mock,patch

from football_predictions import request,followup,results,estimate,Predictions,team_index,BASE
from live_football import MOSCOW,FootballError,select_matches
from test_bot import BotTests

DAY=date(2026,10,27)
NOW=datetime(2026,10,27,18,0,tzinfo=MOSCOW).timestamp()


class PredictionTests(unittest.TestCase):
    def test_screenshot_requests_find_schedule_match(self):
        match=SimpleNamespace(id='europe',home='Манчестер Юнайтед',away='Рома',competition='Лига чемпионов')
        for phrase in ('Дай предсказание на матч МЮ Рома со счетом','МЮ Рома дай предсказание на матч по счету'):
            req=request(phrase,addressed=True,now=NOW)
            self.assertEqual(select_matches([match],req['filter']),[match])
        req=request('бубус дай прогнозы на каждый сегодняшний матч дня',addressed=True,now=NOW)
        self.assertTrue(req['all_matches'])
        self.assertEqual(req['filter'],'')
        pair=request('Шахта или Спортинг?',addressed=True,now=NOW)
        self.assertEqual(pair['kind'],'football_prediction')
        context=dict(day='2026-10-27',filter='Шахтер Спортинг',match_id='pair')
        for phrase in ('А победит кто?','скажи ставить на кого'):
            self.assertEqual(request(phrase,addressed=True,context=context,now=NOW)['match_id'],'pair')

    def test_international_results_and_all_matches(self):
        html=''.join(f'<tr><td>{i}</td><td>{i}.10.2026</td><td><a href="x-obzor-matcha-england-2027.htm">Манчестер Юнайтед - Рома - 2:1</a></td><td></td></tr>' for i in range(1,7))
        self.assertEqual(len(results(html,'Манчестер Юнайтед',DAY)),6)
        self.assertEqual(results(html.replace('obzor','online'),'Манчестер Юнайтед',DAY),[])
        self.assertEqual(len(team_index('<a href="/england/2027/teams/manutd.htm">Манчестер Юнайтед</a>',2027)),1)
        matches=[SimpleNamespace(id=str(i),home='Манчестер Юнайтед',away='Рома',competition='Лига чемпионов',status='scheduled',kickoff=NOW+3000) for i in range(2)]
        service=Predictions(SimpleNamespace(source=SimpleNamespace(day=lambda d:(matches,NOW))),clock=lambda:NOW)
        # Batch delegates each exact id, never asks the language model to pick winners.
        original=service.answer
        with patch.object(service,'answer',side_effect=lambda req:'⚽ <b>Пара</b>\nМой вариант\n50% · 20% · 30%\nСчёт 2:1' if not req.get('all_matches') else original(req)):
            answer=service.answer(dict(day=DAY.isoformat(),filter='',all_matches=True))
        self.assertEqual(answer.count('Счёт 2:1'),2)

    def test_cup_request_from_chat_keeps_competition_and_finds_match(self):
        now=datetime(2026,10,28,15,4,tzinfo=MOSCOW).timestamp()
        cup=SimpleNamespace(id='cup',home='Спартак',away='Рубин',
            competition='Кубок России. Путь РПЛ. Групповой этап, 5-й тур',
            status='scheduled',kickoff=now+19000)
        league=SimpleNamespace(id='league',home='Спартак',away='Рубин',competition='Премьер-лига')
        for phrase in ('дай прогноз на матч кубка СПАРТАК - Рубин',
                       'дай прогноз на матч Кубка России Спартак — Рубин сегодня',
                       'прогноз Спартак Рубин в кубке сегодня'):
            req=request(phrase,addressed=True,now=now)
            self.assertEqual(req['day'],'2026-10-28')
            self.assertEqual(select_matches([cup,league],req['filter']),[cup])
            service=Predictions(SimpleNamespace(source=SimpleNamespace(day=lambda day:([cup,league],now))),
                                fetcher=lambda url:'',clock=lambda:now)
            self.assertNotIn('не нашёл',service.answer(req))
            self.assertEqual(req['_context']['match_id'],'cup')
        self.assertEqual(select_matches([cup],'кубка Спартак Зенит'),[])
        self.assertEqual(select_matches([cup],'кубка Англии Спартак Рубин'),[])

    def setUp(self):
        self.data=json.loads((Path(__file__).parent/'fixtures/prediction_results_20261027.json').read_text(encoding='utf-8'))

    def test_real_results_exclude_scheduled_and_future(self):
        rows=results(self.data['krasnodar'],'Краснодар',DAY)
        self.assertEqual(len(rows),8)
        self.assertEqual(rows[0]['day'],'2026-10-24')
        self.assertEqual(rows[0]['score'],(1,1))
        self.assertTrue(all(r['day']<'2026-10-27' for r in rows))
        earlier=results(self.data['krasnodar'],'Краснодар',date(2026,10,10))
        self.assertTrue(all(r['day']<'2026-10-10' for r in earlier))

    def test_probabilities_sum_to_100_and_are_reproducible(self):
        h=results(self.data['krasnodar'],'Краснодар',DAY)
        a=results(self.data['dinamo'],'Динамо',DAY)
        value=estimate('Краснодар','Динамо',h,a)
        self.assertEqual(sum(value['probabilities']),100)
        self.assertEqual(value,estimate('Краснодар','Динамо',h,a))
        self.assertTrue(all(0<=p<=100 for p in value['probabilities']))

    def test_no_probability_with_small_sample(self):
        with self.assertRaises(FootballError): estimate('a','b',[],[])

    def test_live_rows_and_same_day_are_excluded(self):
        html=self.data['krasnodar'].replace('obzor-matcha-ruschamp','online-matcha-ruschamp')
        self.assertEqual(results(html,'Краснодар',DAY),[])
        cutoff=date(2026,10,24)
        self.assertTrue(all(r['day']<'2026-10-24' for r in results(self.data['krasnodar'],'Краснодар',cutoff)))

    def test_direct_requests_and_weather_negative(self):
        for text in ('Краснодар Динамо Москва дай предсказание на матч','прогноз на Краснодар Динамо','краснодар, динамо или ничья?'):
            value=request(text,addressed=True,now=NOW)
            self.assertEqual(value['kind'],'football_prediction')
            self.assertEqual(value['filter'].casefold().replace(',',''),'краснодар динамо')
        self.assertIsNone(request('прогноз погоды Москва',addressed=True))
        self.assertIsNone(request('Краснодар Динамо прогноз',addressed=False))
        self.assertEqual(request('/predict Краснодар Динамо завтра','/predict',now=NOW)['day'],'2026-10-28')

    def test_followups_keep_exact_match_not_other_conversation(self):
        context=dict(day='2026-10-27',filter='Краснодар Динамо',match_id='one')
        for text in ('А счёт?','Например?','А в этом матче что','краснодар, динамо или ничья?'):
            self.assertTrue(followup(text))
            self.assertEqual(request(text,addressed=True,context=context,now=NOW)['match_id'],'one')
        self.assertIsNone(request('почему небо синее',addressed=True,context=context))
        self.assertIsNone(request('А счёт?',addressed=True))

    def test_index_identity_and_full_answer(self):
        index=''.join(f'<a href="/ruschamp/2027/teams/{slug}.htm">{team}</a>' for slug,team in [('krasnodar','Краснодар'),('dinamo','Динамо-Москва')])
        self.assertEqual(len(team_index(index,2027)),2)
        match=SimpleNamespace(id='one',home='Краснодар',away='Динамо',competition='Кубок России',status='scheduled',kickoff=NOW+9000)
        source=SimpleNamespace(day=lambda day:([match],NOW))
        fetcher=Mock(side_effect=lambda url:index if url==BASE+'/ruschamp/' else self.data['krasnodar' if 'krasnodar' in url else 'dinamo'])
        service=Predictions(SimpleNamespace(source=source),fetcher=fetcher,clock=lambda:NOW)
        req=request('прогноз Краснодар Динамо',addressed=True,now=NOW)
        answer=service.answer(req)
        self.assertIn('Мой вариант по исходу:',answer)
        self.assertEqual(answer.count('%'),3)
        self.assertIn('травми',answer)
        self.assertNotIn('ы',answer)
        self.assertEqual(req['_context']['match_id'],'one')
        self.assertEqual(service.answer(req),answer)
        self.assertEqual(fetcher.call_count,3)
        match.status='live'
        self.assertNotIn('%',service.answer(req))
        self.assertEqual(fetcher.call_count,3)


class PredictionRoutingTests(unittest.TestCase):
    setUp=BotTests.setUp
    tearDown=BotTests.tearDown
    update=BotTests.update

    def test_nickname_batch_goes_to_predictions_not_language_model(self):
        self.store.set('chat_id',-100)
        self.bot.handle(self.update('бубус дай прогнозы на каждый сегодняшний матч дня',number=920))
        utility=self.bot.utility_jobs.get_nowait()['utility']
        self.assertEqual(utility['kind'],'football_prediction')
        self.assertTrue(utility['all_matches'])
        self.assertTrue(self.bot.jobs.empty())

    def test_command_uses_utility_queue(self):
        self.store.set('chat_id',-100)
        self.bot.handle(self.update('/predict Краснодар Динамо',number=901))
        self.assertEqual(self.bot.utility_jobs.get_nowait()['utility']['kind'],'football_prediction')
        self.assertTrue(self.bot.jobs.empty())

    def test_same_user_followup_keeps_context(self):
        self.store.set('chat_id',-100)
        self.store.add_message(dict(chat=dict(id=-100),message_id=500,text='Прогноз',date=NOW,_answer_mode='football_prediction'),human=False)
        self.store.set('prediction:context:-100:500',dict(day='2026-10-27',filter='Краснодар Динамо',match_id='one'))
        self.store.remember_session(-100,42,500)
        self.bot.handle(self.update('А счёт?',number=902))
        self.assertEqual(self.bot.utility_jobs.get_nowait()['utility']['match_id'],'one')
        self.bot.handle(self.update('А счёт?',user=7,number=903))
        self.assertTrue(self.bot.utility_jobs.empty())

    def test_sent_prediction_remembers_context_without_model(self):
        self.store.set('chat_id',-100)
        self.bot.handle(self.update('/predict Краснодар Динамо',number=910))
        job=self.bot.utility_jobs.get_nowait()
        def answer(req, model=None):
            req['_context']=dict(day='2026-10-27',filter='Краснодар Динамо',match_id='one')
            return 'Прогноз: ничья. Вероятность 30%.'
        with patch.object(self.bot.predictions,'answer',side_effect=answer):
            self.bot.process_job(job)
        self.assertEqual(len(self.telegram.sent),1)
        self.assertEqual(self.store.answer_mode(-100,10001),'football_prediction')
        self.assertEqual(self.store.get('prediction:context:-100:10001')['match_id'],'one')
