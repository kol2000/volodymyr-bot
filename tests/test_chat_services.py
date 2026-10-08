"""No live requests: exercise parsing, source freshness and real calculations."""
import io
import sys
import time
import unittest
import urllib.error
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chat_services import (ChatServices, MOSCOW, ServiceError, amounts_in,
                           get_data, service_request)
from common import HTMLMessage


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatServices()
        self.calls = []
        self.today = datetime.now(MOSCOW).strftime('%d.%m.%Y')
        self.place = {'id': 515012, 'name': 'Орёл', 'latitude': 52.96879,
                      'longitude': 36.0791, 'country': 'Россия', 'country_code': 'RU',
                      'feature_code': 'PPLA', 'admin1': 'Орловская область', 'population': 303696,
                      'timezone': 'Europe/Moscow'}
        self.current_time = datetime.now(MOSCOW).replace(second=0, microsecond=0).isoformat()[:16]
        self.weather_data = {'utc_offset_seconds': 10800, 'timezone': 'Europe/Moscow',
                             'current_units': {'temperature_2m': '°C', 'wind_speed_10m': 'm/s', 'surface_pressure': 'hPa'},
                             'current': {'time': self.current_time, 'interval': 900,
                                         'temperature_2m': -2.5, 'apparent_temperature': -6.1,
                                         'relative_humidity_2m': 82, 'weather_code': 71,
                                         'precipitation': 0.3, 'wind_speed_10m': 4.2, 'surface_pressure': 1013.25},
                             'daily': {'time': [datetime.now(MOSCOW).date().isoformat()],
                                       'sunrise': [datetime.now(MOSCOW).strftime('%Y-%m-%dT07:05')],
                                       'sunset': [datetime.now(MOSCOW).strftime('%Y-%m-%dT18:15')]}}

    def fetch(self, url, xml=False):
        self.calls.append(url)
        if 'cbr.ru/' in url:
            return ET.fromstring(f'<ValCurs Date="{self.today}">'
                                 '<Valute><CharCode>USD</CharCode><Nominal>10</Nominal><Value>845,0000</Value></Valute>'
                                 '<Valute><CharCode>UAH</CharCode><Nominal>10</Nominal><Value>20,0000</Value></Valute>'
                                 '<Valute><CharCode>BYN</CharCode><Nominal>3</Nominal><Value>84,0000</Value></Valute>'
                                 '</ValCurs>')
        if 'coingecko.com/' in url:
            ids = parse_qs(urlparse(url).query)['ids'][0].split(',')
            return {coin: {'usd': 0.999 if coin == 'tether' else 2000,
                           'last_updated_at': int(time.time())} for coin in ids}
        if 'geocoding-api.' in url:
            return {'results': [self.place]}
        return self.weather_data

    def test_amount_formats_aliases_and_three_unique_limit(self):
        cases = {'100 USDT': ('100', 'USDT'), '0,25 btc': ('0.25', 'BTC'),
                 '1 000,50 рублей': ('1000.50', 'RUB'), '1\u00a0000 USD': ('1000', 'USD'),
                 '$50.25': ('50.25', 'USD'), '₽ 8450': ('8450', 'RUB'),
                 '100 долларов': ('100', 'USD'), '10 баксов': ('10', 'USD')}
        for text, (amount, unit) in cases.items():
            with self.subTest(text=text):
                self.assertEqual(amounts_in(text), [{'amount': amount, 'unit': unit}])
        self.assertEqual(len(amounts_in('100 USD, 100 USD, 1 BTC, 2 ETH, 3 BNB')), 3)

    def test_hryvnia_and_belarusian_ruble_aliases_normalize_to_codes(self):
        cases = {'80 гривен': ('80', 'UAH'), '80грн': ('80', 'UAH'),
                 '80 ГРН.': ('80', 'UAH'), '80 uah': ('80', 'UAH'),
                 '80 гривень': ('80', 'UAH'), '2 гривні': ('2', 'UAH'),
                 '1 гривня': ('1', 'UAH'), '1 гривна': ('1', 'UAH'),
                 '2 гривны': ('2', 'UAH'), '₴80,50': ('80.50', 'UAH'),
                 '80 ₴': ('80', 'UAH'), '100 byn': ('100', 'BYN'),
                 '100 белорусских рублей': ('100', 'BYN'),
                 '2 белорусских рубля': ('2', 'BYN'), '1 белорусский рубль': ('1', 'BYN'),
                 '1 234,50 бел. руб.': ('1234.50', 'BYN'), '100 белруб': ('100', 'BYN')}
        for text, (amount, unit) in cases.items():
            with self.subTest(text=text):
                self.assertEqual(amounts_in(text), [{'amount': amount, 'unit': unit}])
        self.assertEqual(amounts_in('80 UAH, 80 гривен, 80 грн'), [{'amount': '80', 'unit': 'UAH'}])

    def test_non_amounts_and_url_wallet_fragments_do_not_activate(self):
        for text in ('100 USDTABC', 'x100 USD', '-100 USD', '+100 USD', '0 USDT',
                     '999999999999999 USD', '100 USD_foo', '123.45.67 USD',
                     'https://example.test/100USD', '@100USD', '0x100BTC',
                     '80 UAHABC', '80 byn_foo', '80 гривенник', '-80 гривен',
                     'https://example.test/80UAH', '@100BYN', '80 гр', '80 белорусских рублейных'):
            with self.subTest(text=text):
                self.assertEqual(amounts_in(text), [])

    def test_weather_request_city_normalization_and_missing_city(self):
        for text in ('Володька, какая погода в городе Орёл', 'погода в Орле?',
                     'погода Орел', 'Володька, погода в Орле сегодня'):
            with self.subTest(text=text):
                self.assertEqual(service_request(text), {'kind': 'weather', 'city': 'Орёл'})
        self.assertEqual(service_request('/weather Орёл', '/weather')['city'], 'Орёл')
        self.assertEqual(service_request('какая погода')['error'], 'city_missing')
        self.assertEqual(service_request('погода в Орле завтра')['error'], 'forecast_unsupported')
        self.assertIsNone(service_request('погодка сегодня ужасная'))
        self.assertIsNone(service_request('как там бубус'))

    def test_usd_rub_both_directions_use_cbr_nominal_and_date(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('100 USD и 8450 рублей'))
        self.assertIn('💱 <b>100,00 USD</b>\n💰 <b>≈ 8 450,00 RUB</b>', text)
        self.assertIn('💱 <b>8 450,00 RUB</b>\n💵 <b>≈ 100,00 USD</b>', text)
        self.assertIn('курс ЦБ на ' + self.today, text)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('date_req=', self.calls[0])

    def test_usdt_is_not_assumed_equal_to_usd_and_sources_are_separate(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('100 USDT'))
        self.assertIn('💱 <b>100 USDT</b>\n💵 <b>≈ 99,90 USD</b>\n💰 <b>≈ 8 441,55 RUB</b>', text)
        self.assertIn('CoinGecko:', text)
        self.assertIn('RUB: курс ЦБ на', text)
        crypto_url = next(u for u in self.calls if 'coingecko' in u)
        self.assertNotIn('100', crypto_url)
        self.assertEqual(parse_qs(urlparse(crypto_url).query)['ids'], ['tether'])

    def test_hryvnia_and_byn_convert_to_usd_rub_with_each_cbr_nominal(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('80 гривен и 100 byn'))
        self.assertIn('💱 <b>80,00 UAH</b>\n💵 <b>≈ 1,89 USD</b>\n💰 <b>≈ 160,00 RUB</b>', text)
        self.assertIn('💱 <b>100,00 BYN</b>\n💵 <b>≈ 33,14 USD</b>\n💰 <b>≈ 2 800,00 RUB</b>', text)
        self.assertIn('курс ЦБ на ' + self.today, text)
        self.assertNotIn('CoinGecko', text)
        self.assertEqual(len(self.calls), 1)

    def test_missing_or_invalid_fiat_rates_are_not_replaced_with_invented_values(self):
        for unit in ('UAH', 'BYN'):
            for nominal, value in ((None, None), ('0', '20,00'), ('10', 'NaN'), ('10', '-20,00')):
                root = ET.fromstring(f'<ValCurs Date="{self.today}">'
                                     '<Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>84,50</Value></Valute>'
                                     '</ValCurs>')
                if nominal is not None:
                    row = ET.SubElement(root, 'Valute')
                    for key, content in (('CharCode', unit), ('Nominal', nominal), ('Value', value)):
                        ET.SubElement(row, key).text = content
                with self.subTest(unit=unit, nominal=nominal, value=value), patch('chat_services.get_data', return_value=root):
                    self.client.cache.clear()
                    with self.assertRaises(ServiceError):
                        self.client.answer(service_request('80 ' + unit))

    def test_mixed_fiat_crypto_requests_share_cbr_cache_and_preserve_old_conversions(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('80 грн, 100 BYN, 100 USDT'))
            again = self.client.answer(service_request('100 USD'))
        self.assertIn('<b>≈ 160,00 RUB</b>', text)
        self.assertIn('<b>≈ 2 800,00 RUB</b>', text)
        self.assertIn('<b>≈ 8 441,55 RUB</b>', text)
        self.assertIn('<b>≈ 8 450,00 RUB</b>', again)
        self.assertIn('CoinGecko:', text)
        self.assertEqual(len(self.calls), 2)

    def test_small_crypto_amount_keeps_source_precision(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('0.00000001 BTC'))
        self.assertIn('<b>0,00000001 BTC</b>', text)
        self.assertIn('<b>≈ 0,00002000 USD</b>', text)

    def test_conversion_groups_thousands_and_separates_amount_blocks(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('1234,5 USDT и 100 USD'))
        self.assertIsInstance(text, HTMLMessage)
        self.assertIn('💱 <b>1 234,5 USDT</b>\n💵 <b>≈ 1 233,27 USD</b>', text)
        self.assertIn('\n\n💱 <b>100,00 USD</b>\n', text)
        self.assertIn('\n\n<i>CoinGecko:', text)

    def test_cache_limits_calls_and_refetches_after_expiration(self):
        now = time.time()
        request = service_request('100 USDT')
        with patch('chat_services.get_data', side_effect=self.fetch), patch('chat_services.time.time', return_value=now):
            self.client.answer(request)
            self.client.answer(request)
        self.assertEqual(len(self.calls), 2)
        with patch('chat_services.get_data', side_effect=self.fetch), patch('chat_services.time.time', return_value=now + 61):
            self.client.answer(request)
        self.assertEqual(len(self.calls), 3)

    def test_stale_or_invalid_crypto_prices_never_get_used(self):
        for row in ({'usd': 1, 'last_updated_at': time.time() - 901},
                    {'usd': 1, 'last_updated_at': time.time() + 61},
                    {'usd': None, 'last_updated_at': time.time()},
                    {'usd': True, 'last_updated_at': time.time()},
                    {'usd': 'NaN', 'last_updated_at': time.time()}):
            with self.subTest(row=row), patch('chat_services.get_data', return_value={'tether': row}):
                self.client.cache.clear()
                with self.assertRaises(ServiceError):
                    self.client.crypto({'USDT'})

    def test_cbr_rejects_stale_future_or_missing_usd_data(self):
        today = datetime.now(MOSCOW).date()
        for day in (today - timedelta(days=15), today + timedelta(days=1)):
            root = ET.fromstring(f'<ValCurs Date="{day:%d.%m.%Y}"/>')
            with patch('chat_services.get_data', return_value=root):
                with self.assertRaisesRegex(ServiceError, 'stale_rates'):
                    self.client.cbr()
        with patch('chat_services.get_data', return_value=ET.fromstring(f'<ValCurs Date="{self.today}"/>')):
            with self.assertRaisesRegex(ServiceError, 'missing_usd_rate'):
                self.client.cbr()

    def test_weather_correct_units_location_time_and_no_model_data(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('Володька, какая погода в городе Орёл'))
        self.assertIn('Сечас в городе <b>Орёл</b>:', text)
        self.assertIn('Орловская область, Россия', text)
        self.assertIn('🌨️ слабый снег', text)
        self.assertIn('<i>Температура воздуха</i> — -2.5 °C', text)
        self.assertIn('<i>Чувствуется как</i> — -6.1 °C', text)
        self.assertIn('<i>Ветер</i> — 4.2 м/с', text)
        self.assertIn('<i>Влажность</i> — 82%', text)
        self.assertIn('<i>Атмосферное давление</i> — 760 мм рт. ст.', text)
        self.assertIn('<i>Рассвет</i> в 07:05', text)
        self.assertIn('<i>Закат</i> в 18:15', text)
        self.assertIn('<i>Осадки</i> — 0.3 мм за последние 15 мин.', text)
        self.assertIn('Open-Meteo', text)
        self.assertIn('Europe/Moscow', text)
        geocode = parse_qs(urlparse(self.calls[0]).query)
        self.assertEqual(geocode['name'], ['Орёл'])
        self.assertEqual(geocode['countryCode'], ['RU'])
        self.assertNotIn('Володька', self.calls[0])
        self.assertIn('wind_speed_unit=ms', self.calls[1])
        self.assertIsInstance(text, HTMLMessage)

    def test_weather_external_names_are_html_escaped(self):
        self.place.update(name='Город <центр> & окрестности', admin1='Область <i>важно</i>')
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('погода Орёл'))
        self.assertIn('<b>Город &lt;центр&gt; &amp; окрестности</b>', text)
        self.assertIn('Область &lt;i&gt;важно&lt;/i&gt;', text)
        self.assertNotIn('<центр>', text)

    def test_sun_times_reject_wrong_day_and_do_not_invent_missing_events(self):
        today = datetime.now(MOSCOW).date().isoformat()
        previous = (datetime.now(MOSCOW).date() - timedelta(days=1)).isoformat()
        for daily in ({'time': [previous], 'sunrise': [previous + 'T07:05'], 'sunset': [previous + 'T18:15']},
                      {'time': [today], 'sunrise': [previous + 'T07:05'], 'sunset': [today + 'T18:15']},
                      {'time': [], 'sunrise': [], 'sunset': []}):
            with self.subTest(daily=daily), patch('chat_services.get_data', side_effect=self.fetch):
                self.client.cache.clear()
                self.weather_data['daily'] = daily
                with self.assertRaises(ServiceError):
                    self.client.answer(service_request('погода Орёл'))
        self.client.cache.clear()
        self.weather_data['daily'] = {'time': [today], 'sunrise': [None], 'sunset': [None]}
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('погода Орёл'))
        self.assertIn('<i>Рассвет</i> данных нет', text)
        self.assertIn('<i>Закат</i> данных нет', text)

    def test_pressure_wrong_units_or_invalid_values_are_rejected(self):
        for units, pressure in (('mmHg', 760), ('hPa', None), ('hPa', 1300)):
            with self.subTest(units=units, pressure=pressure), patch('chat_services.get_data', side_effect=self.fetch):
                self.client.cache.clear()
                self.weather_data['current_units']['surface_pressure'] = units
                self.weather_data['current']['surface_pressure'] = pressure
                with self.assertRaises(ServiceError):
                    self.client.answer(service_request('погода Орёл'))

    def test_stale_weather_bad_units_and_invalid_values_are_rejected(self):
        for change in ('stale', 'units', 'null', 'invalid'):
            with self.subTest(change=change):
                self.client.cache.clear()
                saved = self.weather_data['current'].copy()
                if change == 'stale':
                    self.weather_data['current']['time'] = (datetime.now(MOSCOW) - timedelta(hours=2)).isoformat()[:16]
                elif change == 'units':
                    self.weather_data['current_units']['wind_speed_10m'] = 'km/h'
                elif change == 'null':
                    self.weather_data['current']['temperature_2m'] = None
                else:
                    self.weather_data['current']['relative_humidity_2m'] = 300
                with patch('chat_services.get_data', side_effect=self.fetch):
                    with self.assertRaises(ServiceError):
                        self.client.answer(service_request('погода Орёл'))
                self.weather_data['current'] = saved
                self.weather_data['current_units']['wind_speed_10m'] = 'm/s'

    def test_ambiguous_and_unknown_cities_do_not_report_wrong_location(self):
        for rows, reason in (([], 'city_not_found'),
                             ([dict(self.place, population=1000), dict(self.place, id=2, population=900)], 'ambiguous_city')):
            with self.subTest(reason=reason), patch('chat_services.get_data', return_value={'results': rows}):
                with self.assertRaisesRegex(ServiceError, reason):
                    self.client.location('неизвестный город')

    def test_transport_errors_do_not_expose_url_or_response_body(self):
        for error in (urllib.error.URLError('SECRET'),
                      urllib.error.HTTPError('SECRET', 429, 'SECRET', {}, io.BytesIO(b'SECRET'))):
            with patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaises(ServiceError) as caught:
                    get_data('https://example.invalid/SECRET')
                self.assertNotIn('SECRET', str(caught.exception))

    def test_transient_get_timeout_is_retried_but_rate_limit_is_not(self):
        with patch('urllib.request.urlopen', side_effect=[TimeoutError(), io.BytesIO(b'{"ok": true}')]) as network, patch('chat_services.time.sleep'):
            self.assertEqual(get_data('https://example.invalid/data'), {'ok': True})
        self.assertEqual(network.call_count, 2)
        error = urllib.error.HTTPError('url', 429, 'rate limited', {}, io.BytesIO())
        with patch('urllib.request.urlopen', side_effect=error) as network:
            with self.assertRaisesRegex(ServiceError, 'rate_limited'):
                get_data('https://example.invalid/data')
        self.assertEqual(network.call_count, 1)

    def test_expired_weather_cache_does_not_hide_source_failure(self):
        now = time.time()
        with patch('chat_services.get_data', side_effect=self.fetch), patch('chat_services.time.time', return_value=now):
            self.client.weather('Орёл')
        with patch('chat_services.get_data', side_effect=ServiceError('service_unavailable')), patch('chat_services.time.time', return_value=now + 301):
            with self.assertRaisesRegex(ServiceError, 'service_unavailable'):
                self.client.weather('Орёл')


if __name__ == '__main__':
    unittest.main()
