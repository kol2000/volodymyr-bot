"""Currency amounts and current weather from public APIs, never from the LLM."""
import json
from html import escape
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from common import HTMLMessage

MOSCOW = ZoneInfo('Europe/Moscow')
COINS = {'USDT': 'tether', 'USDC': 'usd-coin', 'BTC': 'bitcoin',
         'ETH': 'ethereum', 'BNB': 'binancecoin', 'SOL': 'solana',
         'TON': 'the-open-network', 'DOGE': 'dogecoin'}
NUMBER = r'(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,8})?'
RUBLE_UNIT = r'руб(?:лей|ля|ль|ли)?'
BYN_UNIT = r'(?:BYN|белруб(?:ов|а)?|(?:белорусск(?:ий|их|ие|ого|ому|им|ими|ом)|бел\.?)\s+' + RUBLE_UNIT + r'\.?)'
UAH_UNIT = r'(?:UAH|грн\.?|₴|грив(?:на|ны|ну|не|ной|нами|нам|нах|ен|ня|ні|ню|нею|нями|ням|нях|ень))'
KZT_UNIT = r'(?:KZT|₸|(?:(?:казахстанск|казахск)(?:ий|их|ие|ого|ому|им|ими|ом)\s+)?(?:тенге|теңге))'
UNIT = r'(?:' + BYN_UNIT + '|' + UAH_UNIT + '|' + KZT_UNIT + r'|USDT|USDC|USD|RUB|BTC|ETH|BNB|SOL|TON|DOGE|доллар(?:ов|а)?|бакс(?:ов|а)?|' + RUBLE_UNIT + r'|\$|₽)'
AMOUNTS = re.compile(r'(?<![\w.,+\-])(?:(?P<prefix>\$|₽|₴|₸)\s*(?P<first>' + NUMBER +
                     r')|(?P<second>' + NUMBER + r')\s*(?P<unit>' + UNIT + r'))(?!\w)', re.I)
CITY_ALIASES = {'орле': 'Орёл', 'орел': 'Орёл', 'орёл': 'Орёл',
                'москве': 'Москва', 'петербурге': 'Санкт-Петербург',
                'санкт-петербурге': 'Санкт-Петербург', 'киеве': 'Киев',
                'казани': 'Казань', 'сочи': 'Сочи', 'калуге': 'Калуга',
                'лондоне': 'Лондон', 'париже': 'Париж', 'минске': 'Минск',
                'махачкале': 'Махачкала', 'махачкалу': 'Махачкала',
                'рахине': 'Рахиня', 'рахини': 'Рахиня', 'рахиню': 'Рахиня',
                'рахині': 'Рахиня', 'rakhynia': 'Рахиня'}
# Rakhynia in Ivano-Frankivsk oblast: GeoNames ID 695869 (Wikidata Q4391110).
# Open-Meteo indexes its Latin name but does not find the Cyrillic one.
CITY_LOOKUPS = {'рахиня': {'query': 'Rakhynia', 'country': 'UA', 'id': 695869,
                          'label': 'Рахиня', 'settlement': 'селе'}}
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


def weather_icon(code):
    if code in (0, 1, 2, 3):
        return ('☀️', '🌤️', '⛅', '☁️')[code]
    if code in (45, 48):
        return '🌫️'
    if code in (95, 96, 99):
        return '⛈️'
    if code in (71, 73, 75, 77, 85, 86):
        return '🌨️'
    return '🌧️' if code in CONDITIONS else '🌡️'


def sun_time(daily, field, day):
    if daily['time'][0] != day.isoformat():
        raise ServiceError('stale_sun_times')
    value = daily[field][0]
    if not value:
        return 'данных нет'
    event = datetime.fromisoformat(value)
    if event.date() != day:
        raise ServiceError('stale_sun_times')
    return 'в ' + event.strftime('%H:%M')


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
        elif unit == 'BYN' or unit.startswith('БЕЛ'):
            unit = 'BYN'
        elif unit in ('UAH', '₴') or unit.startswith(('ГРИВ', 'ГРН')):
            unit = 'UAH'
        elif unit in ('KZT', '₸', 'ТЕНГЕ', 'ТЕҢГЕ') or unit.startswith(('КАЗАХСТАНСК', 'КАЗАХСК')):
            unit = 'KZT'
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
        city = re.sub(r'^(?:городе?|г\.|селе|село|деревне|деревня)\s+', '', city, flags=re.I).strip(' ,.!?')
        # Normalize the city separately from an optional country/region qualifier.
        # Otherwise "в Махачкале, Россия" would bypass the same alias as "в Махачкале".
        parts = [' '.join(part.split()) for part in city.split(',')]
        parts[0] = CITY_ALIASES.get(parts[0].casefold(), parts[0])
        city = ', '.join(parts)
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
            rates = {}
            for row in root.findall('Valute'):
                unit = row.findtext('CharCode')
                if unit in ('USD', 'BYN', 'UAH', 'KZT'):
                    rate = decimal_value(row.findtext('Value').replace(',', '.'), positive=True)
                    nominal = decimal_value(row.findtext('Nominal'), positive=True)
                    rates[unit] = rate / nominal
            if 'USD' not in rates:
                raise ServiceError('missing_usd_rate')
            return rates, date.strftime('%d.%m.%Y')

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
        rates, date = self.cbr()
        rate = rates['USD']
        units = {row['unit'] for row in amounts if row['unit'] in COINS}
        coins = self.crypto(units) if units else {}
        blocks = []
        for row in amounts:
            amount, unit = decimal_value(row['amount'], positive=True), row['unit']
            if unit == 'RUB':
                header = f'{money(amount)} RUB'
                results = [f'💵 <b>≈ {money(amount / rate)} USD</b>']
            elif unit == 'USD':
                header = f'{money(amount)} USD'
                results = [f'💰 <b>≈ {money(amount * rate)} RUB</b>']
            elif unit in ('BYN', 'UAH', 'KZT'):
                if unit not in rates:
                    raise ServiceError('missing_fiat_rate')
                rub = amount * rates[unit]
                header = f'{money(amount)} {unit}'
                results = [f'💵 <b>≈ {money(rub / rate)} USD</b>', f'💰 <b>≈ {money(rub)} RUB</b>']
            else:
                usd = amount * coins[unit][0]
                source_amount = format(amount, 'f').rstrip('0').rstrip('.') if '.' in format(amount, 'f') else str(amount)
                whole, dot, fraction = source_amount.partition('.')
                source_amount = f'{int(whole):,}'.replace(',', ' ') + (',' + fraction if dot else '')
                header = f'{source_amount} {unit}'
                results = [f'💵 <b>≈ {money(usd)} USD</b>', f'💰 <b>≈ {money(usd * rate)} RUB</b>']
            blocks.append(f'💱 <b>{escape(header)}</b>\n' + '\n'.join(results))
        sources = f'RUB: курс ЦБ на {date}'
        if any(row['unit'] in ('BYN', 'UAH', 'KZT') for row in amounts):
            sources = f'курс ЦБ на {date}'
        if coins:
            stamp = min(row[1] for row in coins.values())
            sources = 'CoinGecko: ' + datetime.fromtimestamp(stamp, MOSCOW).strftime('%d.%m %H:%M МСК') + '\n' + sources
        return HTMLMessage('\n\n'.join(blocks) + '\n\n<i>' + escape(sources) + '</i>')

    def location(self, city):
        def fetch():
            params = {'name': city, 'count': 10, 'language': 'ru'}
            name, separator, qualifier = city.partition(',')
            known = CITY_LOOKUPS.get(name.strip().casefold())
            if known:
                params.update(name=known['query'] + separator + qualifier, countryCode=known['country'])
            if city == 'Орёл':
                params['countryCode'] = 'RU'
            data = get_data('https://geocoding-api.open-meteo.com/v1/search?' + urllib.parse.urlencode(params))
            rows = [row for row in data.get('results', []) if row.get('feature_code', '').startswith('PPL')]
            if known:
                rows = [row for row in rows if row.get('id') == known['id']
                        and row.get('country_code') == known['country']]
            if not rows:
                raise ServiceError('city_not_found')
            rows.sort(key=lambda row: row.get('population', 0), reverse=True)
            if len(rows) > 1 and rows[0].get('population', 0) < max(100000, 5 * rows[1].get('population', 0)):
                raise ServiceError('ambiguous_city')
            return dict(rows[0], name=known['label'], settlement=known['settlement']) if known else rows[0]
        return self.cached(('city', city.casefold()), 86400, fetch)

    def weather(self, city):
        location = self.location(city)
        local_day = datetime.now(ZoneInfo(location['timezone'])).date()

        def fetch():
            params = {'latitude': location['latitude'], 'longitude': location['longitude'],
                      'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,surface_pressure',
                      'daily': 'sunrise,sunset', 'forecast_days': 1,
                      'wind_speed_unit': 'ms', 'timezone': 'auto'}
            return get_data('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params))

        data = self.cached(('weather', location['id'], local_day), 300, fetch)
        current, units = data['current'], data['current_units']
        if units['temperature_2m'] != '°C' or units['wind_speed_10m'] != 'm/s' or units['surface_pressure'] != 'hPa':
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
        pressure = decimal_value(current['surface_pressure'], positive=True)
        interval = decimal_value(current['interval'], positive=True)
        if interval != int(interval) or not 60 <= interval <= 3600:
            raise ServiceError('invalid_weather_values')
        if not (-100 <= temp <= 70 and -120 <= feels <= 90 and 0 <= humidity <= 100 and 0 <= wind <= 120 and 0 <= rain <= 1000 and 400 <= pressure <= 1150):
            raise ServiceError('invalid_weather_values')
        condition = CONDITIONS.get(current['weather_code'], 'состояние не указано')
        # 1 conventional mmHg = 133.3224 Pa; provider pressure is in hPa.
        mmhg = (pressure / Decimal('1.333224')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        sunrise = sun_time(data['daily'], 'sunrise', local_day)
        sunset = sun_time(data['daily'], 'sunset', local_day)
        title = escape(str(location['name']))
        settlement = escape(str(location.get('settlement', 'городе')))
        region = ', '.join(str(location[k]) for k in ('admin1', 'country') if location.get(k))
        return HTMLMessage(
            f'Сечас в {settlement} <b>{title}</b>:\n\n'
            f'{weather_icon(current["weather_code"])} {escape(condition)}\n\n'
            f'🌡️ <i>Температура воздуха</i> — {temp:+.1f} °C\n'
            f'👀 <i>Чувствуется как</i> — {feels:+.1f} °C\n'
            f'💦 <i>Влажность</i> — {humidity:.0f}%\n'
            f'💨 <i>Ветер</i> — {wind:.1f} м/с\n'
            f'📍 <i>Атмосферное давление</i> — {mmhg} мм рт. ст.\n'
            f'🌧️ <i>Осадки</i> — {rain:.1f} мм за последние {int(interval) // 60} мин.\n\n'
            f'🌅 <i>Рассвет</i> {sunrise}\n'
            f'🌇 <i>Закат</i> {sunset}\n\n'
            f'<i>{escape(region)}\nOpen-Meteo · {stamp.strftime("%d.%m.%Y %H:%M")} ({escape(str(data["timezone"]))})</i>')

    def answer(self, request):
        try:
            error = request.get('error')
            if error:
                raise ServiceError(error)
            return self.weather(request['city']) if request['kind'] == 'weather' else self.convert(request['amounts'])
        except ServiceError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, InvalidOperation, OverflowError):
            raise ServiceError('malformed_data') from None


def service_error_reply(kind, reason):
    if reason == 'city_missing':
        return 'в каком городе погоду показать'
    if reason in ('city_not_found', 'ambiguous_city'):
        return 'уточни город и страну например /weather Орёл, Россия'
    if reason == 'forecast_unsupported':
        return 'пока показываю погоду сечас напиши город'
    return 'сечас свежую погоду не достал' if kind == 'weather' else 'сечас свежий курс не достал'
