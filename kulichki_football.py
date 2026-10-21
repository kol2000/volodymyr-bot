"""The editorial daily list on FNK's homepage and its own text broadcasts."""
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from live_football import (ACTIVE, FINISHED, CACHE_SECONDS, IDLE_INTERVAL, MOSCOW,
                           MONTHS, PREFIX, Document, Node, Match, Goal, FootballError,
                           goals_between, near_match, text)

BASE = 'https://football.kulichki.net'
NAME = 'Футбол на Куличках'
LOG = logging.getLogger('volodymyr')
PATH = re.compile(r'/trans/(20\d{2})/(\d+)-[A-Za-z0-9-]+\.htm$')


def valid_url(url):
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.netloc == 'football.kulichki.net' and PATH.fullmatch(parts.path)


def fetch_html(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'football.kulichki.net' or not (parts.path == '/' or PATH.fullmatch(parts.path)):
        raise FootballError('invalid_match_url')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'volodymyr-bot/1.0 (+https://github.com/kol2000/volodymyr-bot)',
        'Accept': 'text/html', 'Cache-Control': 'no-cache, max-age=0', 'Pragma': 'no-cache'})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            final = urlsplit(response.url)
            if final.scheme != 'https' or final.netloc != parts.netloc or final.path != parts.path:
                raise FootballError('unexpected_redirect')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise FootballError('unexpected_content_type')
            data = response.read(4_000_001)
            if len(data) > 4_000_000:
                raise FootballError('page_too_large')
            age = response.headers.get('Age', '0')
            if age.isdigit() and int(age) > 180:
                raise FootballError('stale_page')
            return data.decode(response.headers.get_content_charset() or 'windows-1251')
    except urllib.error.HTTPError as error:
        raise FootballError(f'http_{error.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FootballError('network_error') from None
    except (UnicodeError, LookupError):
        raise FootballError('invalid_encoding') from None


def parse_home(html):
    root = Document(html).root
    # Pick the smallest table headed exactly this way, never navigation/news links.
    tables = [n for n in root.walk() if n.tag == 'table' and any(
        x.tag == 'th' and text(x) == 'Онлайн трансляции.' for x in n.walk())]
    if not tables:
        raise FootballError('missing_daily_selection')
    table = min(tables, key=lambda n: len(list(n.walk())))
    competition, found = '', {}
    for row in (n for n in table.walk() if n.tag == 'tr'):
        headings = [n for n in row.children if isinstance(n, Node) and n.tag == 'th']
        if headings:
            heading = text(headings[0])
            if heading != 'Онлайн трансляции.':
                competition = heading[:120]
            continue
        cells = [n for n in row.children if isinstance(n, Node) and n.tag == 'td']
        if not cells:
            continue
        for anchor in (n for n in cells[0].walk() if n.tag == 'a'):
            label = re.fullmatch(r'(.+?)\s+-\s+(.+?)\s+-\s+(\d{1,2}:\d{1,2})(?:\s.*)?', text(anchor))
            if not label:
                continue
            url = urljoin(BASE, anchor.attrs.get('href', ''))
            clock = re.match(r'(\d{2}:\d{2})\.', text(cells[0]))
            if not valid_url(url) or urlsplit(url).query or urlsplit(url).fragment or not clock or not competition:
                raise FootballError('invalid_daily_match')
            hour, minute = map(int, clock[1].split(':'))
            if hour > 23 or minute > 59 or any(len(n) > 80 for n in label.groups()[:2]):
                raise FootballError('invalid_daily_match')
            path = PATH.fullmatch(urlsplit(url).path)
            item = dict(id=f'fnk:{path[1]}:{path[2]}', url=url, home=label[1], away=label[2],
                        competition=competition, clock=clock[1])
            if item['id'] in found and found[item['id']] != item:
                raise FootballError('conflicting_match')
            found[item['id']] = item
    if len(found) > 40:
        raise FootballError('too_many_daily_matches')
    return list(found.values())


def parse_broadcast(html, selected):
    root = Document(html).root
    title = next((text(n) for n in root.walk() if n.tag == 'title'), '')
    title_match = re.fullmatch(r'Онлайн трансляция матча (.+?) - (.+?): (\d{1,2}) ([а-я]+) (20\d{2}), .+', title)
    if not title_match or title_match.groups()[:2] != (selected['home'], selected['away']):
        raise FootballError('wrong_broadcast')
    try:
        day, month, year = title_match.groups()[2:]
        hour, minute = map(int, selected['clock'].split(':'))
        kickoff = datetime(int(year), MONTHS.index(month) + 1, int(day), hour, minute, tzinfo=MOSCOW).timestamp()
    except (ValueError, TypeError):
        raise FootballError('invalid_match_date') from None
    board = [n for n in root.walk() if n.tag == 'td' and any(
        c.tag == 'font' and c.attrs.get('size') == '6' for c in n.walk())]
    if not board:
        raise FootballError('missing_match_score')
    board = min(board, key=lambda n: len(list(n.walk())))
    if selected['home'] + ' - ' + selected['away'] not in text(board):
        raise FootballError('wrong_broadcast')
    scores = [text(n) for n in board.walk() if n.tag == 'font' and n.attrs.get('size') == '6']
    score = re.fullmatch(r'(\d{1,2})\s*:\s*(\d{1,2})', scores[0]) if len(scores) == 1 else None
    if not score or any(int(n) > 30 for n in score.groups()):
        raise FootballError('invalid_score')
    feed = next((n for n in root.walk() if n.tag == 'table' and n.attrs.get('id') == 'table15'), None)
    if feed is None:
        raise FootballError('missing_broadcast')
    events = []
    phase_icons = {'end2.gif', 'end1.gif', '1t.gif', '2t.gif'}
    for row in (n for n in feed.walk() if n.tag == 'tr'):
        cells = [n for n in row.children if isinstance(n, Node) and n.tag == 'td']
        if len(cells) != 3:
            continue
        clock = text(cells[0])
        icons = [urlsplit(n.attrs.get('src', '')).path.rsplit('/', 1)[-1].lower()
                 for n in cells[1].walk() if n.tag == 'img']
        timed = re.fullmatch(r'\d{1,3}(?:\+\d{1,2})?|\+\d{1,2}', clock)
        # FNK also posts period whistles with an empty minute cell.
        if not timed and not (clock in ('', '-', '—') and phase_icons.intersection(icons)):
            continue
        events.append((clock, icons, text(cells[2])))
    # A stale/mislabelled end1 icon must not reopen halftime after the second
    # half. Use source markers/minutes, never elapsed wall time or score alone.
    second_half = any('2t.gif' in icons or
                      (re.match(r'^\d', clock) and int(clock.split('+')[0]) > 45)
                      for clock, icons, _ in events)
    eligible = {'end2.gif', '2t.gif'} if second_half else phase_icons
    phase = next((icon for _, icons, _ in events for icon in icons
                  if icon in eligible), '2t.gif' if second_half else '')
    status = {'end2.gif': FINISHED, 'end1.gif': 'break', '1t.gif': 'live', '2t.gif': 'live'}.get(
        phase, 'live' if events else 'scheduled')
    label = {FINISHED: 'Матч завершён', 'break': 'Перерыв', 'scheduled': 'Матч ещё не начался'}.get(status, '')
    if status == 'live':
        clock = next((clock for clock, _, _ in events
                      if re.fullmatch(r'\d{1,3}(?:\+\d{1,2})?|\+\d{1,2}', clock)), '')
        if clock.startswith('+'):
            clock = ('90' if phase == '2t.gif' else '45') + clock
        label = clock + '′ · матч идёт' if clock else 'Матч идёт'
    goals = broadcast_goals(root, events, selected['id'])
    home, away = (int(n) for n in score.groups())
    if status == 'scheduled':
        home = away = None  # A preview's 0:0 is not a confirmed live score.
    return Match(selected['id'], selected['competition'], selected['home'], selected['away'],
                 kickoff, status, label, home, away, selected['url'], goals=goals, source=NAME)


def broadcast_goals(root, events, match_id):
    descriptions = {}
    for _, icons, description in events:
        if not set(icons) & {'gooal.gif', 'owngoal.gif', 'pen.gif', 'pengoal.gif'}:
            continue
        score = re.search(r'(?<!\d)(\d{1,2}):(\d{1,2})(?!\d)', description)
        if score:
            descriptions.setdefault(tuple(map(int, score.groups())), []).append(description)
    goals = []
    summaries = [n for n in root.walk() if n.tag == 'td' and any(
        x.tag == 'img' and x.attrs.get('src', '').lower() == '/trans/goal.gif' for x in n.walk())]
    if not summaries:
        return ()
    summary = min(summaries, key=lambda n: len(list(n.walk())))
    flat = text(summary)
    for anchor in (n for n in summary.walk() if n.tag == 'a' and re.fullmatch(r'/players/\d+\.htm', n.attrs.get('href', ''))):
        name = text(anchor)
        pattern = (re.escape(name) + r'\s*(?:\(([^)]+)\))?\s*,\s*(\d{1,3})(?:\+(\d{1,2}))?'
                   r'(?:\s*[-–]\s*([^()]+?))?\s*\((\d{1,2}):(\d{1,2})\)')
        for found in re.finditer(pattern, flat):
            qualifier, minute, bonus, suffix, home, away = found.groups()
            score = (int(home), int(away))
            description = descriptions.get(score, [])
            if len(description) != 1 or not 1 <= int(minute) <= 120:
                continue
            names = {text(n) for n in root.walk() if n.tag == 'a' and n.attrs.get('href') == anchor.attrs['href'] and 0 < len(text(n)) <= 120}
            player = max(names, key=len) if names else name
            qualifier = ((qualifier or '') + ' ' + (suffix or '')).casefold()
            kind = 'own-goal' if 'авт' in qualifier else 'penalty-goal' if 'пен' in qualifier else 'goal'
            when = minute + (f'+{bonus}' if bonus else '') + '′'
            goals.append(Goal(f'{match_id}:{home}:{away}:{when}', score, when, player, kind,
                              description[0][:600]))
    return tuple({goal.id: goal for goal in goals}.values())


class KulichkiScoreboard:
    name = NAME

    def __init__(self, store=None, fetch=fetch_html, clock=time.time):
        self.store, self.fetch, self.clock = store, fetch, clock
        self.lock = threading.Lock()
        self.cache, self.matches, self.final = {}, {}, {}
        self.home_at, self.home_list = None, []
        self.last_error, self.last_success = '', 0
        if store:
            for row in store.get(PREFIX + 'fnk_selection', []):
                try:
                    if not valid_url(row['url']) or not row['id'].startswith('fnk:'):
                        continue
                    self.matches[row['id']] = Match(**row)
                except (ValueError, TypeError, KeyError):
                    continue

    def day(self, day):
        with self.lock:
            now = self.clock()
            cached = self.cache.get(day)
            if cached and now - cached[0] < CACHE_SECONDS:
                return cached[1], cached[2]
            due = self.home_at is None or now - self.home_at >= IDLE_INTERVAL
            if due:
                self.home_list = parse_home(self.fetch(BASE + '/?_=' + str(time.time_ns())))
                self.home_at = self.clock()
            selected = {row['id']: row for row in self.home_list}
            for match in self.matches.values():
                # Keep previously selected ongoing games across the midnight homepage rollover.
                if match.id not in selected and match.status in ACTIVE and 0 <= now - match.kickoff < 6 * 3600:
                    selected[match.id] = self.selection(match)
            candidates = [row for key, row in selected.items() if key not in self.final and
                          (due or key not in self.matches or near_match(self.matches[key], now))]
            def refresh(row):
                try:
                    started = time.monotonic()
                    match = replace(parse_broadcast(self.fetch(row['url'] + '?_=' + str(time.time_ns())), row),
                                    checked=self.clock())
                    LOG.info('Футбол ФНК: матч=%s; статус=%s; счет=%s:%s; получено=%s; запрос=%.3f сек.',
                             match.id, match.status, match.home_score, match.away_score,
                             datetime.fromtimestamp(match.checked, MOSCOW).isoformat(), time.monotonic() - started)
                    return match, ''
                except FootballError as error:
                    LOG.warning('Трансляция ФНК недоступна: матч=%s; причина=%s', row['id'], error)
                    return row['id'], str(error)
            with ThreadPoolExecutor(max_workers=4) as executor:
                refreshed = list(executor.map(refresh, candidates))
            failed = set()
            self.last_error = ''
            for match, error in refreshed:
                if error:
                    failed.add(match)
                    self.last_error = error
                    continue
                self.matches[match.id] = match
                if match.status == FINISHED and len(goals_between(match, [0, 0])) == match.home_score + match.away_score:
                    self.final[match.id] = match
            self.matches = {key: m for key, m in self.matches.items() if m.kickoff >= now - 8 * 86400
                            and (key in selected or m.status == FINISHED or m.status in ACTIVE)}
            self.final = {key: m for key, m in self.final.items() if key in self.matches}
            if self.store:
                self.store.set(PREFIX + 'fnk_selection', [dict(id=m.id, competition=m.competition,
                    home=m.home, away=m.away, kickoff=m.kickoff, status=m.status, label=m.label,
                    home_score=m.home_score, away_score=m.away_score, url=m.url, source=NAME, checked=m.checked)
                    for m in self.matches.values()])
            result = sorted((m for key, m in self.matches.items() if key not in failed and
                             datetime.fromtimestamp(m.kickoff, MOSCOW).date() == day),
                            key=lambda m: (m.kickoff, m.id))
            if failed and not result:
                raise FootballError(self.last_error)
            checked = self.clock()
            self.last_success = checked
            self.cache[day] = (checked, result, checked)
            self.cache = {d: item for d, item in self.cache.items() if (day - d).days <= 8}
            return result, checked

    @staticmethod
    def selection(match):
        return dict(id=match.id, url=match.url, home=match.home, away=match.away,
                    competition=match.competition,
                    clock=datetime.fromtimestamp(match.kickoff, MOSCOW).strftime('%H:%M'))
