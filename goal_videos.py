"""Attach public Telegram goal clips to the bot's existing FNK goal cards."""
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape, unescape
from urllib.parse import urlsplit

from common import APIError, HTMLMessage, visible_text
from football_policy import excluded_competition
from live_football import Document, FootballError, Node, PREFIX

LOG = logging.getLogger('volodymyr')
CHANNELS = ('matchpremier', 'idmanvideo')
KEY = PREFIX + 'videos:'
INTERVAL = 30
TTL = 2 * 60 * 60
FINAL_SCAN_WINDOW = 5 * 60
RPL_RECOVERY_AFTER = 90
MAX_VIDEO = 45 * 1024 * 1024
MAX_PAGE = 2 * 1024 * 1024


class VideoError(Exception):
    """Fixed diagnostic codes only: never a signed media URL."""


def media_url(value):
    try:
        u = urlsplit(value)
        return bool(u.scheme == 'https' and not u.username and not u.password
                    and u.port in (None, 443) and not u.fragment
                    and re.fullmatch(r'cdn\d+\.telesco\.pe', u.hostname or '')
                    and u.path.startswith('/file/'))
    except ValueError:
        return False


def page_url(value):
    try:
        u = urlsplit(value)
        return u.scheme == 'https' and u.netloc == 't.me' and u.path in {
            '/s/' + channel for channel in CHANNELS} and (
                not u.query or re.fullmatch(r'before=[1-9]\d{0,11}', u.query)) and not u.fragment
    except ValueError:
        return False


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not (media_url(newurl) if media_url(request.full_url) else page_url(newurl)):
            raise VideoError('unexpected_redirect')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_public(url, video=False):
    if not (media_url(url) if video else page_url(url)):
        raise VideoError('invalid_source_url')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                                 'Cache-Control': 'no-cache'})
    limit = MAX_VIDEO if video else MAX_PAGE
    try:
        with urllib.request.build_opener(Redirects()).open(request, timeout=20 if video else 12) as response:
            if not (media_url(response.url) if video else page_url(response.url)):
                raise VideoError('unexpected_redirect')
            content_type = response.headers.get_content_type()
            if content_type not in (('video/mp4', 'application/octet-stream') if video else ('text/html',)):
                raise VideoError('unexpected_content_type')
            size = response.headers.get('Content-Length', '')
            if size.isdigit() and int(size) > limit:
                raise VideoError('video_too_large' if video else 'page_too_large')
            chunks, total, deadline = [], 0, time.monotonic() + (120 if video else 20)
            while True:
                if time.monotonic() > deadline:
                    raise VideoError('download_timeout')
                chunk = response.read(min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise VideoError('video_too_large' if video else 'page_too_large')
            data = b''.join(chunks)
    except urllib.error.HTTPError as error:
        raise VideoError(f'http_{error.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise VideoError('network_error') from None
    if video:
        if len(data) < 12 or data[4:8] != b'ftyp':
            raise VideoError('not_mp4')
        return data
    try:
        return data.decode('utf-8')
    except UnicodeError:
        raise VideoError('invalid_encoding') from None


@dataclass(frozen=True)
class Clip:
    channel: str
    post_id: int
    timestamp: float
    text: str
    url: str

    @property
    def link(self):
        return f'https://t.me/{self.channel}/{self.post_id}'


def own_post_nodes(post):
    """Telegram reuses message_text inside replies: keep only this post's content."""
    def walk(node):
        if node is not post and (node.attrs.get('data-post') or node.tag == 'blockquote' or any(
                cls.startswith(('tgme_widget_message_reply', 'tgme_widget_message_quote',
                                'tgme_widget_message_link_preview'))
                for cls in node.attrs.get('class', '').split())):
            return
        yield node
        for child in node.children:
            if isinstance(child, Node):
                yield from walk(child)
    return walk(post)


def parse_channel(html, channel, skipped=None):
    if channel not in CHANNELS or len(html.encode('utf-8')) > MAX_PAGE:
        raise VideoError('invalid_channel_page')
    root = Document(html).root
    posts = [n for n in root.walk() if n.has('tgme_widget_message') and n.attrs.get('data-post')]
    if not posts:
        raise VideoError('channel_preview_unavailable')
    clips = []
    for post in posts[:100]:
        identifier = re.fullmatch(re.escape(channel) + r'/(\d+)', post.attrs['data-post'])
        if not identifier:
            continue
        own = list(own_post_nodes(post))
        bodies = [n for n in own if n.has('tgme_widget_message_text')]
        times = [n.attrs['datetime'] for n in own if n.tag == 'time' and n.attrs.get('datetime')]
        videos = [n.attrs.get('src', '') for n in own if n.tag == 'video']
        if not bodies or len(set(times)) != 1:
            continue
        try:
            stamp = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                continue
            # Multi-goal albums/compilations cannot be assigned to one goal safely.
            reason = ('protected_post' if post.attrs.get('data-protected') in ('1', 'true') else
                      'ambiguous_caption' if len(bodies) != 1 else
                      'no_native_video' if not videos else
                      'multiple_videos' if len(videos) != 1 else
                      'unsupported_media_url' if not media_url(videos[0]) else '')
            clip = Clip(channel, int(identifier[1]), stamp.timestamp(), bodies[-1].text()[:3000],
                        videos[0] if not reason else '')
            if reason:
                if skipped is not None:
                    skipped.append((clip, reason))
            else:
                clips.append(clip)
        except (ValueError, TypeError, OverflowError):
            continue
    return sorted({clip.post_id: clip for clip in clips}.values(), key=lambda clip: clip.post_id, reverse=True)


class PublicClips:
    def __init__(self, fetch=fetch_public, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self.cached_at, self.cached, self.errors = None, [], {}
        self.history_at, self.history_cached, self.history_errors = None, [], {}

    def history(self, pages=4):
        """Bounded final sweep, including pages with no native video posts."""
        now = self.clock()
        if self.history_at is not None and now-self.history_at < 60:
            self.errors = dict(self.history_errors)
            return self.history_cached
        def read(channel):
            found, cursor, error = {}, None, ''
            try:
                for _ in range(min(4, max(1, pages))):
                    html = self.fetch('https://t.me/s/'+channel+(f'?before={cursor}' if cursor else ''))
                    for clip in parse_channel(html, channel):
                        found[clip.post_id] = clip
                    ids = [int(m[1]) for n in Document(html).root.walk()
                           if (m := re.fullmatch(re.escape(channel)+r'/(\d+)', n.attrs.get('data-post', '')))]
                    if not ids or (cursor is not None and min(ids) >= cursor):
                        break
                    cursor = min(ids)
            except VideoError as exc:
                error = str(exc)
            except (FootballError, ValueError, RecursionError):
                error = 'invalid_channel_page'
            return list(found.values()), error, channel
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(read, CHANNELS))
        self.history_cached = [c for clips, _, _ in rows for c in clips]
        self.history_errors = {channel: error for _, error, channel in rows if error}
        self.history_at, self.errors = now, dict(self.history_errors)
        return self.history_cached

    def recent(self):
        now = self.clock()
        if self.cached_at is not None and now - self.cached_at < INTERVAL:
            return self.cached
        def read(channel):
            try:
                return channel, parse_channel(self.fetch('https://t.me/s/' + channel), channel), ''
            except VideoError as error:
                return channel, [], str(error)
            except (FootballError, ValueError, RecursionError):
                return channel, [], 'invalid_channel_page'
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(read, CHANNELS))
        self.cached, self.errors = [], {}
        for channel, clips, error in rows:
            self.cached.extend(clips)
            if error:
                self.errors[channel] = error
        self.cached_at = now
        return self.cached


CYRILLIC = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                    ('a','b','v','g','d','e','e','zh','z','i','y','k','l','m','n','o','p','r','s','t',
                     'u','f','h','ts','ch','sh','shch','','y','','e','yu','ya')))
ALIASES = {
    'реал мадрид': ('real madrid',), 'лейпциг': ('leipzig', 'rb leipzig', 'leypsiq', 'leyptsiq'),
    'бавария': ('bayern', 'bayern munich', 'bavariya'), 'арсенал': ('arsenal',),
    'псж': ('psg',), 'барселона': ('barselona', 'barcelona'), 'ливерпуль': ('liverpool',),
    'галатасарай': ('galatasaray', 'qalatasaray'), 'лиль': ('lille', 'lill'), 'лилль': ('lille', 'lill'),
    'манчестер юнайтед': ('manchester united', 'man united', 'mançester yunayted', 'му', 'мю'),
    'манчестер сити': ('manchester city', 'man city', 'mançester siti'),
    'астон вилла': ('aston villa',), 'челси': ('chelsea', 'celsi'),
    'тоттенхэм': ('tottenham',), 'интер': ('inter',), 'ювентус': ('juventus', 'yuventus'),
    'бенфика': ('benfica', 'benfika'), 'атлетик': ('athletic', 'atletik klub'),
    'атлетико': ('atletico', 'atletiko'), 'карабах': ('qarabag', 'karabakh'),
    'зенит': ('zenit',), 'спартак': ('spartak',), 'цска': ('cska',), 'комо': ('como',),
    'динамо москва': ('dinamo moscow', 'dinamo moskva'), 'родина': ('rodina',),
    'локомотив': ('lokomotiv',), 'ростов': ('rostov',), 'кайрат': ('kairat', 'kayrat'),
    'крылья советов': ('крылья', 'krylya sovetov'),
}


def norm(value):
    value = value.casefold().replace('ı', 'i').replace('ə', 'e')
    value = ''.join(CYRILLIC.get(c, c) for c in value)
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def named(text, name):
    variants = (name, *ALIASES.get(name.casefold(), ()))
    return any(' ' + norm(v) + ' ' in ' ' + text + ' ' for v in variants if len(norm(v)) >= 3)


def minute_values(text):
    values = []
    for match in re.finditer(r'(?<!\d)(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\s*(?:[′’\x27`]|(?:-?[йяюе]?\s*)?минут\w*)', text, re.I):
        value = int(match[1]) + int(match[2] or 0)
        if 1 <= value <= 140:
            values.append(value)
    return set(values)


def russian_named(text, name):
    """Bounded case endings for proper names; no fuzzy edit-distance matching."""
    name = name.casefold().replace('ё', 'е')
    text = text.casefold().replace('ё', 'е')
    if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text):
        return True
    if any(re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', text)
           for alias in ALIASES.get(name, ()) if re.fullmatch(r'[а-я ]+', alias)):
        return True
    if not re.fullmatch(r'[а-я]{4,}', name):
        return False
    stem = name[:-1] if name.endswith(('а', 'я')) else name
    return bool(re.search(r'(?<!\w)' + re.escape(stem) + r'(?:а|я|у|ю|е|ы|и|ой|ом|ою|ей|ем)(?!\w)', text))


def narrative_goal_reason(clip, job):
    """Domestic broadcaster's scoreless captions need a complete FNK goal path."""
    if clip.channel != 'matchpremier' or not re.search(
            r'^(?:Премьер-лига\b|РПЛ\b|Чемпионат России\b)', job.get('competition', ''), re.I):
        return 'insufficient_goal_identity'
    goals = job.get('match_goals', [])
    previous, sides = (0, 0), []
    for goal in goals:
        score = tuple(goal['score'])
        delta = tuple(a-b for a, b in zip(score, previous))
        if delta not in ((1, 0), (0, 1)):
            return 'narrative_context_missing'
        sides.append(delta.index(1))
        previous = score
    if not goals or previous != tuple(job.get('match_score', ())):
        return 'narrative_context_missing'
    text = clip.text.casefold().replace('ё', 'е')
    if re.search(r'начинал|отбор|разбор|повтор|вспомина|год назад|лучшие голы|почти|едва\s+не|чуть\s+не|мимо|перекладин|штанг|'
                 r'не\s+(?:забил|забит|забивает|сравнял|помог|замкнул|вколотил|поразил|расстрелял|отправил)|мог\s+забить|забил\s+бы', text):
        return 'narrative_not_goal_clip'
    ordinal = {'первый': 1, 'второй': 2, 'третий': 3, 'четвертый': 4, 'пятый': 5,
               'шестой': 6, 'седьмой': 7}
    first = re.search(r'\b(' + '|'.join(ordinal) + r')\s+гол\b', text)
    conceded = re.search(r'\b(' + '|'.join(ordinal) + r')\s+(?:пропущенн\w*\s+у|в\s+воротах)\s+[«"]?([а-я]+)', text)
    draw = bool(re.search(r'\bсравнял[аи]?\b', text))
    seconds = re.search(r'(?<!\w)([1-9]\d{0,2})\s*секунд\w*\b', text)
    cue = bool(re.search(r'\bгол\b|\bзабил[аи]?\b|\bсравнял[аи]?\b|\bвпереди\b', text))
    candidates = []
    for goal, side in zip(goals, sides):
        minute = next(iter(minute_values(goal['minute'])), None)
        # Scoreless captions must be near this goal's playing time. Unlike exact
        # captions they cannot identify a delayed retrospective by themselves.
        base_minute = re.match(r'\d+', goal['minute'])
        second_half = base_minute and int(base_minute[0]) > 45
        expected = job['kickoff'] + 60 * (minute or 0) + (900 if second_half else 0)
        if minute is None or not -180 <= clip.timestamp - expected <= 1200:
            continue
        score = goal['score']
        if draw and score[0] != score[1]:
            continue
        if 'с пенальти' in text and goal['kind'] != 'penalty-goal':
            continue
        if re.search(r'\bвпереди\b', text) and score[side] <= score[1-side]:
            continue
        if first and sum(score) != ordinal[first[1]]:
            continue
        if conceded and (not russian_named(conceded[2], (job['home'], job['away'])[1-side])
                         or score[side] != ordinal[conceded[1]]):
            continue
        team = (job['home'], job['away'])[side]
        scoring_team = russian_named(text, team)
        surname = goal['player'].split()[-1] if goal['player'].split() else ''
        scorer = len(surname) >= 4 and russian_named(text, surname)
        direct_scorer = scorer and cue and (scoring_team or draw)
        # Broadcaster: "40 СЕКУНД!!! ЦСКА УЖЕ ВПЕРЕДИ!!!". Elapsed
        # seconds identify a playing minute, not the clip publication delay.
        timed_lead = bool(seconds and scoring_team and re.search(r'\bвпереди\b', text)
                          and not re.search(r'секунд\w*\s+до\b|\b(?:осталось|через)\b',text)
                          and minute == (int(seconds[1])+59)//60
                          and score[side] == score[1-side]+1)
        # A native RPL clip may describe only the finishing action or the
        # provider's decisive pass. Require a unique confirmed FNK episode,
        # a narrow playing-time window and independently named participants.
        close_episode = -180 <= clip.timestamp-expected <= 600
        finish = bool(close_episode and scorer and re.search(
            r'\b(?:замкнул|вколотил|поразил|расстрелял)\b|отправил.{0,35}в\s+сетк',text))
        own_goal = bool(scorer and scoring_team and goal['kind'] == 'own-goal' and re.search(
            r'срезал\s+в\s+свои\b|в\s+свои\s+ворота\b|\bавтогол\b', text))
        penalty = bool(first and scoring_team and 'пенальти' in text and goal['kind'] == 'penalty-goal')
        conceded_goal = bool(conceded)
        # "Акрону помог рикошет. У Лунёва не было шансов": a team alone is
        # insufficient; require the same ricochet and a separate named person
        # in FNK's own episode description, with a positive scoring assertion.
        description = goal.get('description', '')
        participants=[]
        for person in re.finditer(r'\b[А-ЯЁ][а-яё]{3,}\b',description):
            if re.search(r'(?:^|[.!?])\s*$',description[:person.start()]):
                continue
            name=person[0]
            if not any(russian_named(name, value) for value in (job['home'],job['away'],*goal['player'].split())):
                participants.append(name)
        assist = bool(close_episode and scoring_team and
            re.search(r'\b(?:отрезал|разрезал|вывел|выдал)\b.{0,55}(?:передач|пас|заброс)|'
                      r'(?:передач|пас|заброс).{0,55}\b(?:отрезал|разрезал|вывел)\b',text) and
            re.search(r'передач|пас|заброс|навес',description,re.I) and
            any(russian_named(text,person) for person in participants))
        people = [w for w in re.findall(r'\b[А-ЯЁ][а-яё]{4,}\b', description)
                  if not russian_named(w, job['home']) and not russian_named(w, job['away'])]
        ricochet = bool(scoring_team and re.search(r'помог\s+рикошет', text)
                        and re.search(r'рикошет', description, re.I)
                        and any(russian_named(text, person) for person in people))
        if direct_scorer or timed_lead or finish or assist or own_goal or penalty or conceded_goal or ricochet:
            candidates.append((tuple(score), goal['minute'], goal['player']))
    target = job['goal']
    identity = (tuple(target['score']), target['minute'], target['player'])
    if len(candidates) > 1:
        return 'ambiguous_narrative_goal'
    return '' if candidates == [identity] else 'insufficient_narrative_identity'


def observed_goal_finish(clip, job):
    """Fallback for native RPL captions near the recorded goal-card time."""
    text = clip.text.casefold().replace('ё', 'е')
    if not -180 <= clip.timestamp-job['created'] <= 300 or re.search(
            r'почти|едва|чуть не|повтор|разбор|вспомина|обзор|отмен|офсайд|не засчита|'
            r'мимо|штанг|перекладин|не\s+(?:вонзил|замкнул|забил|отправил|реализовал|точен|попал)|'
            r'не\s+очень|плохо|отбил|сейв|сэйв|спас|назнач|заработал|пробьет|будет\s+бить|начинал|\bбы\b', text):
        return False
    finish = bool(re.search(r'\b(?:вонзил|вколотил|забил|замкнул|поразил)\b|'
                     r'отправил.{0,40}(?:в\s+девятку|в\s+сетку|в\s+ворота)', text))
    penalty = bool(re.search(r'\bреализовал\s+пенальти\b|\bточен\s+с\s+точки\b|'
                            r'\bхорошо\s+бьет\s+пенальти\b',text))
    numbers = {'ноль':0,'один':1,'два':2,'три':3,'четыре':4,'пять':5,
               'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10}
    words = '|'.join(numbers)
    scores = {(numbers[m[1]],numbers[m[2]]) for m in
              re.finditer(r'\b('+words+r')\s+('+words+r')\b',text)}
    candidates = []
    previous = [0,0]
    ordinals = {'первый':1,'второй':2,'третий':3,'четвертый':4,'пятый':5}
    ordinal = re.search(r'\b('+'|'.join(ordinals)+r')\b',text)
    for goal in job.get('match_goals', []):
        delta = [a-b for a,b in zip(goal['score'],previous)]
        previous = goal['score']
        if delta not in ([1,0],[0,1]):return False
        side = delta.index(1)
        penalty_finish = (penalty and goal['kind']=='penalty-goal'
                          and russian_named(text,(job['home'],job['away'])[side])
                          and (not ordinal or goal['score'][side]==ordinals[ordinal[1]]))
        surname = goal['player'].split()[-1:]
        if ((finish or penalty_finish) and surname and len(surname[0]) >= 4 and russian_named(text, surname[0])
                and (not scores or scores == {tuple(goal['score'])})):
            candidates.append((tuple(goal['score']),goal['minute'],goal['player']))
    target = job['goal']
    return candidates == [(tuple(target['score']),target['minute'],target['player'])]


def goal_match_reason(clip, job, now):
    """Empty for a match; fixed codes for diagnostic use, never signed URLs."""
    if clip.timestamp < max(job['kickoff'] - 300, job['created'] - 600):
        return 'publication_before_window'
    if clip.timestamp > now + 120:
        return 'future_publication'
    text = clip.text.casefold()
    if re.search(r'отмен[её]н|отмена|офсайд|вне игры|disallowed|no goal|l[eə]ğv|обзор|highlights|все голы', text):
        return 'cancelled_or_compilation'
    # An aggregate score in parentheses is not the score of this fixture.
    scoring = re.sub(r'\(\s*\d{1,2}\s*:\s*\d{1,2}\s*\)', '', text)
    scores = {tuple(map(int, m)) for m in re.findall(r'(?<!\d)(\d{1,2})\s*[:–-]\s*(\d{1,2})(?!\d)', scoring)}
    target = tuple(job['goal']['score'])
    if scores and scores != {target}:
        return 'score_mismatch'
    normalized = norm(text)
    teams = int(named(normalized, job['home'])) + int(named(normalized, job['away']))
    minutes = minute_values(text)
    target_minute = next(iter(minute_values(job['goal']['minute'])), None)
    if minutes and (target_minute is None or minutes != {target_minute}):
        # Sources sometimes label the elapsed/started minute differently. Accept
        # only a one-minute difference with both teams AND the exact goal score.
        if not (target_minute is not None and len(minutes) == 1 and teams == 2
                and scores == {target} and abs(next(iter(minutes)) - target_minute) == 1):
            return 'minute_mismatch'
    surname = norm(job['goal']['player']).split()[-1:]
    scorer = bool(surname and len(surname[0]) >= 4 and surname[0] in normalized.split())
    cue = bool(re.search(r'гол|go+a+l|qol|сравнял|удвоил|выводит.*вперед|вывел.*впер[её]д', text))
    matched = ((teams == 2 and (scores or minutes)) or
               (teams >= 1 and scorer and (scores or minutes)) or
               (teams >= 1 and minutes and cue) or (scorer and scores and minutes))
    if matched:
        return ''
    reason = narrative_goal_reason(clip, job) if not scores and not minutes else 'insufficient_goal_identity'
    if reason == 'insufficient_narrative_identity' and observed_goal_finish(clip, job):
        return ''
    return reason


def matches_goal(clip, job, now):
    return not goal_match_reason(clip, job, now)


def caption(job, clip=None):
    """Keep the original goal card and one short source footer; no service links."""
    plain = unescape(re.sub(r'<[^>]+>', '', job.get('render_original', job['original'])))
    lines, footer = [], 'Футбол на Куличках'
    for line in plain.splitlines():
        line = line.rstrip()
        if re.match(r'^\s*(?:ФНК:|Видео:\s*@|https?://)', line):
            continue
        if re.match(r'^\s*(?:Футбол на Куличках|Спорт-Экспресс)\s*·\s*проверено\b', line):
            footer = line.strip()
            continue
        lines.append(line)
    plain = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    source = '\n\n' + footer
    units = lambda s: len(s.encode('utf-16-le')) // 2
    def formatted(body):
        url = job.get('match_url', '')
        parsed = urlsplit(url)
        name, _, rest = footer.partition(' · ')
        label = escape(name)
        if parsed.scheme == 'https' and parsed.netloc == 'football.kulichki.net' and parsed.path.startswith('/trans/'):
            label = f'<a href="{escape(url, quote=True)}">{label}</a>'
        tail = label + (' · ' + escape(rest) if rest else '')
        lines = []
        teams = job.get('home', '') + ' — ' + job.get('away', '')
        for line in body.splitlines():
            value = escape(line)
            if line.startswith('⚽') or (teams.strip(' —') and line.startswith(teams)) or re.match(r'^\d+(?:\+\d+)?′ · (?:Гол|Автогол)', line):
                value = '<b>' + value + '</b>'
            lines.append(value)
        return HTMLMessage('\n'.join(lines) + '\n\n<i>' + tail + '</i>')
    if units(plain + source) <= 1024:
        return formatted(plain)
    goal = job['goal']
    plain = (f'⚽ {job["home"]} — {job["away"]} · {goal["score"][0]}:{goal["score"][1]}\n'
             f'{job["competition"]}\n' + '\n'.join(
                 f'{g["minute"]} · {g["player"]} · {g["score"][0]}:{g["score"][1]}' for g in job['goals'])
             + '\n' + goal['description'])
    while units(plain + source) > 1024:
        plain = plain[:-2]
    return formatted(plain.rstrip())


class GoalVideos:
    def __init__(self, store, source=None, download=None, clock=time.time):
        self.store, self.clock = store, clock
        self.source = source or PublicClips(clock=clock)
        self.download = download or (lambda url: fetch_public(url, video=True))
        self.lock = threading.Lock()
        self.next_poll = 0
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS goal_video_jobs (
                    chat_id INTEGER, message_id INTEGER, match_id TEXT, created REAL,
                    expires REAL, payload TEXT, status TEXT, PRIMARY KEY(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS goal_video_used (
                    chat_id INTEGER, post TEXT, digest TEXT, created REAL,
                    PRIMARY KEY(chat_id,post));
                CREATE INDEX IF NOT EXISTS goal_video_digest ON goal_video_used(chat_id,digest);
                CREATE TABLE IF NOT EXISTS goal_video_diagnostics (
                    chat_id INTEGER, message_id INTEGER, checked REAL, reason TEXT,
                    detail TEXT, final_checked REAL DEFAULT 0,
                    PRIMARY KEY(chat_id,message_id));
            ''')
            # A crash after editing must not repeat work or create a replacement post.
            db.execute("UPDATE goal_video_jobs SET status='uncertain' WHERE status='editing'")

    def enabled(self):
        return self.store.get(KEY + 'enabled', True) and self.store.get(PREFIX + 'enabled', False)

    def answer(self, request, chat_id):
        if request['kind'] == 'football_video_switch':
            with self.lock:
                enabled = request['enabled']
                self.store.set(KEY + 'enabled', enabled)
                if not enabled:
                    self.cancel()
            return ('Видео голов включены: ролики добавляются в исходные сообщения Бубуса.' if enabled
                    else 'Добавление видео голов выключено.')
        with self.store.db() as db:
            waiting = db.execute("SELECT count(*) FROM goal_video_jobs WHERE chat_id=? AND status='pending'", (chat_id,)).fetchone()[0]
            diagnostics = db.execute('SELECT message_id,reason,detail FROM goal_video_diagnostics WHERE chat_id=? ORDER BY checked DESC LIMIT 5', (chat_id,)).fetchall()
        labels = dict(waiting='подходящий ролик пока не найден', source_unavailable='источник недоступен',
                      identity_unresolved='есть связанный ролик, эпизод не подтверждён',
                      context_missing='неполные подробности гола', ambiguous='неоднозначное совпадение',
                      download_failed='не удалось скачать видео', used='ролик уже использован',
                      telegram_failed='Telegram отклонил загрузку', uncertain='результат загрузки не подтверждён',
                      attached='видео добавлено', expired='срок ожидания истёк', cancelled='задание отменено')
        details = '\n'.join(f'Пост {n}: {labels.get(reason, reason)}' +
                            (f'; {labels[detail]}' if reason=='expired' and detail in labels else '')
                            for n, reason, detail in diagnostics)
        return ('Видео голов: ' + ('включены' if self.enabled() else 'выключены') +
                '\nИсточники: @matchpremier, @idmanvideo\n'
                f'Ожидают ролик: {waiting}\nПоследняя ошибка: {self.store.get(KEY + "error", "") or "нет"}\n'
                f'Проверка каждые 30 секунд при ожидающих голах; ожидание до {TTL // 60} минут.\n'
                'РПЛ: через 90 секунд повторно просматриваем до 4 страниц; кэш ленты — 60 секунд.\n'
                'За 5 минут до конца — повторная сверка до 4 страниц каждого канала.\n' +
                (details+'\n' if details else '') +
                'Видео добавляется в исходное сообщение без пересылки и нового поста.')

    def diagnose(self, row, reason, detail='', final=False):
        with self.store.db() as db:
            old=db.execute('SELECT reason,detail FROM goal_video_diagnostics WHERE chat_id=? AND message_id=?',(row[0],row[1])).fetchone()
            db.execute('INSERT INTO goal_video_diagnostics VALUES (?,?,?,?,?,?) '
                       'ON CONFLICT(chat_id,message_id) DO UPDATE SET checked=excluded.checked,reason=excluded.reason,detail=excluded.detail,final_checked=max(final_checked,excluded.final_checked)',
                       (row[0],row[1],self.clock(),reason,detail,self.clock() if final else 0))
        if reason in ('identity_unresolved','source_unavailable','download_failed') and old != (reason,detail):
            LOG.warning('Мониторинг видео: пост=%s; причина=%s',row[1],reason)

    def cancel(self):
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='cancelled' WHERE status='pending'")

    def refresh_match_context(self, chat_id, match):
        """Refresh waiting clips from the live source, even without a card edit."""
        if excluded_competition(match.competition):
            return
        context = dict(match_goals=[asdict(g) for g in match.goals], match_score=[match.home_score, match.away_score])
        with self.store.db() as db:
            for number, raw in db.execute("SELECT message_id,payload FROM goal_video_jobs WHERE chat_id=? AND match_id=? AND status='pending'", (chat_id, match.id)).fetchall():
                job = json.loads(raw)
                if all(job.get(k) == v for k, v in context.items()):
                    continue
                job.update(context)
                db.execute('UPDATE goal_video_jobs SET payload=? WHERE chat_id=? AND message_id=?',
                           (json.dumps(job, ensure_ascii=False), chat_id, number))

    def enqueue(self, chat_id, match, goals, message_id, original, created=None):
        if excluded_competition(match.competition):
            return
        if not goals or not self.enabled():
            return
        now = self.clock() if created is None else created
        # Telegram permits one media in a formerly textual message. If several
        # goals share one FNK card, attach the latest goal; never replace it later.
        payload = dict(home=match.home, away=match.away, competition=match.competition,
                       kickoff=match.kickoff, match_url=match.url, original=str(original),
                       goals=[asdict(g) for g in goals], goal=asdict(goals[-1]), created=now,
                       match_goals=[asdict(g) for g in match.goals], match_score=[match.home_score, match.away_score])
        with self.store.db() as db:
            # Earlier pending cards must compare against later goals too (for
            # example the same player scoring twice). Never rewrite their target.
            for number, raw in db.execute("SELECT message_id,payload FROM goal_video_jobs WHERE chat_id=? AND match_id=? AND status='pending'", (chat_id, match.id)).fetchall():
                existing = json.loads(raw)
                existing.update(match_goals=payload['match_goals'], match_score=payload['match_score'])
                db.execute('UPDATE goal_video_jobs SET payload=? WHERE chat_id=? AND message_id=?',
                           (json.dumps(existing, ensure_ascii=False), chat_id, number))
            db.execute('INSERT INTO goal_video_jobs VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT(chat_id,message_id) DO UPDATE SET payload=excluded.payload '
                       "WHERE goal_video_jobs.status='pending'",
                       (chat_id, message_id, match.id, now, now + TTL, json.dumps(payload, ensure_ascii=False), 'pending'))

    def current(self, row, allowed):
        chat, message, match_id, created, expires, payload, status = row
        if excluded_competition(json.loads(payload).get('competition')):
            return False
        if not self.enabled() or chat != self.store.get('chat_id') or not allowed(chat) or self.clock() >= expires:
            return False
        score = self.store.get(PREFIX + f'seen:{chat}', {}).get(match_id, {}).get('score')
        target = json.loads(payload)['goal']['score']
        return bool(score and None not in score and all(s >= g for s, g in zip(score, target)))

    def mark(self, row, status):
        with self.store.db() as db:
            db.execute('UPDATE goal_video_jobs SET status=? WHERE chat_id=? AND message_id=?', (status, row[0], row[1]))

    def poll(self, edit, allowed):
        now = self.clock()
        if not self.enabled() or now < self.next_poll or now < self.store.get('blocked_until', 0):
            return
        self.next_poll = now + INTERVAL
        with self.store.db() as db:
            db.execute('DELETE FROM goal_video_jobs WHERE created<?', (now - 2 * 86400,))
            db.execute('DELETE FROM goal_video_used WHERE created<?', (now - 2 * 86400,))
            db.execute('DELETE FROM goal_video_diagnostics WHERE checked<?', (now - 2 * 86400,))
            rows = db.execute("SELECT * FROM goal_video_jobs WHERE status='pending' ORDER BY created LIMIT 30").fetchall()
        current = []
        for row in rows:
            if self.current(row, allowed):
                current.append((row, json.loads(row[5])))
            else:
                self.mark(row, 'cancelled')
                with self.store.db() as db:
                    previous = db.execute('SELECT reason FROM goal_video_diagnostics WHERE chat_id=? AND message_id=?', (row[0],row[1])).fetchone()
                self.diagnose(row, 'expired' if now >= row[4] else 'cancelled', previous[0] if previous else '')
        if not current:
            return
        clips = self.source.recent()
        with self.store.db() as db:
            scanned = dict(db.execute('SELECT message_id,final_checked FROM goal_video_diagnostics WHERE chat_id=?', (self.store.get('chat_id'),)))
        sweep = [row for row, _ in current if row[4]-now <= FINAL_SCAN_WINDOW and not scanned.get(row[1])]
        recovery=any(now-row[3]>=RPL_RECOVERY_AFTER and not scanned.get(row[1]) and re.match(r'^(?:Премьер-лига|РПЛ|Чемпионат России)\b',job.get('competition',''),re.I)
                     for row,job in current)
        if (sweep or recovery) and hasattr(self.source, 'history'):
            history = self.source.history()
            clips = list({c.link:c for c in [*clips,*history]}.values())
            for row in sweep:
                self.diagnose(row, 'waiting', final=not self.source.errors)
        self.store.set(KEY + 'checked', now)
        self.store.set(KEY + 'error', '; '.join(f'{c}:{e}' for c, e in self.source.errors.items()))
        for row, job in current:
            reasons = [goal_match_reason(c, job, now) for c in clips]
            reason = ('source_unavailable' if self.source.errors else
                      'context_missing' if 'narrative_context_missing' in reasons else
                      'ambiguous' if 'ambiguous_narrative_goal' in reasons else 'waiting')
            related=[c for c in clips if c.channel=='matchpremier' and row[3]-180<=c.timestamp<=row[3]+900 and
                     (russian_named(c.text,job['home']) or russian_named(c.text,job['away']) or
                      any(russian_named(c.text,name) for name in job['goal']['player'].split()[-1:]))]
            if reason=='waiting' and related and not any(not r for r in reasons):
                reason='identity_unresolved'
            self.diagnose(row, reason, '; '.join(sorted(set(reasons))))
            # Preference for the domestic broadcaster; the second source remains
            # useful for European matches and as a fallback when a clip is missing.
            options = [c for c in clips if matches_goal(c, job, now)
                       and sum(matches_goal(c, other, now) for _, other in current) == 1]
            options.sort(key=lambda c: (CHANNELS.index(c.channel), -c.post_id))
            if any(not goal_match_reason(c,job,now) for c in clips) and not options:
                self.diagnose(row, 'ambiguous')
            for clip in options:
                with self.store.db() as db:
                    used = db.execute('SELECT 1 FROM goal_video_used WHERE chat_id=? AND post=?', (row[0], clip.link)).fetchone()
                if used:
                    self.diagnose(row, 'used')
                    continue
                try:
                    data = self.download(clip.url)
                except VideoError as error:
                    self.diagnose(row, 'download_failed', str(error))
                    self.store.set(KEY + 'error', str(error))
                    continue
                digest = hashlib.sha256(data).hexdigest()
                with self.lock:
                    # Description may have grown while the video was downloading.
                    with self.store.db() as db:
                        fresh = db.execute('SELECT * FROM goal_video_jobs WHERE chat_id=? AND message_id=?', (row[0], row[1])).fetchone()
                    if not fresh or fresh[-1] != 'pending':
                        break
                    row, job = fresh, json.loads(fresh[5])
                    if not matches_goal(clip, job, self.clock()):
                        break
                    if not self.current(row, allowed) or self.clock() < self.store.get('blocked_until', 0):
                        break
                    with self.store.db() as db:
                        duplicate = db.execute('SELECT 1 FROM goal_video_used WHERE chat_id=? AND digest=?', (row[0], digest)).fetchone()
                        if duplicate:
                            db.execute('INSERT OR IGNORE INTO goal_video_used VALUES (?,?,?,?)', (row[0], clip.link, digest, now))
                        else:
                            db.execute('INSERT OR IGNORE INTO goal_video_used VALUES (?,?,?,?)', (row[0], clip.link, digest, now))
                            claimed = db.execute("UPDATE goal_video_jobs SET status='editing' WHERE chat_id=? AND message_id=? AND status='pending'", (row[0], row[1])).rowcount
                    if duplicate:
                        self.diagnose(row, 'used')
                        continue
                    if not claimed:
                        break
                    try:
                        edit(row[0], row[1], data, caption(job, clip))
                    except APIError as error:
                        if error.retry_after:
                            self.store.set('blocked_until', self.clock() + error.retry_after)
                        if error.code == 429:
                            self.diagnose(row, 'telegram_failed', 'http_429')
                            with self.store.db() as db:
                                db.execute('DELETE FROM goal_video_used WHERE chat_id=? AND post=?', (row[0], clip.link))
                            self.mark(row, 'pending')
                            self.next_poll = self.clock() + max(INTERVAL, error.retry_after)
                            break
                        # Editing never sends a new post. Do not delete/repost or
                        # blindly retry an upload whose result was not confirmed.
                        self.mark(row, 'uncertain' if not error.code or error.code >= 500 else 'failed')
                        self.store.set(KEY + 'error', f'edit_http_{error.code}')
                        self.diagnose(row, 'uncertain' if not error.code or error.code >= 500 else 'telegram_failed', f'http_{error.code}')
                        LOG.warning('Добавление видео не подтверждено: HTTP=%s; пост=%s', error.code, row[1])
                    else:
                        self.mark(row, 'attached')
                        self.diagnose(row, 'attached')
                        with self.store.db() as db:
                            columns = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
                            if 'body' in columns:
                                plain = visible_text(caption(job, clip))
                                db.execute('UPDATE messages SET text=?,body=? WHERE chat_id=? AND message_id=? AND human=0',
                                           (plain[:1000], plain[:3000], row[0], row[1]))
                        self.store.set(KEY + 'error', '')
                        LOG.info('Видео гола добавлено в исходный пост=%s; источник=@%s/%s', row[1], clip.channel, clip.post_id)
                    break
                del data
