"""Public match-centre scores for RPL/Russian Cup; no API key or LLM."""
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from common import HTMLMessage

LOG = logging.getLogger('volodymyr')
MOSCOW = ZoneInfo('Europe/Moscow')
BASE = 'https://www.sport-express.net'
PREFIX = 'livefootball:'  # Retired API-Football cleanup must not touch this state.
COMMANDS = {'/football', '/football_on', '/football_off', '/football_status'}
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
ACTIVE = {'live', 'break', 'penalties'}
FINISHED = 'finished'


class FootballError(Exception):
    """A fixed diagnostic code, never an arbitrary response body or URL."""


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, cls):
        return cls in self.attrs.get('class', '').split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find(self, cls):
        return next((node for node in self.walk() if node.has(cls)), None)

    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        return ' '.join(child.text() if isinstance(child, Node) else child
                        for child in self.children)


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.nodes = 0
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100000 or len(self.stack) > 200:
            raise FootballError('page_too_complex')
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def text(node):
    return ' '.join(node.text().split()) if node else ''


def status_code(label):
    value = label.casefold().replace('ё', 'е')
    if re.search(r'заверш|окончен', value):
        return FINISHED
    if re.search(r'отлож|перенес|отмен|прерван|приостанов', value):
        return 'paused'
    if re.search(r'не нач|ожида|анонс', value):
        return 'scheduled'
    if 'перерыв' in value:
        return 'break'
    if 'пенальти' in value:
        return 'penalties'
    if (re.search(r'тайм|идет|игра|live', value)
            or re.fullmatch(r"\d{1,3}(?:\s*\+\s*\d{1,2})?\s*(?:['’′]|мин\.?)", value)):
        return 'live'
    return 'unknown'


@dataclass(frozen=True)
class Match:
    id: str
    competition: str
    home: str
    away: str
    kickoff: float
    status: str
    label: str
    home_score: int | None
    away_score: int | None
    url: str
    extra: str = ''

    def state(self):
        return {'status': self.status, 'score': [self.home_score, self.away_score],
                'extra': self.extra, 'kickoff': self.kickoff}


def parse_page(html, day):
    """Read only score cards below the exact supported competition headings."""
    root = Document(html).root
    centre = root.find('se-matchcenter-sports-list')
    if not centre:
        raise FootballError('unexpected_page')
    expected = f'{day.day:02d} {MONTHS[day.month - 1]} {day.year}'
    actual = text(centre.find('se-matchcenter-sports-list__date'))
    if actual.lstrip('0') != expected.lstrip('0'):
        raise FootballError('wrong_page_date')
    matches = {}
    for group in centre.walk():
        if not group.has('se-competition-titled-block'):
            continue
        title = text(group.find('se-competition-titled-block__title')).casefold()
        if re.fullmatch(r'чемпионат россии\. премьер-лига', title):
            competition = 'РПЛ'
        elif re.fullmatch(r'(?:fonbet |фонбет )?кубок россии', title):
            competition = 'Кубок России'
        else:
            continue
        for card in group.walk():
            if card.tag != 'a' or not card.has('se-matchcenter-matches__match'):
                continue
            url = card.attrs.get('href', '')
            if url.startswith('/'):
                url = BASE + url
            parsed = urlsplit(url)
            identifier = re.search(r'/football/.*/match-[\w-]+-(\d+)/?$', parsed.path)
            if (parsed.scheme != 'https' or parsed.netloc != 'www.sport-express.net'
                    or not identifier or parsed.query or parsed.fragment):
                raise FootballError('invalid_match_url')
            names = [text(node) for node in card.walk()
                     if node.has('se-matchcenter-matches__match-team__name')]
            clock = text(card.find('se-matchcenter-matches__match-date'))
            label = text(card.find('se-matchcenter-matches__match-status'))
            score_text = text(card.find('se-matchcenter-matches__match-score'))
            if len(names) != 2 or any(not name or len(name) > 80 for name in names):
                raise FootballError('invalid_match_teams')
            if not re.fullmatch(r'\d{2}:\d{2}', clock) or not label or len(label) > 80:
                raise FootballError('invalid_match_status')
            hour, minute = map(int, clock.split(':'))
            if hour > 23 or minute > 59:
                raise FootballError('invalid_match_time')
            kickoff = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW).timestamp()
            status = status_code(label)
            score = re.fullmatch(r'(\d{1,2})\s*:\s*(\d{1,2})(.*)', score_text)
            if score:
                home_score, away_score = map(int, score.group(1, 2))
                extra = score.group(3).strip()
                if max(home_score, away_score) > 30 or len(extra) > 80:
                    raise FootballError('invalid_score')
            elif re.fullmatch(r'-\s*:\s*-', score_text) and status in ('scheduled', 'paused', 'unknown'):
                home_score = away_score = None
                extra = ''
            else:
                raise FootballError('missing_score')
            # Some sites print 0:0 in previews; a scheduled match has no live score.
            if status == 'scheduled':
                home_score = away_score = None
                extra = ''
            item = Match(identifier[1], competition, *names, kickoff, status, label,
                         home_score, away_score, url, extra)
            if item.id in matches and matches[item.id] != item:
                raise FootballError('conflicting_match')
            matches[item.id] = item
    return sorted(matches.values(), key=lambda match: (match.kickoff, match.id))


def fetch_html(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'volodymyr-bot/1.0 (+https://github.com/kol2000/volodymyr-bot)',
        'Accept': 'text/html', 'Cache-Control': 'no-cache',
    })
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            final = urlsplit(response.url)
            if final.scheme != 'https' or final.netloc != 'www.sport-express.net':
                raise FootballError('unexpected_redirect')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise FootballError('unexpected_content_type')
            data = response.read(4_000_001)
            if len(data) > 4_000_000:
                raise FootballError('page_too_large')
            # A successful HTTP response can still be an old CDN snapshot.
            age = response.headers.get('Age', '0')
            if age.isdigit() and int(age) > 180:
                raise FootballError('stale_page')
            return data.decode(response.headers.get_content_charset() or 'utf-8')
    except urllib.error.HTTPError as error:
        raise FootballError(f'http_{error.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FootballError('network_error') from None
    except (UnicodeError, LookupError):
        raise FootballError('invalid_encoding') from None


class Scoreboard:
    def __init__(self, fetch=fetch_html, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self.cache = {}
        self.lock = threading.Lock()
        self.requests = 0
        self.last_success = 0
        self.last_error = ''

    def day(self, day):
        with self.lock:
            now = self.clock()
            saved = self.cache.get(day)
            if saved and now - saved[0] < 60:
                if isinstance(saved[1], FootballError):
                    raise saved[1]
                return saved[1], saved[0]
            self.requests += 1
            try:
                html = self.fetch(BASE + '/live/football/' + day.strftime('%d-%m-%Y') + '/')
                matches = parse_page(html, day)
            except FootballError as error:
                self.last_error = str(error)
                self.cache[day] = (now, error)
                raise
            self.last_success, self.last_error = self.clock(), ''
            self.cache[day] = (self.last_success, matches)
            for old in sorted(self.cache)[:-8]:
                del self.cache[old]
            return matches, self.last_success


def football_request(value, command='', addressed=False, now=None):
    if command and command not in COMMANDS:
        return None
    body = value.strip()
    if command in COMMANDS:
        body = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ''
    else:
        nickname = re.match(r'^(?:володька|володько|бубус|бубуська)[\s,:!]+', body, re.I)
        if nickname:
            body, addressed = body[nickname.end():], True
        if not addressed:
            return None
        match = re.match(r'^(?:какой\s+сч[её]т|сч[её]т(?:\s+матч[аей]+)?|футбол|'
                         r'что\s+там\s+футбол|как\s+игра(?:ет|ют)|'
                         r'кто\s+играет(?:\s+в\s+футбол)?|результаты\s+матчей|'
                         r'что\s+с\s+футболом)\b[\s,:!?]*(.*)$', body, re.I)
        if not match:
            return None
        body = match[1]
    if command in ('/football_on', '/football_off'):
        return {'kind': 'football_switch', 'enabled': command == '/football_on'}
    if command == '/football_status':
        return {'kind': 'football_status'}
    day = datetime.fromtimestamp(time.time() if now is None else now, MOSCOW).date()
    body = body.strip(' ,.!?')
    dates = re.findall(r'(?<!\w)(\d{1,2}\.\d{1,2}(?:\.\d{4})?)(?!\w)', body)
    relative = re.findall(r'\b(сегодня|завтра|вчера)\b', body, re.I)
    if len(dates) + len(relative) > 1:
        return {'kind': 'football_invalid'}
    if dates:
        try:
            parts = list(map(int, dates[0].split('.')))
            day = day.replace(year=parts[2] if len(parts) == 3 else day.year, month=parts[1], day=parts[0])
        except ValueError:
            return {'kind': 'football_invalid'}
        body = body.replace(dates[0], '')
    elif relative:
        day += timedelta(days={'сегодня': 0, 'завтра': 1, 'вчера': -1}[relative[0].lower()])
        body = re.sub(r'\b(?:сегодня|завтра|вчера)\b', '', body, flags=re.I)
    body = re.sub(r'\b(?:в|у|матче|матча|игры|играет|покажи|сейчас)\b', '', body, flags=re.I)
    if len(body) > 100:
        return {'kind': 'football_invalid'}
    return {'kind': 'football', 'day': day.isoformat(), 'filter': ' '.join(body.split()).strip(' ,.!?')}


def normalize(value):
    return ' '.join(re.findall(r'\w+', value.casefold().replace('ё', 'е')))


def select_matches(matches, query):
    terms = normalize(query).split()
    if not terms:
        return matches
    terms = [word for word in terms if word not in ('и', 'против', 'vs')]
    return [match for match in matches if all(word in normalize(
        f'{match.home} {match.away} {match.competition}').split() for word in terms)]


def match_line(match):
    teams = f'{escape(match.home)} — {escape(match.away)}'
    if match.status == 'scheduled':
        clock = datetime.fromtimestamp(match.kickoff, MOSCOW).strftime('%H:%M')
        return f'🕒 <b>{teams}</b>\nНачало: {clock} МСК · матч ещё не начался'
    score = (f'{match.home_score}:{match.away_score}'
             if match.home_score is not None and match.away_score is not None else 'счет не подтверждён')
    extra = f' {escape(match.extra)}' if match.extra else ''
    return f'<b>{teams} · {score}</b>{extra}\n{escape(match.label)}'


def scoreboard_text(matches, day, checked, query=''):
    selected = select_matches(matches, query)
    lines = [f'⚽ <b>РПЛ и Кубок России · {day.strftime("%d.%m.%Y")}</b>']
    if not selected:
        lines.append('На эту дату подходящих матчей нет.' if query else 'На эту дату матчей этих турниров нет.')
    displayed = 0
    for match in selected[:12]:
        line = f'\n{escape(match.competition)}\n{match_line(match)}'
        if len('\n'.join(lines)) + len(line) > 3500:
            break
        lines.append(line)
        displayed += 1
    if len(selected) > displayed:
        lines.append(f'\nЕщё матчей: {len(selected) - displayed}. Уточни команду: /football Ростов')
    stamp = datetime.fromtimestamp(checked, MOSCOW).strftime('%H:%M:%S МСК')
    lines.append(f'\n<i>Источник: Спорт-Экспресс · проверено {stamp}</i>')
    return HTMLMessage('\n'.join(lines))


def changes(previous, match, now):
    """State changes, never inferred clock transitions or invented goal scorers."""
    if match.status == 'unknown':
        return []
    if previous is None:
        return [('live', '⚽ Матч сейчас идёт')] if match.status in ACTIVE and now - match.kickoff < 6 * 3600 else []
    old_status, old_score = previous.get('status'), previous.get('score')
    current = [match.home_score, match.away_score]
    events = []
    if match.status in ACTIVE and old_status in ('scheduled', 'paused', 'unknown'):
        events.append(('start', '⚽ Матч начался' if old_status == 'scheduled' else '⚽ Матч продолжается'))
    if old_score != current and None not in current and old_score and None not in old_score:
        if match.status in ACTIVE or (match.status == FINISHED and old_status in ACTIVE):
            events.append(('score', '⚽ Счет изменился'))
    if match.status == 'break' and old_status != 'break':
        events.append(('break', '⏸ Перерыв'))
    if match.status == 'penalties' and old_status != 'penalties':
        events.append(('penalties', '🥅 Серия пенальти'))
    if match.status == FINISHED and old_status != FINISHED and now - match.kickoff < 6 * 3600:
        events.append(('finish', '🏁 Матч завершён'))
    if match.status == 'paused' and old_status != 'paused':
        events.append(('paused', '📌 Статус матча изменился'))
    if match.extra != previous.get('extra', '') and match.status in ACTIVE and not events:
        events.append(('score', '⚽ Данные счета обновились'))
    # If score and period change arrived in one poll, send one combined card.
    if len(events) > 1:
        return [(events[-1][0], ' · '.join(title for _, title in events))]
    return events


QUIPS = {'start': 'ну шо побегайте хоть нормально', 'live': 'шо там уже начали без меня',
         'score': 'ну хоть ворота нашли наконец то', 'break': 'шо устали уже бегать',
         'penalties': 'ну сечас начнется нервотрепка', 'finish': 'всё разбегайтесь эксперты диванные',
         'paused': 'шо опять всё через одно место'}


def event_text(match, event, checked):
    kind, title = event
    stamp = datetime.fromtimestamp(checked, MOSCOW).strftime('%H:%M:%S МСК')
    return HTMLMessage(f'<b>{escape(title)}</b>\n{escape(match.competition)}\n{match_line(match)}\n'
                       f'\n<i>{QUIPS[kind]}</i>\n<i><a href="{escape(match.url, quote=True)}">'
                       f'Спорт-Экспресс</a> · проверено {stamp}</i>')


class Football:
    def __init__(self, store, source=None, clock=time.time):
        self.store, self.source, self.clock = store, source or Scoreboard(), clock
        self.lock = threading.Lock()

    def enabled(self):
        return bool(self.store.get(PREFIX + 'enabled', False))

    def answer(self, request, chat_id):
        kind = request['kind']
        if kind == 'football_invalid':
            return 'укажи одну дату например /football сегодня или /football 09.10 Ростов'
        target = self.store.get('chat_id')
        if kind == 'football_switch' and not request['enabled']:
            with self.lock:
                self.store.set(PREFIX + 'enabled', False)
            return 'Футбольные уведомления выключены. /football продолжает показывать счета по запросу.'
        if kind == 'football_status':
            stamp = self.store.get(PREFIX + 'success', 0)
            last = datetime.fromtimestamp(stamp, MOSCOW).strftime('%d.%m %H:%M:%S МСК') if stamp else 'ещё нет'
            reason = self.store.get(PREFIX + 'error', '') or 'нет'
            return ('Футбол: ' + ('включён' if self.enabled() else 'выключен') + '\n'
                    'Источник: Спорт-Экспресс; только РПЛ и Кубок России\n'
                    f'Последняя проверка: {last}\nПоследняя ошибка: {reason}\n'
                    'При матчах: проверка раз в 90 секунд; вне игр — раз в 15 минут.\n'
                    'Фактическая задержка зависит от обновления источника.')
        now = self.clock()
        day = datetime.fromtimestamp(now, MOSCOW).date()
        if kind == 'football_switch' and not target:
            return 'Сначала подключи группу командой /bubus, затем /football_on.'
        if kind == 'football':
            day = datetime.strptime(request['day'], '%Y-%m-%d').date()
        matches, checked = self.source.day(day)
        self.store.set(PREFIX + 'success', checked)
        self.store.set(PREFIX + 'error', '')
        if kind == 'football_switch':
            # Establish a baseline before enabling: no replay of earlier results.
            with self.lock:
                if target != self.store.get('chat_id'):
                    raise FootballError('chat_changed')
                if not self.enabled():
                    self.store.set(PREFIX + f'seen:{target}', {m.id: dict(m.state(), checked=checked) for m in matches})
                self.store.set(PREFIX + 'enabled', True)
                self.store.set(PREFIX + 'next_poll', 0)
            return HTMLMessage('⚽ <b>Уведомления включены в подключённой группе</b>\n'
                               'Начало, изменение счета, перерыв и финал.\n'
                               'Только РПЛ и Кубок России; /football_off — выключить.\n\n'
                               + scoreboard_text(matches, day, checked))
        return scoreboard_text(matches, day, checked, request.get('filter', ''))

    def poll(self, send, allowed):
        """Single poll; state reserved before send, including uncertain Telegram sends."""
        now = self.clock()
        chat_id = self.store.get('chat_id')
        if not self.enabled() or not chat_id or not allowed(chat_id):
            return
        if now < self.store.get(PREFIX + 'next_poll', 0) or now < self.store.get('blocked_until', 0):
            return
        day = datetime.fromtimestamp(now, MOSCOW).date()
        seen = self.store.get(PREFIX + f'seen:{chat_id}', {})
        days = [day]
        if any(row.get('status') in ACTIVE | {'scheduled'} and 0 <= now - row.get('kickoff', 0) < 6 * 3600
               and datetime.fromtimestamp(row['kickoff'], MOSCOW).date() < day for row in seen.values()):
            days.insert(0, day - timedelta(days=1))
        snapshots = []
        try:
            for requested_day in days:
                items, checked = self.source.day(requested_day)
                snapshots.extend((match, checked) for match in items)
                self.store.set(PREFIX + 'success', checked)
            self.store.set(PREFIX + 'error', '')
        except FootballError as error:
            self.store.set(PREFIX + 'error', str(error))
            self.store.set(PREFIX + 'next_poll', now + 300)
            LOG.warning('Футбольный источник недоступен: причина=%s', error)
            return
        active = any(match.status in ACTIVE or (match.status in ('scheduled', 'paused', 'unknown')
                     and -6 * 3600 <= match.kickoff - now <= 15 * 60) for match, _ in snapshots)
        self.store.set(PREFIX + 'next_poll', now + (90 if active else 900))
        LOG.info('Футбольная проверка: матчей=%s; интервал=%s сек.', len(snapshots), 90 if active else 900)
        for match, checked in snapshots:
            with self.lock:
                if not self.enabled() or chat_id != self.store.get('chat_id') or not allowed(chat_id):
                    return
                if self.clock() < self.store.get('blocked_until', 0):
                    return
                latest = self.store.get(PREFIX + f'seen:{chat_id}', {})
                previous = latest.get(match.id)
                if previous and previous.get('checked', 0) > checked:
                    continue
                # A source can briefly regress a final status to live. Never replay it.
                if previous and previous.get('status') == FINISHED:
                    continue
                events = changes(previous, match, now)
                latest[match.id] = dict(match.state(), checked=checked)
                latest = {key: row for key, row in latest.items() if row.get('kickoff', 0) >= now - 2 * 86400}
                self.store.set(PREFIX + f'seen:{chat_id}', latest)
                if events:
                    # Lock serializes /football_off with the send and state reservation.
                    send(chat_id, event_text(match, events[0], checked))


def error_reply():
    return 'счет сечас не подтверждён источник недоступен попробуй /football позже'
