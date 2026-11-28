import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import test_forecast_service as fixtures
from forecast_service import checksum, identity, write_once
from forecast_weekly import MOSCOW, period, publish, render, summarize


class WeeklyTests(unittest.TestCase):
    def test_calendar_and_catchup(self):
        start, end = period(datetime(2026, 11, 30, 10, tzinfo=MOSCOW))
        self.assertEqual(start.date().isoformat(), '2026-11-23')
        self.assertEqual(end.date().isoformat(), '2026-11-30')
        self.assertEqual(period(datetime(2026, 12, 2, tzinfo=MOSCOW)), (start, end))
        self.assertEqual(period(datetime(2026, 11, 30, 9, tzinfo=MOSCOW))[1], start)

    def test_metrics_boundaries_and_pending(self):
        start, end = period(datetime(2026, 11, 30, 10, tzinfo=MOSCOW))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, kickoff in enumerate([start.timestamp()-1, start.timestamp(), end.timestamp()-1, end.timestamp()]):
                r = fixtures.ForecastTests().forecast()
                r.update(match_id=f's365:{i}', kickoff=kickoff, created=kickoff-3600)
                write_once(root/'observations'/f'{i}.json', dict(forecast=r, sha256=checksum(r)))
                if i == 1:
                    path = root/'results'/(identity(r)+'.json')
                    write_once(path, dict(forecast_sha256=checksum(r), match_id=r['match_id'], competition=r['competition'], actual=0, score_90=[2,1]))
            groups = summarize(root, start, end)
            self.assertEqual(groups['russian_cup'], dict(total=2, settled=1, correct=1, exact=1))
            self.assertIn('Ждут подтверждения: 1', render(groups, start, end))
            value = json.loads(path.read_text()); value['actual'] = 2
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): summarize(root, start, end)

    def test_empty(self):
        start, end = period(datetime(2026, 11, 30, 10, tzinfo=MOSCOW))
        text = render({}, start, end)
        self.assertIn('Прогнозов: 0', text)
        self.assertNotIn('100%', text)

    def test_send_once_and_uncertain(self):
        start = datetime(2026,11,23,tzinfo=MOSCOW)
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def send(text):
                calls.append(text)
                return {'message_id': 12}
            self.assertEqual(publish(tmp,start,'test',send),'sent')
            self.assertEqual(publish(tmp,start,'test',send),'already_attempted')
            self.assertEqual(len(calls),1)
            def timeout(text): raise TimeoutError()
            later = start + timedelta(days=7)
            with self.assertRaises(TimeoutError): publish(tmp,later,'test',timeout)
            self.assertEqual(publish(tmp,later,'test',send),'already_attempted')
            self.assertEqual(len(calls),1)


if __name__ == '__main__': unittest.main()
