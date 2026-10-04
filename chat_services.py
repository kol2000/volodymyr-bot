"""Currency amounts and current weather from public APIs, never from the LLM."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo('Europe/Moscow')
COINS = {'USDT': 'tether', 'USDC': 'usd-coin', 'BTC': 'bitcoin',
         'ETH': 'ethereum', 'BNB': 'binancecoin', 'SOL': 'solana',
         'TON': 'the-open-network', 'DOGE': 'dogecoin'}
NUMBER = r'(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,8})?'
UNIT = r'(?:USDT|USDC|USD|RUB|BTC|ETH|BNB|SOL|TON|DOGE|доллар(?:ов|а)?|бакс(?:ов|а)?|руб(?:лей|ля|ль)?|\$|₽)'
AMOUNTS = re.compile(r'(?<![\w.,+\-])(?:(?P<prefix>\$|₽)\s*(?P<first>' + NUMBER +
                     r')|(?P<second>' + NUMBER + r')\s*(?P<unit>' + UNIT + r'))(?!\w)', re.I)
CITY_ALIASES = {'орле': 'Орёл', 'орел': 'Орёл', 'орёл': 'Орёл',
                'москве': 'Москва', 'петербурге': 'Санкт-Петербург',
                'санкт-петербурге': 'Санкт-Петербург', 'киеве': 'Киев',
                'казани': 'Казань', 'сочи': 'Сочи', 'калуге': 'Калуга',
                'лондоне': 'Лондон', 'париже': 'Париж', 'минске': 'Минск'}
CONDITIONS = {0: 'ясно', 1: 'преимущественно ясно', 2: 'переменная облачность',
              3: 'пасмурно', 45: 'туман', 48: 'туман с изморозью',
              51: 'слабая морось', 53: 'морось', 55: 'сильная морось',
              56: 'слабая ледяная морось', 57: 'ледяная морось',
              61: 'слабый дождь', 63: 'дождь', 65: 'сильный дождь',
              66: 'слабый ледяной дождь', 67: 'ледяной дождь',
              71: 'слабый снег', 73: 'снег', 75: 'сильный снег', 77: 'снежная крупа',
              80: 'слабые ливни', 81: 'ливни', 82: 'сильные ливни',
              85: 'слабые снежные заряды', 86: 'сильные снежные заряды',
              95: 'гроза', 96: 'гроза с градом', 99: 'сильная гроза с градом'}


class ServiceError(Exception):
    """Only a fixed reason code, without URLs or response bodies."""


def decimal_value(value, positive=False):
    if isinstance(value, bool):
        raise ServiceError('invalid_number')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ServiceError('invalid_number') from None
    if not result.is_finite() or (positive and result <= 0):
        raise ServiceError('invalid_number')
    return result


def amounts_in(text):
    # URLs, addresses and usernames are not currency amounts.
    text = re.sub(r'https?://\S+|\b0x[a-f0-9]+\b|@[\w]+', '', text, flags=re.I)
    result = []
    for match in AMOUNTS.finditer(text[:1000]):
        number = re.sub(r'[ \u00a0\u202f]', '', match['first'] or match['second']).replace(',', '.')
        amount = decimal_value(number)
        unit = (match['prefix'] or match['unit']).upper()
        if unit in ('$', 'USD') or unit.startswith(('ДОЛЛАР', 'БАКС')):
            unit = 'USD'
        elif unit in ('₽', 'RUB') or unit.startswith('РУБ'):
            unit = 'RUB'
        row = {'amount': str(amount), 'unit': unit}
        if 0 < amount <= Decimal('1000000000000') and row not in result:
            result.append(row)
        if len(result) == 3:
            break
    return result


def service_request(text, command=''):
    if command == '/weather' or re.search(r'\b(?:какая|покажи|скажи|узнай)\b.*\bпогод[ауеы]\b', text, re.I) or re.match(r'^\s*(?:[\w@]+[, :]\s*)?погод[ауеы]\b', text, re.I):
        if re.search(r'\b(?:завтра|послезавтра|недел\w*)\b', text, re.I):
            return {'kind': 'weather', 'error': 'forecast_unsupported'}
        rest = re.sub(r'^/weather(?:@\w+)?\s*', '', text, flags=re.I) if command == '/weather' else re.split(r'\bпогод[ауеы]\b', text, maxsplit=1, flags=re.I)[-1]
        rest = re.sub(r'\b(?:сейчас|сечас|сегодня|пожалуйста)\b', '', rest, flags=re.I).strip(' ,.!?')
        city = re.sub(r'^(?:в|во)\s+', '', rest, flags=re.I)
        city = re.sub(r'^(?:городе?|г\.)\s+', '', city, flags=re.I).strip(' ,.!?')
        city = CITY_ALIASES.get(city.casefold(), city)
        if not city:
            return {'kind': 'weather', 'error': 'city_missing'}
        if len(city) > 80 or not re.fullmatch(r'[а-яёіїєґa-z ,\-]+', city, re.I):
            return {'kind': 'weather', 'error': 'city_not_found'}
        return {'kind': 'weather', 'city': city}
    amounts = amounts_in(text)
    if amounts or command == '/convert':
        return {'kind': 'currency', 'amounts': amounts}
    return None


def get_data(url, xml=False):
    request = urllib.request.Request(url, headers={'User-Agent': 'volodymyr-bot/1.0'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read(262145)
            if len(body) > 262144:
                raise ServiceError('response_too_large')
            return ET.fromstring(body) if xml else json.loads(body)
        except urllib.error.HTTPError as error:
            if not attempt and error.code in (500, 502, 503, 504):
                time.sleep(0.5)
                continue
            raise ServiceError('rate_limited' if error.code == 429 else 'service_http_error') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            # Retrying an idempotent data GET cannot duplicate a Telegram reply.
            if not attempt:
                time.sleep(0.5)
                continue
            raise ServiceError('service_unavailable') from None
        except (ValueError, ET.ParseError):
            raise ServiceError('malformed_data') from None


def money(value):
    value = decimal_value(value)
    precision = Decimal('0.00000001') if 0 < abs(value) < Decimal('0.01') else Decimal('0.01')
    return f'{value.quantize(precision, rounding=ROUND_HALF_UP):,f}'.replace(',', ' ').replace('.', ',')


class ChatServices:
    def __init__(self):
        self.cache = {}

    def cached(self, key, ttl, fetch):
        now = time.time()
        stored = self.cache.get(key)
        if stored and 0 <= now - stored[0] < ttl:
            return stored[1]
        value = fetch()
        if len(self.cache) >= 100:
            self.cache.clear()
        self.cache[key] = (time.time(), value)
        return value

    def cbr(self):
        today = datetime.now(MOSCOW).date()

        def fetch():
            root = get_data('https://www.cbr.ru/scripts/XML_daily.asp?' +
                            urllib.parse.urlencode({'date_req': today.strftime('%d/%m/%Y')}), xml=True)
            date = datetime.strptime(root.attrib['Date'], '%d.%m.%Y').date()
            if not 0 <= (today - date).days <= 14:
                raise ServiceError('stale_rates')
            for row in root.findall('Valute'):
                if row.findtext('CharCode') == 'USD':
                    rate = decimal_value(row.findtext('Value').replace(',', '.'), positive=True)
                    nominal = decimal_value(row.findtext('Nominal'), positive=True)
                    return rate / nominal, date.strftime('%d.%m.%Y')
            raise ServiceError('missing_usd_rate')

        return self.cached(('cbr', today), 3600, fetch)

    def crypto(self, units):
        def fetch():
            url = 'https://api.coingecko.com/api/v3/simple/price?' + urllib.parse.urlencode({
                'ids': ','.join(COINS[u] for u in sorted(units)), 'vs_currencies': 'usd',
                'include_last_updated_at': 'true'})
            data = get_data(url)
            result = {}
            for unit in units:
                row = data[COINS[unit]]
                stamp = decimal_value(row['last_updated_at'], positive=True)
                if not -60 <= time.time() - float(stamp) <= 900:
                    raise ServiceError('stale_crypto_price')
                result[unit] = (decimal_value(row['usd'], positive=True), float(stamp))
            return result

        data = self.cached(('crypto', tuple(sorted(units))), 60, fetch)
        if any(time.time() - row[1] > 900 for row in data.values()):
            raise ServiceError('stale_crypto_price')
        return data

    def convert(self, amounts):
        if not amounts:
            return 'напиши сумму например 100 USDT или 5000 рублей'
        rate, date = self.cbr()
        units = {row['unit'] for row in amounts if row['unit'] in COINS}
        coins = self.crypto(units) if units else {}
        lines = []
        for row in amounts:
            amount, unit = decimal_value(row['amount'], positive=True), row['unit']
            if unit == 'RUB':
                line = f'{money(amount)} RUB ≈ {money(amount / rate)} USD'
            elif unit == 'USD':
                line = f'{money(amount)} USD ≈ {money(amount * rate)} RUB'
            else:
                usd = amount * coins[unit][0]
                source_amount = format(amount, 'f').rstrip('0').rstrip('.') if '.' in format(amount, 'f') else str(amount)
                line = f'{source_amount.replace(".", ",")} {unit} ≈ {money(usd)} USD ≈ {money(usd * rate)} RUB'
            lines.append(line)
        sources = f'RUB: курс ЦБ на {date}'
        if coins:
            stamp = min(row[1] for row in coins.values())
            sources = 'CoinGecko: ' + datetime.fromtimestamp(stamp, MOSCOW).strftime('%d.%m %H:%M МСК') + '\n' + sources
        return '\n'.join(lines) + '\n\n' + sources

    def location(self, city):
        def fetch():
            params = {'name': city, 'count': 10, 'language': 'ru'}
            if city == 'Орёл':
                params['countryCode'] = 'RU'
            data = get_data('https://geocoding-api.open-meteo.com/v1/search?' + urllib.parse.urlencode(params))
            rows = [row for row in data.get('results', []) if row.get('feature_code', '').startswith('PPL')]
            if not rows:
                raise ServiceError('city_not_found')
            rows.sort(key=lambda row: row.get('population', 0), reverse=True)
            if len(rows) > 1 and rows[0].get('population', 0) < max(100000, 5 * rows[1].get('population', 0)):
                raise ServiceError('ambiguous_city')
            return rows[0]
        return self.cached(('city', city.casefold()), 86400, fetch)

    def weather(self, city):
        location = self.location(city)

        def fetch():
            params = {'latitude': location['latitude'], 'longitude': location['longitude'],
                      'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
                      'wind_speed_unit': 'ms', 'timezone': 'auto'}
            return get_data('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params))

        data = self.cached(('weather', location['id']), 300, fetch)
        current, units = data['current'], data['current_units']
        if units['temperature_2m'] != '°C' or units['wind_speed_10m'] != 'm/s':
            raise ServiceError('unexpected_weather_units')
        offset = int(data['utc_offset_seconds'])
        stamp = datetime.fromisoformat(current['time']).replace(tzinfo=timezone(timedelta(seconds=offset)))
        if not -900 <= time.time() - stamp.timestamp() <= 5400:
            raise ServiceError('stale_weather')
        temp = decimal_value(current['temperature_2m'])
        feels = decimal_value(current['apparent_temperature'])
        humidity = decimal_value(current['relative_humidity_2m'])
        wind = decimal_value(current['wind_speed_10m'])
        rain = decimal_value(current['precipitation'])
        interval = decimal_value(current['interval'], positive=True)
        if interval != int(interval) or not 60 <= interval <= 3600:
            raise ServiceError('invalid_weather_values')
        if not (-100 <= temp <= 70 and -120 <= feels <= 90 and 0 <= humidity <= 100 and 0 <= wind <= 120 and 0 <= rain <= 1000):
            raise ServiceError('invalid_weather_values')
        condition = CONDITIONS.get(current['weather_code'], 'состояние не указано')
        title = ', '.join(str(location[k]) for k in ('name', 'admin1', 'country') if location.get(k))
        return (f'{title}\n{temp:+.1f} °C, {condition}; ощущается {feels:+.1f} °C\n'
                f'ветер {wind:.1f} м/с; влажность {humidity:.0f}%\n'
                f'осадки {rain:.1f} мм за последние {int(interval) // 60} мин.\n\n'
                f'Open-Meteo · {stamp.strftime("%d.%m.%Y %H:%M")} ({data["timezone"]})')

    def answer(self, request):
        try:
            error = request.get('error')
            if error:
                raise ServiceError(error)
            return self.weather(request['city']) if request['kind'] == 'weather' else self.convert(request['amounts'])
        except ServiceError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, OverflowError):
            raise ServiceError('malformed_data') from None


def service_error_reply(kind, reason):
    if reason == 'city_missing':
        return 'в каком городе погоду показать'
    if reason in ('city_not_found', 'ambiguous_city'):
        return 'уточни город и страну например /weather Орёл, Россия'
    if reason == 'forecast_unsupported':
        return 'пока показываю погоду сечас напиши город'
    return 'сечас свежую погоду не достал' if kind == 'weather' else 'сечас свежий курс не достал'
