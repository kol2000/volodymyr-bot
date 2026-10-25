import dataclasses
import json
import unittest
from pathlib import Path
from goal_videos import Clip, matches_goal


class RPLHistoryTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((Path(__file__).parent/'fixtures/matchpremier_history_20261025.json').read_text(encoding='utf-8'))
        self.jobs=self.data['jobs']
        self.clips={p['post_id']:Clip('matchpremier',p['post_id'],p['timestamp'],p['text'],
                    'https://cdn4.telesco.pe/file/fixture.mp4') for p in self.data['posts']}

    def test_all_27_public_posts_match_only_the_14_corresponding_goals(self):
        expected={30100:'Дмитрий Рыбчинский',30103:'Артем Шуманский',30104:'Ярослав Гладышев',
                  30105:'Кристиян Бистрович',30107:'Максим Болдырев',30110:'Герман Онугха',
                  30114:'Игор Вуячич',30115:'Амар Рахманович',30117:'Джон Кордоба',
                  30119:'Лорис Муйоколо',30124:'Андрей Лангович',30125:'Олакунле Олусегун',
                  30134:'Кирилл Глебов',30137:'Тамерлан Мусаев'}
        for number,clip in self.clips.items():
            found=[j['goal']['player'] for j in self.jobs if matches_goal(clip,j,clip.timestamp+600)]
            self.assertEqual(found,[expected[number]] if number in expected else [],number)

    def test_observed_time_does_not_override_negative_or_conflicting_evidence(self):
        clip=self.clips[30137]
        job=next(j for j in self.jobs if j['goal']['player']=='Тамерлан Мусаев')
        for text in ['Мусаев спорит с судьей.', 'Мусаев почти вонзил после углового.',
                     'Повтор: Мусаев вонзил после углового.', 'Мусаев вонзил, но гол не засчитали.',
                     'Мусаев не вонзил после углового.', 'Мусаев вонзил! Три ноль!',
                     'Мусаев вонзил! 3:0!', 'Мусаев вонзил на 70′!', 'Мусаев вонзил в штангу.']:
            self.assertFalse(matches_goal(dataclasses.replace(clip,text=text),job,clip.timestamp+600),text)
        for delta in [-181,301,900]:
            self.assertFalse(matches_goal(dataclasses.replace(clip,timestamp=job['created']+delta),job,clip.timestamp+1200))
        for change in [dict(match_goals=[]),dict(match_score=[3,0]),dict(competition='Первая лига')]:
            self.assertFalse(matches_goal(clip,dict(job,**change),clip.timestamp+600))
        self.assertFalse(matches_goal(dataclasses.replace(clip,channel='idmanvideo'),job,clip.timestamp+600))

    def test_same_scorer_twice_requires_disambiguation_even_if_only_one_card_pending(self):
        clip=dataclasses.replace(self.clips[30137],text='Мусаев вонзил после углового!')
        job=next(j for j in self.jobs if j['goal']['player']=='Тамерлан Мусаев')
        later=dict(job['goal'],score=[3,0],minute='25′')
        changed=dict(job,match_goals=[*job['match_goals'],later],match_score=[3,0])
        self.assertFalse(matches_goal(clip,changed,clip.timestamp+600))
