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


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatServices()
        self.calls = []
        self.today = datetime.now(MOSCOW).strftime('%d.%m.%Y')
        self.place = {'id': 515012, 'name': 'Орёл', 'latitude': 52.96879,
                      'longitude': 36.0791, 'country': 'Россия', 'country_code': 'RU',
                      'feature_code': 'PPLA', 'admin1': 'Орловская область', 'population': 303696}
        self.current_time = datetime.now(MOSCOW).replace(second=0, microsecond=0).isoformat()[:16]
        self.weather_data = {'utc_offset_seconds': 10800, 'timezone': 'Europe/Moscow',
                             'current_units': {'temperature_2m': '°C', 'wind_speed_10m': 'm/s'},
                             'current': {'time': self.current_time, 'interval': 900,
                                         'temperature_2m': -2.5, 'apparent_temperature': -6.1,
                                         'relative_humidity_2m': 82, 'weather_code': 71,
                                         'precipitation': 0.3, 'wind_speed_10m': 4.2}}

    def fetch(self, url, xml=False):
        self.calls.append(url)
        if 'cbr.ru/' in url:
            return ET.fromstring(f'<ValCurs Date="{self.today}"><Valute><CharCode>USD</CharCode><Nominal>10</Nominal><Value>845,0000</Value></Valute></ValCurs>')
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

    def test_non_amounts_and_url_wallet_fragments_do_not_activate(self):
        for text in ('100 USDTABC', 'x100 USD', '-100 USD', '+100 USD', '0 USDT',
                     '999999999999999 USD', '100 USD_foo', '123.45.67 USD',
                     'https://example.test/100USD', '@100USD', '0x100BTC'):
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
        self.assertIn('100,00 USD ≈ 8 450,00 RUB', text)
        self.assertIn('8 450,00 RUB ≈ 100,00 USD', text)
        self.assertIn('курс ЦБ на ' + self.today, text)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('date_req=', self.calls[0])

    def test_usdt_is_not_assumed_equal_to_usd_and_sources_are_separate(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('100 USDT'))
        self.assertIn('100 USDT ≈ 99,90 USD ≈ 8 441,55 RUB', text)
        self.assertIn('CoinGecko:', text)
        self.assertIn('RUB: курс ЦБ на', text)
        crypto_url = next(u for u in self.calls if 'coingecko' in u)
        self.assertNotIn('100', crypto_url)
        self.assertEqual(parse_qs(urlparse(crypto_url).query)['ids'], ['tether'])

    def test_small_crypto_amount_keeps_source_precision(self):
        with patch('chat_services.get_data', side_effect=self.fetch):
            text = self.client.answer(service_request('0.00000001 BTC'))
        self.assertIn('0,00000001 BTC ≈ 0,00002000 USD', text)

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
        self.assertIn('Орёл, Орловская область, Россия', text)
        self.assertIn('-2.5 °C, слабый снег; ощущается -6.1 °C', text)
        self.assertIn('ветер 4.2 м/с; влажность 82%', text)
        self.assertIn('осадки 0.3 мм за последние 15 мин.', text)
        self.assertIn('Open-Meteo', text)
        self.assertIn('Europe/Moscow', text)
        geocode = parse_qs(urlparse(self.calls[0]).query)
        self.assertEqual(geocode['name'], ['Орёл'])
        self.assertEqual(geocode['countryCode'], ['RU'])
        self.assertNotIn('Володька', self.calls[0])
        self.assertIn('wind_speed_unit=ms', self.calls[1])

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
