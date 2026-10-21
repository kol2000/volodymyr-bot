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
from html import unescape
from urllib.parse import urlsplit

from common import APIError
from live_football import Document, FootballError, PREFIX

LOG = logging.getLogger('volodymyr')
CHANNELS = ('matchpremier', 'idmanvideo')
KEY = PREFIX + 'videos:'
INTERVAL = 30
TTL = 30 * 60
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
            '/s/' + channel for channel in CHANNELS} and not u.query and not u.fragment
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
            chunks, total, deadline = [], 0, time.monotonic() + (60 if video else 20)
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


def parse_channel(html, channel):
    if channel not in CHANNELS or len(html.encode('utf-8')) > MAX_PAGE:
        raise VideoError('invalid_channel_page')
    root = Document(html).root
    posts = [n for n in root.walk() if n.has('tgme_widget_message') and n.attrs.get('data-post')]
    if not posts:
        raise VideoError('channel_preview_unavailable')
    clips = []
    for post in posts[:100]:
        identifier = re.fullmatch(re.escape(channel) + r'/(\d+)', post.attrs['data-post'])
        if not identifier or post.attrs.get('data-protected') in ('1', 'true'):
            continue
        body = post.find('tgme_widget_message_text')
        times = [n.attrs['datetime'] for n in post.walk() if n.tag == 'time' and n.attrs.get('datetime')]
        videos = [n.attrs.get('src', '') for n in post.walk() if n.tag == 'video']
        # Multi-goal albums/compilations cannot be assigned to one goal safely.
        if len(videos) != 1 or not media_url(videos[0]) or not body or not times:
            continue
        try:
            stamp = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                continue
            clips.append(Clip(channel, int(identifier[1]), stamp.timestamp(), body.text()[:3000], videos[0]))
        except (ValueError, TypeError, OverflowError):
            continue
    return sorted({clip.post_id: clip for clip in clips}.values(), key=lambda clip: clip.post_id, reverse=True)


class PublicClips:
    def __init__(self, fetch=fetch_public, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self.cached_at, self.cached, self.errors = None, [], {}

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
    'галатасарай': ('galatasaray', 'qalatasaray'), 'лиль': ('lille',), 'лилль': ('lille',),
    'манчестер юнайтед': ('manchester united', 'man united', 'му', 'мю'),
    'манчестер сити': ('manchester city', 'man city'), 'челси': ('chelsea', 'celsi'),
    'тоттенхэм': ('tottenham',), 'интер': ('inter',), 'ювентус': ('juventus', 'yuventus'),
    'бенфика': ('benfica', 'benfika'), 'атлетик': ('athletic', 'atletik klub'),
    'атлетико': ('atletico', 'atletiko'), 'карабах': ('qarabag', 'karabakh'),
    'зенит': ('zenit',), 'спартак': ('spartak',), 'цска': ('cska',), 'комо': ('como',),
    'динамо москва': ('dinamo moscow', 'dinamo moskva'), 'родина': ('rodina',),
    'локомотив': ('lokomotiv',), 'ростов': ('rostov',), 'кайрат': ('kairat', 'kayrat'),
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


def matches_goal(clip, job, now):
    if not max(job['kickoff'] - 300, job['created'] - 600) <= clip.timestamp <= now + 120:
        return False
    text = clip.text.casefold()
    if re.search(r'отмен[её]н|отмена|офсайд|вне игры|disallowed|no goal|l[eə]ğv|обзор|highlights|все голы', text):
        return False
    # An aggregate score in parentheses is not the score of this fixture.
    scoring = re.sub(r'\(\s*\d{1,2}\s*:\s*\d{1,2}\s*\)', '', text)
    scores = {tuple(map(int, m)) for m in re.findall(r'(?<!\d)(\d{1,2})\s*[:–-]\s*(\d{1,2})(?!\d)', scoring)}
    target = tuple(job['goal']['score'])
    if scores and scores != {target}:
        return False
    minutes = minute_values(text)
    target_minute = next(iter(minute_values(job['goal']['minute'])), None)
    if minutes and (target_minute is None or minutes != {target_minute}):
        return False
    normalized = norm(text)
    teams = int(named(normalized, job['home'])) + int(named(normalized, job['away']))
    surname = norm(job['goal']['player']).split()[-1:]
    scorer = bool(surname and len(surname[0]) >= 4 and surname[0] in normalized.split())
    cue = bool(re.search(r'гол|go+a+l|qol|сравнял|удвоил|выводит.*вперед|вывел.*впер[её]д', text))
    return bool((teams == 2 and (scores or minutes)) or
                (teams >= 1 and scorer and (scores or minutes)) or
                (teams >= 1 and minutes and cue) or (scorer and scores and minutes))


def caption(job, clip):
    source = f'\n\nВидео: @{clip.channel}\n{clip.link}'
    plain = unescape(re.sub(r'<[^>]+>', '', job['original']))
    units = lambda s: len(s.encode('utf-16-le')) // 2
    if units(plain + source) <= 1024:
        return plain + source
    goal = job['goal']
    plain = (f'⚽ {job["home"]} — {job["away"]} · {goal["score"][0]}:{goal["score"][1]}\n'
             f'{job["competition"]}\n' + '\n'.join(
                 f'{g["minute"]} · {g["player"]} · {g["score"][0]}:{g["score"][1]}' for g in job['goals'])
             + '\n' + goal['description'] + '\nФНК: ' + job['match_url'])
    while units(plain + source) > 1024:
        plain = plain[:-2]
    return plain.rstrip() + source


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
        return ('Видео голов: ' + ('включены' if self.enabled() else 'выключены') +
                '\nИсточники: @matchpremier, @idmanvideo\n'
                f'Ожидают ролик: {waiting}\nПоследняя ошибка: {self.store.get(KEY + "error", "") or "нет"}\n'
                'Проверка каждые 30 секунд при ожидающих голах; ожидание до 30 минут.\n'
                'Видео добавляется в исходное сообщение без пересылки и нового поста.')

    def cancel(self):
        with self.store.db() as db:
            db.execute("UPDATE goal_video_jobs SET status='cancelled' WHERE status='pending'")

    def enqueue(self, chat_id, match, goals, message_id, original):
        if not goals or not self.enabled():
            return
        now = self.clock()
        # Telegram permits one media in a formerly textual message. If several
        # goals share one FNK card, attach the latest goal; never replace it later.
        payload = dict(home=match.home, away=match.away, competition=match.competition,
                       kickoff=match.kickoff, match_url=match.url, original=str(original),
                       goals=[asdict(g) for g in goals], goal=asdict(goals[-1]), created=now)
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO goal_video_jobs VALUES (?,?,?,?,?,?,?)',
                       (chat_id, message_id, match.id, now, now + TTL, json.dumps(payload, ensure_ascii=False), 'pending'))

    def current(self, row, allowed):
        chat, message, match_id, created, expires, payload, status = row
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
            rows = db.execute("SELECT * FROM goal_video_jobs WHERE status='pending' ORDER BY created LIMIT 30").fetchall()
        current = []
        for row in rows:
            if self.current(row, allowed):
                current.append((row, json.loads(row[5])))
            else:
                self.mark(row, 'cancelled')
        if not current:
            return
        clips = self.source.recent()
        self.store.set(KEY + 'checked', now)
        self.store.set(KEY + 'error', '; '.join(f'{c}:{e}' for c, e in self.source.errors.items()))
        for row, job in current:
            # Preference for the domestic broadcaster; the second source remains
            # useful for European matches and as a fallback when a clip is missing.
            options = [c for c in clips if matches_goal(c, job, now)
                       and sum(matches_goal(c, other, now) for _, other in current) == 1]
            options.sort(key=lambda c: (CHANNELS.index(c.channel), -c.post_id))
            for clip in options:
                with self.store.db() as db:
                    used = db.execute('SELECT 1 FROM goal_video_used WHERE chat_id=? AND post=?', (row[0], clip.link)).fetchone()
                if used:
                    continue
                try:
                    data = self.download(clip.url)
                except VideoError as error:
                    self.store.set(KEY + 'error', str(error))
                    continue
                digest = hashlib.sha256(data).hexdigest()
                with self.lock:
                    if not self.current(row, allowed) or self.clock() < self.store.get('blocked_until', 0):
                        break
                    with self.store.db() as db:
                        duplicate = db.execute('SELECT 1 FROM goal_video_used WHERE chat_id=? AND digest=?', (row[0], digest)).fetchone()
                        if duplicate:
                            db.execute('INSERT OR IGNORE INTO goal_video_used VALUES (?,?,?,?)', (row[0], clip.link, digest, now))
                            continue
                        db.execute('INSERT OR IGNORE INTO goal_video_used VALUES (?,?,?,?)', (row[0], clip.link, digest, now))
                        claimed = db.execute("UPDATE goal_video_jobs SET status='editing' WHERE chat_id=? AND message_id=? AND status='pending'", (row[0], row[1])).rowcount
                    if not claimed:
                        break
                    try:
                        edit(row[0], row[1], data, caption(job, clip))
                    except APIError as error:
                        if error.retry_after:
                            self.store.set('blocked_until', self.clock() + error.retry_after)
                        if error.code == 429:
                            with self.store.db() as db:
                                db.execute('DELETE FROM goal_video_used WHERE chat_id=? AND post=?', (row[0], clip.link))
                            self.mark(row, 'pending')
                            self.next_poll = self.clock() + max(INTERVAL, error.retry_after)
                            break
                        # Editing never sends a new post. Do not delete/repost or
                        # blindly retry an upload whose result was not confirmed.
                        self.mark(row, 'uncertain' if not error.code or error.code >= 500 else 'failed')
                        self.store.set(KEY + 'error', f'edit_http_{error.code}')
                        LOG.warning('Добавление видео не подтверждено: HTTP=%s; пост=%s', error.code, row[1])
                    else:
                        self.mark(row, 'attached')
                        self.store.set(KEY + 'error', '')
                        LOG.info('Видео гола добавлено в исходный пост=%s; источник=@%s/%s', row[1], clip.channel, clip.post_id)
                    break
                del data
