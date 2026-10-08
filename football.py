"""RPL and Russian Cup scores. Fixed API source, persistent quota, no LLM."""
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from common import HTMLMessage

MOSCOW = ZoneInfo('Europe/Moscow')
BASE = 'https://v3.football.api-sports.io/'
LIMIT = 100
RESERVE = 10
FINISHED = {'FT', 'AET', 'PEN'}
LIVE = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'}
STOPPED = FINISHED | {'PST', 'CANC', 'ABD', 'AWD', 'WO'}
COMPETITIONS = {'Premier League': ('League', 'РПЛ'), 'Cup': ('Cup', 'Кубок России')}
COMMANDS = {'/football', '/football_on', '/football_off'}
# Aliases concern club names, not fixture IDs; IDs/seasons come from /leagues.
TEAM_ALIASES = (
    (r'зенит\w*', ('zenit',)), (r'спартак\w*', ('spartak',)),
    (r'цска', ('cska',)), (r'локомотив\w*|локо', ('lokomotiv',)),
    (r'краснодар\w*', ('krasnodar',)), (r'рубин\w*', ('rubin',)),
    (r'ростов\w*', ('rostov',)), (r'балтик\w*', ('baltika',)),
    (r'ахмат\w*', ('akhmat',)), (r'оренбург\w*', ('orenburg',)),
    (r'сочи', ('sochi',)), (r'акрон\w*', ('akron',)),
    (r'крылья\s+советов|крыльев\s+советов|крылья', ('krylya',)),
    (r'пари\s+нн|нижн\w*\s+новгород\w*', ('nizhny', 'pari nn')),
    (r'динамо\s+махачкал\w*', ('dynamo makhachkala', 'dinamo makhachkala')),
    (r'динамо\s+москв\w*', ('dynamo moscow', 'dinamo moscow')),
    (r'динамо', ('dynamo', 'dinamo')), (r'урал\w*', ('ural',)),
    (r'торпедо', ('torpedo',)), (r'уфа', ('ufa',)),
)
TEAM_NAMES = {'zenit saint petersburg': 'Зенит', 'zenit': 'Зенит',
              'spartak moscow': 'Спартак', 'cska moscow': 'ЦСКА',
              'lokomotiv moscow': 'Локомотив', 'fc krasnodar': 'Краснодар',
              'krasnodar': 'Краснодар', 'rubin': 'Рубин', 'rubin kazan': 'Рубин',
              'fc rostov': 'Ростов', 'rostov': 'Ростов', 'baltika': 'Балтика',
              'akhmat': 'Ахмат', 'akhmat grozny': 'Ахмат', 'orenburg': 'Оренбург',
              'fc sochi': 'Сочи', 'sochi': 'Сочи', 'akron': 'Акрон',
              'krylya sovetov': 'Крылья Советов', 'nizhny novgorod': 'Пари НН',
              'dynamo moscow': 'Динамо Москва', 'dinamo moscow': 'Динамо Москва',
              'dynamo makhachkala': 'Динамо Махачкала', 'dinamo makhachkala': 'Динамо Махачкала'}


class FootballError(Exception):
    """Only a fixed reason, never credentials or a provider response body."""


def error_reply(reason):
    return {
        'key_missing': 'Футбол пока не настроен: владельцу нужно запустить python3 setup_football.py на VM.',
        'key_invalid': 'Ключ API-Football не принят. Владельцу: повтори настройку на VM.',
        'season_access': 'API-Football не разрешает этот сезон на вашем тарифе. Старые результаты вместо текущих не показываю.',
        'competitions_missing': 'API-Football пока не вернул текущие сезоны РПЛ и Кубка России.',
        'quota': 'Лимит футбольного API на сегодня исчерпан. Новые счета появятся после 03:00 МСК.',
        'reserve': 'Запросы на уведомления закончились; остаток оставлен для /football.',
        'rate_limit': 'API-Football просит паузу. Повтори запрос через пару минут.',
    }.get(reason, 'Сечас свежий счёт не достал. Попробуй /football позже.')


def football_request(text, command='', addressed=False):
    if command and command not in COMMANDS:
        return None
    body = text.strip()
    if command:
        body = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ''
        if command in ('/football_on', '/football_off'):
            return {'kind': 'football_control', 'enabled': command == '/football_on'}
        addressed = True
    else:
        match = re.match(r'^\s*(?:володька|володько|бубус|бубуська)[\s,:!]+', body, re.I)
        if match:
            body, addressed = body[match.end():], True
    if not addressed:
        return None
    sports = re.search(r'\b(?:футбол\w*|рпл|кубок\s+россии|кубк[аеу]\s+россии)\b', body, re.I)
    question = re.search(r'\b(?:сч[её]т|играет|играют|матч\w*|сыграл\w*)\b', body, re.I)
    team = next((aliases for pattern, aliases in TEAM_ALIASES
                 if re.search(r'(?<!\w)(?:' + pattern + r')(?!\w)', body, re.I)), None)
    if command != '/football' and not sports and not (question and team):
        return None
    competition = ('РПЛ' if re.search(r'\bрпл\b', body, re.I) else
                   'Кубок России' if re.search(r'\bкуб(?:ок|ка|ке|ку)\b', body, re.I) else None)
    if command and body and not team and not competition and body.casefold() not in ('сегодня', 'футбол'):
        return {'kind': 'fixed', 'text': 'Напиши /football, /football РПЛ, /football Кубок или /football Зенит. Показываю матчи за сегодня.'}
    return {'kind': 'football', 'competition': competition, 'team': team}


def provider_get(key, endpoint, params):
    request = urllib.request.Request(BASE + endpoint + '?' + urllib.parse.urlencode(params),
                                     headers={'x-apisports-key': key, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        reason = 'key_invalid' if error.code in (401, 403) else 'rate_limit' if error.code == 429 else 'unavailable'
        raise FootballError(reason) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise FootballError('unavailable') from None


def _load(db, key, default):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def utc_day(now):
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def quota_state(db, now):
    quota = _load(db, 'football_quota', {})
    if quota.get('day') != utc_day(now):
        quota = {'day': utc_day(now), 'used': 0, 'remaining': LIMIT, 'calls': []}
    return quota


def name(text):
    return escape(TEAM_NAMES.get(text.casefold(), text)[:80])


def match_line(row):
    home, away = name(row['home']), name(row['away'])
    score = f'{row["goals"][0]} : {row["goals"][1]}' if None not in row['goals'] else '—'
    status = row['status']
    if status == 'NS':
        detail = datetime.fromtimestamp(row['kickoff'], MOSCOW).strftime('%H:%M МСК')
        return f'{home} — {away} · {detail}'
    detail = {'FT': 'матч окончен', 'AET': 'после доп. времени', 'PEN': 'после пенальти',
              'HT': 'перерыв', 'P': 'серия пенальти', 'ET': 'доп. время', 'BT': 'перерыв перед доп. временем',
              'PST': 'перенесён', 'CANC': 'отменён', 'ABD': 'прерван', 'INT': 'приостановлен',
              'AWD': 'технический результат', 'WO': 'неявка', 'TBD': 'время уточняется'}.get(status)
    if not detail:
        detail = f'{row["minute"]}′' if row['minute'] is not None else 'идёт матч' if status in LIVE else 'статус уточняется'
    penalties = row['penalties']
    if status in ('P', 'PEN') and None not in penalties:
        detail += f' · пенальти {penalties[0]} : {penalties[1]}'
    return f'{home} <b>{score}</b> {away} · {detail}'


class Football:
    def __init__(self, config, store, transport=provider_get, clock=time.time):
        self.key = config.get('football_api_key', '').strip()
        self.store, self.transport, self.clock = store, transport, clock
        self.lock = threading.RLock()

    def remaining(self):
        with self.store.db() as db:
            q = quota_state(db, self.clock())
        return max(0, min(LIMIT - q['used'], q['remaining']))

    def _request(self, endpoint, params, automatic=False):
        if not self.key:
            raise FootballError('key_missing')
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            q = quota_state(db, now)
            pause = _load(db, 'football_pause', {})
            if pause.get('until', 0) > now:
                raise FootballError(pause.get('reason', 'unavailable'))
            remaining = min(LIMIT - q['used'], q['remaining'])
            if remaining <= (RESERVE if automatic else 0):
                raise FootballError('reserve' if remaining > 0 else 'quota')
            q['calls'] = [t for t in q['calls'] if now - t < 60]
            if len(q['calls']) >= 8:
                raise FootballError('rate_limit')
            # Claim BEFORE transport; failed/uncertain calls also consume the budget.
            q['used'] += 1
            q['remaining'] = max(0, q['remaining'] - 1)
            q['calls'].append(now)
            _save(db, 'football_quota', q)
        try:
            data, headers = self.transport(self.key, endpoint, params)
            if not isinstance(data, dict) or not isinstance(data.get('response'), list):
                raise FootballError('malformed')
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                q = quota_state(db, self.clock())
                # A call straddling midnight must not overwrite the new day's quota.
                if q['day'] == utc_day(now):
                    value = headers.get('x-ratelimit-requests-remaining')
                    if value is not None:
                        q['remaining'] = min(q['remaining'], max(0, int(value)))
                    _save(db, 'football_quota', q)
                if headers.get('x-ratelimit-remaining') == '0':
                    _save(db, 'football_pause', {'until': self.clock() + 60, 'reason': 'rate_limit'})
            if data.get('errors'):
                errors = str(data['errors']).casefold()
                reason = ('season_access' if any(x in errors for x in ('season', 'plan', 'subscription')) else
                          'key_invalid' if any(x in errors for x in ('token', 'key')) else
                          'quota' if 'day' in errors and ('limit' in errors or 'request' in errors) else
                          'rate_limit' if 'ratelimit' in errors or 'minute' in errors else 'unavailable')
                raise FootballError(reason)
            if data.get('paging', {}).get('total', 1) > 1:
                raise FootballError('incomplete')
            return data['response']
        except (ValueError, TypeError, AttributeError):
            raise FootballError('malformed') from None
        except FootballError as error:
            reason = str(error)
            delay = 120 if reason == 'rate_limit' else 3600 if reason == 'key_invalid' else 300
            if reason in ('season_access', 'quota'):
                delay = 86400 - now % 86400
            self.store.set('football_pause', {'until': self.clock() + delay, 'reason': reason})
            raise

    def leagues(self, automatic=False, force=False):
        now = self.clock()
        cache = self.store.get('football_leagues', {})
        if cache and not force and now - cache['updated'] < 86400:
            return cache['rows']
        rows = self._request('leagues', {'country': 'Russia', 'current': 'true'}, automatic)
        result = []
        today = datetime.fromtimestamp(now, MOSCOW).date().isoformat()
        for row in rows:
            league = row.get('league', {})
            spec = COMPETITIONS.get(league.get('name'))
            if (not spec or league.get('type') != spec[0] or row.get('country', {}).get('name') != 'Russia'):
                continue
            seasons = [s for s in row.get('seasons', []) if s.get('current')
                       and s.get('start', '') <= today <= s.get('end', '')]
            if not seasons:
                continue
            season = max(seasons, key=lambda s: s['year'])
            result.append({'id': int(league['id']), 'season': int(season['year']), 'name': spec[1]})
        if {r['name'] for r in result} != {'РПЛ', 'Кубок России'} or len(result) != 2:
            self.store.set('football_pause', {'until': now + 86400 - now % 86400, 'reason': 'competitions_missing'})
            raise FootballError('competitions_missing')
        self.store.set('football_leagues', {'updated': now, 'rows': result})
        return result

    def _fixtures(self, raw, leagues):
        names = {r['id']: r['name'] for r in leagues}
        result = []
        try:
            for row in raw:
                if row.get('league', {}).get('id') not in names:
                    continue
                fixture, teams, goals = row['fixture'], row['teams'], row['goals']
                penalties = (row.get('score') or {}).get('penalty') or {}
                kickoff = float(fixture['timestamp'])
                minute = fixture['status'].get('elapsed')
                if (not math.isfinite(kickoff) or kickoff <= 0 or not isinstance(fixture['status']['short'], str)
                        or (minute is not None and (type(minute) is not int or not 0 <= minute <= 300))
                        or any(not isinstance(teams[side]['name'], str) or not teams[side]['name'].strip() for side in ('home', 'away'))):
                    raise ValueError()
                pair = [goals.get('home'), goals.get('away')]
                penalty_pair = [penalties.get('home'), penalties.get('away')]
                if any(v is not None and (type(v) is not int or not 0 <= v <= 99) for v in pair + penalty_pair):
                    raise ValueError()
                result.append({'id': int(fixture['id']), 'competition': names[row['league']['id']],
                               'kickoff': kickoff, 'status': fixture['status']['short'],
                               'minute': minute, 'goals': pair, 'penalties': penalty_pair,
                               'home': str(teams['home']['name']), 'away': str(teams['away']['name'])})
            return result
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise FootballError('malformed') from None

    def schedule(self, automatic=False, refresh=False, force=False):
        now = self.clock()
        day = datetime.fromtimestamp(now, MOSCOW).date()
        cache = self.store.get('football_schedule', {})
        if not force and cache.get('day') == day.isoformat() and (not refresh or now - cache['updated'] < 21600):
            return cache
        leagues = self.leagues(automatic)
        rows = []
        for league in leagues:
            raw = self._request('fixtures', {'league': league['id'], 'season': league['season'],
                                            'from': (day - timedelta(days=1)).isoformat(), 'to': day.isoformat(),
                                            'timezone': 'Europe/Moscow'}, automatic)
            rows += self._fixtures(raw, leagues)
        cache = {'day': day.isoformat(), 'updated': now, 'rows': rows,
                 'checked': {str(r['id']): now for r in rows}}
        self.store.set('football_schedule', cache)
        return cache

    def active(self, rows):
        now = self.clock()
        return [r for r in rows if r['status'] not in STOPPED
                and r['kickoff'] - 60 <= now <= r['kickoff'] + 21600]

    def refresh(self, cache, automatic=False):
        now = self.clock()
        active = self.active(cache['rows'])
        ids = [r['id'] for r in active if now - cache['checked'].get(str(r['id']), 0) >= 60]
        leagues = self.leagues(automatic)
        for offset in range(0, len(ids), 20):
            chunk = ids[offset:offset + 20]
            raw = self._request('fixtures', {'ids': '-'.join(map(str, chunk)), 'timezone': 'Europe/Moscow'}, automatic)
            rows = self._fixtures(raw, leagues)
            if {r['id'] for r in rows} != set(chunk):
                raise FootballError('incomplete')
            updated = {r['id']: r for r in rows}
            cache['rows'] = [updated.get(r['id'], r) for r in cache['rows']]
            cache['checked'].update({str(r['id']): self.clock() for r in rows})
            self.store.set('football_schedule', cache)
        return cache

    def interval(self, rows):
        now = self.clock()
        windows = sorted((max(now, r['kickoff'] - 60), max(r['kickoff'] + 14400, now + 120))
                         for r in rows if r['status'] not in STOPPED and r['kickoff'] + 21600 >= now)
        seconds, end = 0, now
        for start, stop in windows:
            seconds += max(0, stop - max(start, end))
            end = max(end, stop)
        batches = max(1, math.ceil(len(windows) / 20))
        return max(120, math.ceil(seconds * batches / max(1, self.remaining() - RESERVE - 2)))

    def answer(self, request):
        with self.lock:
            if not self.key:
                raise FootballError('key_missing')
            cache = self.refresh(self.schedule(refresh=True))
            today = cache['day']
            rows = [r for r in cache['rows'] if datetime.fromtimestamp(r['kickoff'], MOSCOW).date().isoformat() == today]
            if request.get('competition'):
                rows = [r for r in rows if r['competition'] == request['competition']]
            if request.get('team'):
                rows = [r for r in rows if any(alias in (r['home'] + ' ' + r['away']).casefold() for alias in request['team'])]
            lines = [f'<b>⚽ РПЛ и Кубок России · {datetime.fromtimestamp(self.clock(), MOSCOW):%d.%m.%Y}</b>']
            for competition in ('РПЛ', 'Кубок России'):
                group = sorted((r for r in rows if r['competition'] == competition), key=lambda r: r['kickoff'])
                if group:
                    lines += ['', f'<b>{competition}</b>']
                    omitted = 0
                    for row in group:
                        line = match_line(row)
                        if sum(len(part) + 1 for part in lines) + len(line) < 3300:
                            lines.append(line)
                        else:
                            omitted += 1
                    if omitted:
                        lines.append(f'Ещо матчей: {omitted}; уточни команду.')
            if not rows:
                lines.append('\nСегодня подходящих матчей в этих турнирах нет.')
            checked = min((cache['checked'][str(r['id'])] for r in rows), default=cache['updated'])
            lines.append(f'\n<i>API-Football · проверено {datetime.fromtimestamp(checked, MOSCOW):%H:%M МСК}</i>')
            lines.append('<i>ну шо диванные тренеры счёт перед вами</i>')
            return HTMLMessage('\n'.join(lines))

    def tick(self, chat_id):
        """Claim observed changes before Telegram send; never replay on restart."""
        with self.lock:
            now = self.clock()
            if not self.key or now < self.store.get('football_next', 0):
                return []
            # Prevent loops on provider failure; no hidden transport retries.
            self.store.set('football_next', now + 300)
            cache = self.schedule(automatic=True)
            cache = self.refresh(cache, automatic=True)
            self.store.set('football_next', now + self.interval(cache['rows']))
            key = f'football_seen:{chat_id}'
            old = self.store.get(key)
            seen, events = {}, []
            for row in cache['rows']:
                fixture_id = str(row['id'])
                seen[fixture_id] = {'status': row['status'], 'goals': row['goals'], 'penalties': row['penalties']}
                previous = (old or {}).get(fixture_id)
                if previous is None:
                    continue  # First snapshot is a baseline, including already live games.
                title = None
                if row['status'] in FINISHED and previous['status'] not in STOPPED:
                    title = '🏁 Матч окончен'
                elif row['status'] in LIVE and None not in row['goals']:
                    before = previous['goals']
                    if None in before and previous['status'] == 'NS':
                        before = [0, 0]
                    if None not in before and before != row['goals']:
                        title = '⚽ Счёт изменился' if sum(row['goals']) > sum(before) else '↩️ Коррекция счёта'
                if title:
                    checked = cache['checked'].get(fixture_id, 0)
                    if now - checked < 600 and now - row['kickoff'] < 21600:
                        events.append(HTMLMessage(f'<b>{title} · {row["competition"]}</b>\n{match_line(row)}\n'
                                                 f'<i>API-Football · {datetime.fromtimestamp(checked, MOSCOW):%H:%M МСК}</i>\n'
                                                 '<i>ну шо ти теперь скажеш</i>'))
            self.store.set(key, seen)
            return events

    def status(self):
        enabled = self.store.get('football_enabled', bool(self.key))
        return ('Футбол: ' + ('ключ не настроен' if not self.key else 'уведомления включены' if enabled else 'уведомления выключены')
                + f' · запросов осталось: {self.remaining()}/{LIMIT} (10 зарезервированы для команд)')
