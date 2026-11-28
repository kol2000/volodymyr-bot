from goal_caption_language import goal_evidence_text
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
from football_club_names import aliases as club_aliases
from live_football import Document, FootballError, Node, PREFIX

LOG = logging.getLogger('volodymyr')
CHANNELS = ('matchpremier', 'idmanvideo')
KEY = PREFIX + 'videos:'
INTERVAL = 30
TTL = 2 * 60 * 60
FINAL_SCAN_WINDOW = 5 * 60
RPL_RECOVERY_AFTER = 0
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
            chunks, total, deadline = [], 0, time.monotonic() + (300 if video else 20)
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
        if channel == 'idmanvideo':
            from football_name_audit import caption_lines, report_caption
            report_caption(caption_lines(bodies[-1]), 'https://t.me/' + post.attrs['data-post'])
        try:
            stamp = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                continue
            # Multi-goal albums/compilations cannot be assigned to one goal safely.
            reason = ('protected_post' if post.attrs.get('data-protected') in ('1', 'true') else
                      'ambiguous_caption' if len(bodies) != 1 else
                      'multiple_videos' if sum(n.has('grouped_media_wrap') for n in own) > 1 else
                      'public_media_too_big' if not videos and any('Media is too big' in n.text() for n in own if n.has('message_media_not_supported_label')) else
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
        self.unavailable = []
        self.history_unavailable = []

    def history(self, pages=4):
        """Bounded final sweep, including pages with no native video posts."""
        now = self.clock()
        if self.history_at is not None and now-self.history_at < 60:
            self.errors = dict(self.history_errors)
            self.unavailable = list({c.link:(c,r) for c,r in [*self.unavailable,*self.history_unavailable]}.values())
            return self.history_cached
        def read(channel):
            found, cursor, error = {}, None, ''
            skipped = []
            try:
                for _ in range(min(4, max(1, pages))):
                    html = self.fetch('https://t.me/s/'+channel+(f'?before={cursor}' if cursor else ''))
                    for clip in parse_channel(html, channel, skipped=skipped):
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
            return list(found.values()), error, channel, skipped
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(read, CHANNELS))
        self.history_cached = [c for clips, _, _, _ in rows for c in clips]
        self.history_errors = {channel: error for _, error, channel, _ in rows if error}
        self.history_unavailable = [(c,r) for _,_,_,items in rows for c,r in items if r=='public_media_too_big']
        self.unavailable = list({c.link:(c,r) for c,r in [*self.unavailable,*self.history_unavailable]}.values())
        self.history_at, self.errors = now, dict(self.history_errors)
        return self.history_cached

    def recent(self):
        now = self.clock()
        if self.cached_at is not None and now - self.cached_at < INTERVAL:
            return self.cached
        def read(channel):
            skipped = []
            try:
                return channel, parse_channel(self.fetch('https://t.me/s/' + channel), channel, skipped=skipped), '', skipped
            except VideoError as error:
                return channel, [], str(error), []
            except (FootballError, ValueError, RecursionError):
                return channel, [], 'invalid_channel_page', []
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(read, CHANNELS))
        self.cached, self.errors = [], {}
        self.unavailable = []
        for channel, clips, error, skipped in rows:
            self.cached.extend(clips)
            self.unavailable.extend((c,r) for c,r in skipped if r=='public_media_too_big')
            if error:
                self.errors[channel] = error
        self.cached_at = now
        return self.cached


CYRILLIC = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                    ('a','b','v','g','d','e','e','zh','z','i','y','k','l','m','n','o','p','r','s','t',
                     'u','f','h','ts','ch','sh','shch','','y','','e','yu','ya')))
ALIASES = {
    'шахтёр': ('shakhtar', 'şaxtyor', 'shakhtar donetsk'),
    'шахтер': ('shakhtar', 'şaxtyor', 'shakhtar donetsk'),
    'спортинг лиссабон': ('sporting', 'sportinq', 'sporting lisbon', 'спортинг'),
    'спортинг': ('sporting', 'sportinq', 'sporting lisbon'),
    'фенербахче': ('fenerbahce', 'fənərbaxça', 'fenerbahche'),
    'реал мадрид': ('real madrid',), 'лейпциг': ('leipzig', 'rb leipzig', 'leypsiq', 'leyptsiq'),
    'бавария': ('bayern', 'bayern munich', 'bavariya'), 'арсенал': ('arsenal',),
    'псж': ('psg',), 'барселона': ('barselona', 'barcelona'), 'ливерпуль': ('liverpool','liverpul'),
    'амед': ('amed','amedspor'), 'кристал пэлас': ('kristal palas','crystal palace'),
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
    'расинг': ('rasinq santander', 'racing santander'),
}


from national_team_names import NATIONAL_NAMES, NATIONAL_CANONICAL
ALIASES.update(NATIONAL_NAMES)
ALIASES.update({name: NATIONAL_NAMES[key] for name, key in NATIONAL_CANONICAL.items()})


# Club identities shared by FNK/Soccer365 and Azerbaijani/Turkish captions.
# Keep full names: generic city names and "Real"/"Manchester" are ambiguous.
CL_CLUB_NAMES = (
    ('ПСЖ', 'PSG', 'PSJ', 'Paris Saint-Germain', 'Paris Sen Jermen'),
    ('Астон Вилла', 'Aston Villa'),
    ('Ливерпуль', 'Liverpool', 'Liverpul'),
    ('Реал Мадрид', 'Real Madrid'),
    ('Реал Бетис', 'Бетис', 'Real Betis', 'Betis'),
    ('Штутгарт', 'Stuttgart', 'Ştutqart', 'Ştutqard'),
    ('Арсенал', 'Arsenal'),
    ('Барселона', 'Barcelona', 'Barselona'),
    ('Комо', 'Como', 'Komo'),
    ('Манчестер Сити', 'Manchester City', 'Mançester Siti'),
    ('Брюгге', 'Club Brugge', 'Brugge', 'Brügge'),
    ('Спортинг Лиссабон', 'Спортинг', 'Sporting Lisbon', 'Sporting Lizbon', 'Sporting', 'Sportinq'),
    ('Боруссия Дортмунд', 'Боруссия Д', 'Borussia Dortmund', 'Borussiya Dortmund', 'Dortmund'),
    ('Бавария Мюнхен', 'Бавария', 'Bayern Munich', 'Bayern München', 'Bayern Münih', 'Bayern', 'Bavariya'),
    ('Рома', 'Roma', 'AS Roma'),
    ('Атлетико Мадрид', 'Атлетико', 'Atletico Madrid', 'Atletiko Madrid', 'Atletico', 'Atletiko'),
    ('Шахтёр', 'Шахтер', 'Шахтёр Донецк', 'Shakhtar Donetsk', 'Shakhtar', 'Şaxtyor', 'Şahtar Donetsk'),
    ('ПСВ', 'ПСВ Эйндховен', 'PSV', 'PSV Eindhoven'),
    ('Вильярреал', 'Villarreal', 'Vilyarreal'),
    ('Манчестер Юнайтед', 'Manchester United', 'Mançester Yunayted'),
    ('Интер', 'Интер Милан', 'Inter', 'İnter'),
    ('Наполи', 'Napoli'),
    ('Галатасарай', 'Galatasaray', 'Qalatasaray'),
    ('ЛАСК', 'LASK', 'LASK Linz'),
    ('Сабах Масазыр', 'Сабах', 'Sabah Masazır', 'Sabah'),
    ('Фейеноорд', 'Feyenoord', 'Feyenoord Rotterdam', 'Feyenord'),
    ('Ланс', 'Lens', 'Lans'),
    ('Буде-Глимт', 'Будё-Глимт', 'Bodø/Glimt', 'Bodo Glimt', 'Bude Qlimt', 'Bude-Glimt'),
    ('АЕК Афины', 'АЕК', 'AEK Athens', 'AEK Afina', 'AEK Atina', 'AEK'),
    ('РБ Лейпциг', 'Лейпциг', 'RB Leipzig', 'Leipzig', 'Leypsiq', 'Leyptsiq'),
    ('Фенербахче', 'Fenerbahçe', 'Fənərbaxça', 'Fenerbahce'),
    ('Славия Прага', 'Slavia Praha', 'Slavia Prague', 'Slaviya Praqa', 'Slavia Prag'),
    ('Викинг', 'Viking', 'Vikinq'),
    ('Порту', 'Porto'),
    ('Лилль', 'Лиль', 'Lille', 'Lill'),
    ('Слован Братислава', 'Slovan Bratislava'),
)
for _club_names in CL_CLUB_NAMES:
    for _club_name in _club_names:
        ALIASES[_club_name.casefold()] = tuple(dict.fromkeys(
            (*ALIASES.get(_club_name.casefold(), ()), *_club_names)))


def norm(value):
    value = value.casefold().replace('ı', 'i').replace('ə', 'e')
    value = ''.join(CYRILLIC.get(c, c) for c in value)
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def named(text, name):
    name = NATIONAL_CANONICAL.get(name.casefold(), name)
    if name.casefold() == 'париж':
        text = re.sub(r'(?<!\w)paris\s+(?:saint|sen)\s+(?:germain|zh?ermen)(?!\w)', ' ', text)
    if name.casefold() in NATIONAL_NAMES:
        # Match the longest country name first: Northern Ireland is not Ireland.
        own={norm(v) for v in (name,*NATIONAL_NAMES[name.casefold()])}
        covered=text
        for country,aliases in NATIONAL_NAMES.items():
            if country==name.casefold():continue
            for alias in (country,*aliases):
                value=norm(alias)
                if any(v!=value and ' '+v+' ' in ' '+value+' ' for v in own):
                    covered=re.sub(r'(?<!\w)'+re.escape(value)+r'(?!\w)',' ',covered)
        return any(' '+v+' ' in ' '+covered+' ' for v in own)
    variants = (name, *ALIASES.get(name.casefold(), ()))
    normalized = {norm(v) for v in variants} | set(club_aliases(name, norm))
    return any(' ' + v + ' ' in ' ' + text + ' ' for v in normalized if len(v) >= 3)


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
    stem = name[:-1] if name.endswith(('а', 'я', 'ь')) else name
    return bool(re.search(r'(?<!\w)' + re.escape(stem) + r'(?:а|я|у|ю|е|ы|и|ой|ом|ою|ей|ем)(?!\w)', text))


def domestic_broadcast(competition):
    return (not excluded_competition(competition) and bool(re.match(
        r'^(?:Премьер-лига|РПЛ|Чемпионат России|Кубок России|Суперкубок России)\b', competition or '', re.I)))


def narrative_goal_reason(clip, job):
    """Domestic broadcaster's scoreless captions need a complete FNK goal path."""
    if clip.channel != 'matchpremier' or not domestic_broadcast(job.get('competition', '')):
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
    text = goal_evidence_text(clip.text).casefold().replace('ё', 'е')
    # "Под перекладину" describes a finish, unlike hitting the crossbar.
    negative_text = re.sub(r'\bпод\s+(?:перекладину|штангу)\b', '', text)
    if re.search(r'начинал|отбор|разбор|повтор|вспомина|год назад|лучшие голы|сейв|сэйв|почти|едва\s+не|чуть\s+не|мимо|перекладин|штанг|'
                 r'не\s+(?:забил|забит|забивает|сравнял|помог|замкнул|вколотил|поразил|расстрелял|отправил)|мог\s+забить|забил\s+бы', negative_text):
        return 'narrative_not_goal_clip'
    ordinal = {'первый': 1, 'второй': 2, 'третий': 3, 'четвертый': 4, 'пятый': 5,
               'шестой': 6, 'седьмой': 7}
    first = re.search(r'\b(' + '|'.join(ordinal) + r')\s+гол\b', text)
    # A player's first goal for a club is not necessarily the first of this match.
    if first and re.match(r'\s+(?:за\b|в\s+(?:карьере|сезоне)\b)',text[first.end():]):
        first = None
    conceded = re.search(r'\b(' + '|'.join(ordinal) + r')\s+(?:пропущенн\w*\s+у|в\s+воротах)\s+[«"]?([а-я]+)', text)
    conceded_corner = re.match(r'^[«"]?([^»"\n]+?)[»"]?\s+пропустил[аи]?\s+(?:гол\s+)?(?:с|после)\s+углового\b', text)
    draw = bool(re.search(r'\bсравнял[аи]?\b', text))
    seconds = re.search(r'(?<!\w)([1-9]\d{0,2})\s*секунд\w*\b', text)
    career_goal = bool(re.search(r'\bпервый\s+(?:мяч|гол)\b[^.!?]{0,35}\bза\b',text)
                       and not re.search(r'\b(?:ждем|ждём|будет|может|забьет|забьёт|забить|когда|скоро)\b',text))
    cue = career_goal or bool(re.search(r'\bгол\b|\bзабил[аи]?\b|\bсравнял[аи]?\b|\bвпереди\b|\bголевая\s+комбинация\b', text))
    candidates = []
    for index, (goal, side) in enumerate(zip(goals, sides)):
        minute = next(iter(minute_values(goal['minute'])), None)
        # Scoreless captions must be near this goal's playing time. Unlike exact
        # captions they cannot identify a delayed retrospective by themselves.
        base_minute = re.match(r'\d+', goal['minute'])
        second_half = base_minute and int(base_minute[0]) > 45
        expected = job['kickoff'] + 60 * (minute or 0) + (900 if second_half else 0)
        if minute is None or not -180 <= clip.timestamp - expected <= 1200:
            continue
        score = goal['score']
        own_cue = bool(re.search(r'\bв\s+свои\b|\bавтогол\b',text))
        if own_cue and goal['kind'] != 'own-goal':
            continue
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
        pulled_back = bool(scorer and scoring_team and goal['kind'] != 'own-goal'
            and score[side] <= score[1-side]
            and 0 <= clip.timestamp-job['created'] <= 900
            and re.search(r'\bотыграл[аи]?\s+(?:один|мяч|гол)\b|\bсократил[аи]?\s+(?:отставание|разрыв)\b',text)
            and not re.search(r'\bне\s+(?:отыграл|сократил)|\b(?:мог|может|почти|вчера|архив|повтор|обзор)\b',text))
        # "День Воробьёва. Дубль в первом тайме": the complete confirmed
        # scoring path must make this precisely his second goal for this side.
        brace = bool(scorer and goal['kind'] != 'own-goal' and re.search(r'\bдубль\b', text)
                     and not re.search(r'\b(?:не|нет|может|мог|будет|оформит|шанс|ждем|ждём)\b|до\s+дубл|мечт',text)
                     and sum(norm(g['player']) == norm(goal['player']) and g['kind'] != 'own-goal'
                             and s == side for g,s in zip(goals[:index+1],sides[:index+1])) == 2)
        if re.search(r'\bпервом\s+тайме\b',text) and second_half:
            brace = False
        if re.search(r'\bвтором\s+тайме\b',text) and not second_half:
            brace = False
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
        corner_goal = bool(conceded_corner and close_episode
            and 0 <= clip.timestamp-job['created'] <= 300
            and russian_named(conceded_corner[1], (job['home'],job['away'])[1-side])
            and re.search(r'углов|от\s+флажка',goal.get('description',''),re.I)
            and not re.search(r'вчера|архив|повтор|обзор|год назад|раньше|снова покажем',text))
        finish = bool(close_episode and scorer and re.search(
            r'\b(?:замкнул|вколотил|поразил|расстрелял)\b|отправил.{0,35}(?:в\s+сетк|под\s+перекладину)',text))
        # Editorial praise is evidence only with the named scorer and a matching
        # shooting description in FNK, not a generic "beautiful shot" caption.
        praised_shot = bool(close_episode and scorer and re.search(r'\bудар\b',text)
                            and re.search(r'\bбудем\s+пересматривать\b',text)
                            and re.search(r'удар|пробил|жахнул',goal.get('description',''),re.I))
        own_goal = bool(scorer and goal['kind'] == 'own-goal' and own_cue
                        and (scoring_team or 0 <= clip.timestamp-job['created'] <= 600)
                        and re.search(r'\b(?:замкнул|срезал|отправил|забил|автогол)\b',text))
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
        if direct_scorer or pulled_back or brace or timed_lead or finish or praised_shot or assist or own_goal or penalty or conceded_goal or ricochet or corner_goal:
            candidates.append((tuple(score), goal['minute'], goal['player']))
    target = job['goal']
    identity = (tuple(target['score']), target['minute'], target['player'])
    if len(candidates) > 1:
        return 'ambiguous_narrative_goal'
    return '' if candidates == [identity] else 'insufficient_narrative_identity'


def observed_goal_finish(clip, job):
    """Fallback for native RPL captions near the recorded goal-card time."""
    text = goal_evidence_text(clip.text).casefold().replace('ё', 'е')
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


def observed_narrative_finish(clip, job):
    """Corroborate an editorial caption with player, FNK event and observed time."""
    if clip.channel != 'matchpremier' or not domestic_broadcast(job.get('competition','')):
        return False
    if not -60 <= clip.timestamp-job['created'] <= 600:
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    negative=re.sub(r'\bпод\s+(?:штангу|перекладину)\b','',text)
    if re.search(r'почти|едва|чуть не|мимо|штанг|перекладин|сейв|сэйв|отбил|спас|'
                 r'отмен|офсайд|обзор|разбор|повтор|вспомин|архив|интервью|разминк|трениров|'
                 r'\bне\b|\bбы\b|мог|может|будет|завтра|анонс|реклам',negative):
        return False
    goals=job.get('match_goals',[])
    previous=(0,0)
    for goal in goals:
        score=tuple(goal['score'])
        if tuple(a-b for a,b in zip(score,previous)) not in ((1,0),(0,1)):
            return False
        previous=score
    if not goals or previous!=tuple(job.get('match_score',())):
        return False
    target=job['goal']
    name=(target['player'].split() or [''])[-1].casefold().replace('ё','е')
    # Expressive spelling (МИРОНООООВ) still has to contain every surname letter.
    expressive=(len(name)>=4 and re.fullmatch(r'[а-я]+',name) and re.search(
        r'(?<!\w)'+''.join(re.escape(c)+'+' for c in name)+r'(?!\w)',text))
    scorer=len(name)>=4 and (russian_named(text,name) or expressive)
    action=bool(re.search(r'(?:сильно|закрутил|отправил|убрал).{0,65}в\s+(?:ближний|дальний)|'
                         r'положил|удивительный\s+удар|\b(?:забил[аи]?|вбил|вколотил)\b|\bповел\b|'
                         r'переиграл\s+защит|\bответила\b|\bв\s+воротах\b',text))
    if target.get('kind')=='own-goal':
        return bool(re.search(r'заставил[аи]?.{0,55}забить\s+себе',text)
                    and russian_named(text,job['home']) and russian_named(text,job['away']))
    # An editorial headed finish needs the same action in the confirmed event,
    # in addition to scorer, complete score progression and observed upload time.
    headed_finish = bool(
        re.search(r'\b(?:кивнул|замкнул|подрезал|пробил|забил)\s+(?:мяч\s+|сферу\s+)?(?:головой|макушкой)\b', text)
        and re.search(r'\b(?:подрезал|пробил|замкнул|забил|отправил)\b.{0,45}\b(?:головой|макушкой)\b',
                      target.get('description', ''), re.I)
    )
    long_range_finish = bool(
        re.search(r'\bдальним\b|\bиздали\b|\bдальн(?:ий|его)\s+удар', text)
        and re.search(r'\b(?:попал|забил|положил)\b', text)
        and re.search(r'из-за\s+штрафной|дальн(?:ий|его)\s+удар|метров\s+с\s+(?:2[0-9]|[3-5][0-9])\b',
                      target.get('description', ''), re.I)
    )
    if not scorer or not (action or headed_finish or long_range_finish):
        return False
    ordinal=re.search(r'\b(первый|второй|третий|четвертый|пятый)\s+(?:вбил|вколотил|забил)\b',text)
    if ordinal:
        before=next((g['score'] for g in goals if sum(g['score'])==sum(target['score'])-1),(0,0))
        side=next((i for i in (0,1) if target['score'][i]>before[i]),None)
        if side is None or target['score'][side]!=('первый','второй','третий','четвертый','пятый').index(ordinal[1])+1:
            return False
    # A repeated scorer needs an explicit score/ordinal; never select his nearest goal.
    if sum(norm(g['player'])==norm(target['player']) for g in goals)>1:
        return False
    if 'третий в воротах' in text:
        side=0 if target['score'][0] > (next((g['score'][0] for g in reversed(goals)
                 if sum(g['score'])==sum(target['score'])-1),0)) else 1
        if target['score'][side]!=3:
            return False
    return bool(re.search(r'го+л|пробил|удар|попал|отправил|забил',target.get('description',''),re.I))


def temporal_identity(job):
    goal = job['goal']
    return (job['home'],job['away'],job['kickoff'],tuple(goal['score']),goal['minute'])


def publication_window(clip,job):
    # A named fixture can tolerate delayed channel uploads. Anonymous clips
    # retain the narrow window and require global uniqueness.
    return 900 if any(russian_named(clip.text,t) for t in (job['home'],job['away'])) else 300


def temporal_candidate(clip, job, now):
    """Candidate only, never sufficient without the global uniqueness checks."""
    if clip.channel != 'matchpremier' or not domestic_broadcast(job.get('competition','')):
        return False
    if not 0 <= clip.timestamp-job['created'] <= publication_window(clip,job) or now-clip.timestamp < 45:
        return False
    # Preserve explicit conflicting evidence and complete-source requirements.
    reason = goal_match_reason(clip,job,now)
    if reason not in ('','insufficient_goal_identity','insufficient_narrative_identity'):
        return False
    previous=(0,0)
    for goal in job.get('match_goals',[]):
        score=tuple(goal['score'])
        if tuple(a-b for a,b in zip(score,previous)) not in ((1,0),(0,1)):
            return False
        previous=score
    if not job.get('match_goals') or previous!=tuple(job.get('match_score',())):
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    if reason and re.search(r'\b(?:первый|второй|третий|четвертый|пятый|дубль|хет[ -]?трик|ноль|один|два|три|четыре|пять)\b',text):
        return False
    minute=re.match(r'\d+',job['goal']['minute'])
    if not minute or ('первом тайме' in text and int(minute[0])>45) or ('втором тайме' in text and int(minute[0])<=45):
        return False
    if re.search(r'интервью|судь|разбор|разобрал|повтор|обзор|вспомин|архив|вчера|год назад|в августе|'
                 r'трениров|разминк|лучши[йе]|гол месяца|гол тура|гол года|почти|едва|чуть не|не заб|'
                 r'не поп|мимо|штанг|перекладин|отбил|сейв|сэйв|отмен|офсайд|не засчит|'
                 r'будет|завтра|может|заработал пенальти|назнач|пробьет|пенальти.{0,20}назнач|'
                 r'не реализ|не оформ|мог бы|забил бы|спас|анонс|реклам|подпис|промо|'
                 r'смотрите|\bкак\b|прямой эфир',text):
        return False
    return bool(re.search(r'\b(?:го+л|забил|забивает|вонзил|вколотил|замкнул|положил|'
                          r'сравнял|дубль|хет[ -]?трик|впереди|пушка|шедевр)\b|'
                          r'в девятку|в сетку|точен с точки|реализовал пенальти',text))


def temporal_matches(clip, job, peers, clips, now, active=()):
    """One goal and one scoring clip within the evidence window; no nearest guess."""
    if not temporal_candidate(clip,job,now):
        return False
    # Already attached, cancelled or uncertain cards remain competing evidence.
    pool={temporal_identity(j):j for j in peers if domestic_broadcast(j.get('competition',''))}
    pool.setdefault(temporal_identity(job),job)
    near={key:j for key,j in pool.items() if 0 <= clip.timestamp-j['created'] <= publication_window(clip,j)}
    if set(near)!={temporal_identity(job)}:
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    own_names=(job['home'],job['away'],(job['goal']['player'].split() or [''])[-1])
    own_hint=any(russian_named(text,name) for name in own_names if len(name)>=4)
    # With parallel games, a generic caption cannot identify the right match.
    for match in active:
        same=(match.get('home'),match.get('away'))==(job['home'],job['away'])
        if not same and match.get('status') in ('live','break','penalties') and not own_hint:
            return False
        if not same and any(name not in own_names and russian_named(text,name)
                            for name in (match.get('home',''),match.get('away','')) if len(name)>=4):
            return False
    # Never override a different named team/player or a strong match to another goal.
    for key,other in pool.items():
        if key==temporal_identity(job):continue
        if matches_goal(clip,other,now):return False
        foreign=(other['home'],other['away'],(other['goal']['player'].split() or [''])[-1])
        if any(name not in own_names and len(name)>=4 and russian_named(text,name) for name in foreign):
            return False
    nearby={c.link for c in clips if temporal_candidate(c,job,now+45)}
    return nearby=={clip.link}


def broadcast_event_matches(clip, job):
    """Named scoring team + unique real goal in a 15-minute publication window.

    A coaching/career milestone is not a match goal number. The timeline,
    rather than that number, must identify exactly one event.
    """
    if clip.channel != 'matchpremier' or not domestic_broadcast(job.get('competition','')):
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    if re.search(r'отмен|офсайд|не засчит|архив|вчера|повтор|обзор|вспомин|интервью|анонс|'
                 r'почти|чуть не|едва|не заб|мог бы|забил бы|будет|завтра|трениров|сейв|сэйв',text):
        return False
    draw=bool(re.search(r'\b(?:вновь|снова|теперь|опять)\s+ничья\b|счет\s+(?:снова\s+)?равн',text))
    milestone=re.search(r'\bпервый\s+гол\s+[«"]?([^»".!?]+?)[»"]?\s+при\s+[а-я]',text)
    if not draw and not milestone:
        return False
    goals=job.get('match_goals',[])
    previous=(0,0); candidates=[]
    for goal in goals:
        score=tuple(goal['score'])
        delta=tuple(a-b for a,b in zip(score,previous))
        if delta not in ((1,0),(0,1)):return False
        side=delta.index(1);previous=score
        team=(job['home'],job['away'])[side]
        if not russian_named(text,team):continue
        if draw and (score[0]!=score[1] or not sum(score)):continue
        if milestone and not russian_named(milestone[1],team):continue
        # Use actual notification time for the target; estimate other goals
        # relative to it so a second nearby goal prevents a guessed match.
        minute=next(iter(minute_values(goal['minute'])),None)
        target=next(iter(minute_values(job['goal']['minute'])),None)
        if minute is None or target is None:return False
        event_time=job['created']+(minute-target)*60
        if 0 <= clip.timestamp-event_time <= 900:
            candidates.append((score,goal['minute']))
    if not goals or previous!=tuple(job.get('match_score',())):return False
    return candidates==[(tuple(job['goal']['score']),job['goal']['minute'])]


def participant_error_matches(clip,job):
    """Match a named error participant in a confirmed goal, not just its scorer."""
    if clip.channel!='matchpremier' or not domestic_broadcast(job.get('competition','')):
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    if re.search(r'повтор|вчера|архив|обзор|отмен|офсайд|не засчит|сейв|сэйв|спас|почти|едва|чуть не|не заб|мог бы|завтра|интервью|разбор',text):
        return False
    if not re.search(r'куда\s+(?:убежал|побежал|вышел)|что\s+(?:сделал|натворил)|ошиб(?:ся|ка)|привез',text):
        return False
    previous=(0,0);found=[]
    for goal in job.get('match_goals',[]):
        score=tuple(goal['score'])
        if tuple(a-b for a,b in zip(score,previous)) not in ((1,0),(0,1)):return False
        previous=score
        description=goal.get('description','')
        # Names are evidence only when the source explicitly attributes the
        # error to them. Ordinary shared words do not count as participants.
        names=re.findall(r'\bошибк[а-я]*\s+([А-ЯЁ][а-яё]{4,})\b',description)
        names+=re.findall(r'\b([А-ЯЁ][а-яё]{4,})\s+(?:ошибся|привез)\b',description)
        if not any(russian_named(text,name) for name in names):continue
        minute=next(iter(minute_values(goal['minute'])),None)
        target=next(iter(minute_values(job['goal']['minute'])),None)
        if minute is None or target is None:return False
        observed=job['created']+(minute-target)*60
        if 0<=clip.timestamp-observed<=300:
            found.append((score,goal['minute'],goal['player']))
    if previous!=tuple(job.get('match_score',())):return False
    target=job['goal']
    return found==[(tuple(target['score']),target['minute'],target['player'])]


def observed_episode_matches(clip, job):
    """Corroborate a recent broadcaster clip against a unique confirmed episode."""
    if clip.channel != 'matchpremier' or not domestic_broadcast(job.get('competition','')):
        return False
    if not -30 <= clip.timestamp-job['created'] <= 600:
        return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    if re.search(r'отмен|офсайд|не засчит|архив|вчера|повтор|обзор|вспомин|интервью|анонс|'
                 r'почти|чуть не|едва|не заб|не попал|мог бы|забил бы|будет|завтра|трениров|разминк',text):
        return False
    # "Под штангу" and "мяч из ворот выскочил" describe goals, not saved shots.
    cleaned=re.sub(r'под\s+(?:штангу|перекладину)', '', text)
    if re.search(r'мимо|попал.{0,15}(?:в штангу|в перекладину)|сейв|сэйв|спас',cleaned):
        return False
    def person(name):
        name=name.casefold().replace('ё','е')
        return len(name)>=4 and (russian_named(text,name) or bool(re.fullmatch(r'[а-я]+',name)
            and re.search(r'(?<!\w)'+''.join(re.escape(c)+'+' for c in name)+r'(?!\w)',text)))
    goals=job.get('match_goals',[]); previous=(0,0); found=[]
    for goal in goals:
        score=tuple(goal['score'])
        if tuple(a-b for a,b in zip(score,previous)) not in ((1,0),(0,1)):return False
        previous=score
        if goal.get('kind')=='own-goal':continue
        names=goal['player'].split()
        description=goal.get('description','')
        scorer=bool(names and person(names[-1]))
        # First name alone needs a second independently named participant.
        others=[n for n in re.findall(r'\b[А-ЯЁ][а-яё]{3,}\b',description)
                if n.casefold() not in {v.casefold() for v in names}
                and not any(russian_named(n,t) for t in (job['home'],job['away']))]
        second=any(person(n) for n in others)
        scorer=scorer or (len(names)>1 and person(names[0]) and second)
        finish=bool(re.search(r'\bзабил\b|суждено было забить|привела? к голу|\bничья\b|'
                             r'мяч из ворот|сухая серия.{0,60}закончилась',text))
        # A named provider plus a matching pass action in the goal description
        # supports captions which omit both scorer and team.
        assist=bool(second and re.search(r'прострел|передач|нашел',text)
                    and re.search(r'прострел|передач|стрельну|наброс|покатил',description,re.I))
        if not ((scorer and finish) or assist):continue
        if 'ничья' in text and score[0]!=score[1]:continue
        if not re.search(r'го+л|забил|поразил|расстрелял|втыкая|воткнул',description,re.I):continue
        found.append((score,goal['minute'],goal['player']))
    if not goals or previous!=tuple(job.get('match_score',())):return False
    target=job['goal']
    return found==[(tuple(target['score']),target['minute'],target['player'])]


def national_winner_matches(clip,job):
    # Delayed national-team highlights need explicit scorer + winning-goal claim.
    if clip.channel!='matchpremier' or any(t.casefold() not in NATIONAL_NAMES for t in (job['home'],job['away'])):
        return False
    if not re.search(r'товарищеск|лига наций|чемпионат мира|чемпионат европы|отбор',job.get('competition',''),re.I):
        return False
    if not 0<=clip.timestamp-job['created']<=7200:return False
    text=goal_evidence_text(clip.text).casefold().replace('ё','е')
    if not re.search(r'победный гол',text) or re.search(r'архив|вчера|повтор|обзор|вспомин|отмен|офсайд|не засчит|мог|почти|будет',text):return False
    target=job['goal'];surname=(target['player'].split() or [''])[-1]
    if len(surname)<4 or not russian_named(text,surname) or target['kind']=='own-goal':return False
    previous=(0,0);scoring=[]
    goals=job.get('match_goals',[])
    for g in goals:
        score=tuple(g['score']);delta=tuple(a-b for a,b in zip(score,previous))
        if delta not in ((1,0),(0,1)):return False
        side=delta.index(1)
        scoring.append((g,side))
        previous=score
    if not goals or previous!=tuple(job.get('match_score',())) or previous[0]==previous[1]:return False
    side=0 if previous[0]>previous[1] else 1
    winners=[g for g,s in scoring if s==side and g['score'][side]==previous[1-side]+1]
    return winners==[target] and sum(g['player']==target['player'] for g in goals)==1


def goal_match_reason(clip, job, now):
    """Empty for a match; fixed codes for diagnostic use, never signed URLs."""
    if clip.timestamp < max(job['kickoff'] - 300, job['created'] - 600):
        return 'publication_before_window'
    if clip.timestamp > now + 120:
        return 'future_publication'
    text = goal_evidence_text(clip.text).casefold()
    if re.search(r'отмен[её]н|отмена|офсайд|вне игры|disallowed|no goal|l[eə]ğv|qeydə alınmadı|qeyde alinmadi|обзор|highlights|все голы', text):
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
    surname = norm(job['goal']['player']).split()[-1:]
    scorer = bool(surname and len(surname[0]) >= 4 and surname[0] in normalized.split())
    if minutes and (target_minute is None or minutes != {target_minute}):
        # Sources sometimes label the elapsed/started minute differently. Accept
        # only a one-minute difference with both teams AND the exact goal score.
        tolerance = 2 if (scorer and -60 <= clip.timestamp-job['created'] <= 600) else 1
        if not (target_minute is not None and len(minutes) == 1 and teams == 2
                and scores == {target} and abs(next(iter(minutes)) - target_minute) <= tolerance):
            return 'minute_mismatch'
    surname = norm(job['goal']['player']).split()[-1:]
    scorer = bool(surname and len(surname[0]) >= 4 and surname[0] in normalized.split())
    cue = bool(re.search(r'гол|go+a+l|qol|сравнял|удвоил|выводит.*вперед|вывел.*впер[её]д', text))
    matched = ((teams == 2 and (scores or minutes)) or
               (teams >= 1 and scorer and (scores or minutes)) or
               (teams >= 1 and minutes and cue) or (scorer and scores and minutes))
    if matched:
        return ''
    if not scores and not minutes and national_winner_matches(clip,job):
        return ''
    reason = narrative_goal_reason(clip, job) if not scores and not minutes else 'insufficient_goal_identity'
    if reason == 'insufficient_narrative_identity' and now-clip.timestamp>=45 and observed_episode_matches(clip,job):
        return ''
    if reason == 'insufficient_narrative_identity' and broadcast_event_matches(clip,job):
        return ''
    if reason == 'insufficient_narrative_identity' and now-clip.timestamp>=45 and participant_error_matches(clip,job):
        return ''
    if reason == 'insufficient_narrative_identity' and (observed_goal_finish(clip, job) or observed_narrative_finish(clip,job)):
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
        if re.match(r'^\s*(?:Футбол на Куличках|Спорт-Экспресс|Soccer365)\s*·\s*проверено\b', line):
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
        if parsed.scheme == 'https' and ((parsed.netloc == 'football.kulichki.net' and parsed.path.startswith('/trans/')) or
                                        (parsed.netloc == 'soccer365.ru' and re.fullmatch(r'/live/match-[a-z0-9-]+/|/games/[1-9]\d*/',parsed.path))):
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
        self.visual_checked = {}
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
                      public_media_too_big='Telegram не отдаёт большой ролик через публичную страницу',
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
            db.execute('DELETE FROM goal_video_jobs WHERE created<?', (now - 30 * 86400,))
            db.execute('DELETE FROM goal_video_used WHERE created<?', (now - 30 * 86400,))
            db.execute('DELETE FROM goal_video_diagnostics WHERE checked<?', (now - 30 * 86400,))
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
        # Keep scanning while pending, including after the first successful
        # history read. PublicClips.history caches these reads for 60 seconds.
        recovery=any(now-row[3]>=RPL_RECOVERY_AFTER and domestic_broadcast(job.get('competition',''))
                     for row,job in current)
        if (sweep or recovery) and hasattr(self.source, 'history'):
            history = self.source.history()
            clips = list({c.link:c for c in [*clips,*history]}.values())
            for row in sweep:
                self.diagnose(row, 'waiting', final=not self.source.errors)
        self.store.set(KEY + 'checked', now)
        self.store.set(KEY + 'error', '; '.join(f'{c}:{e}' for c, e in self.source.errors.items()))
        with self.store.db() as db:
            peers=[json.loads(r[0]) for r in db.execute('SELECT payload FROM goal_video_jobs WHERE chat_id=? AND created>=?',
                                                       (self.store.get('chat_id'),now-2*3600))]
        active=self.store.get(PREFIX+'fnk_selection',[])
        from telegram_reader import ready as reader_ready, download as reader_download
        from desktop_reader import ready as desktop_ready, download as desktop_download
        reader_clips = []
        desktop_enabled = self.store.get(KEY + 'desktop_enabled', False) and desktop_ready()
        if desktop_enabled or reader_ready():
            backend = 'desktop-reader:' if desktop_enabled else 'telegram-reader:'
            reader_clips = [Clip(c.channel,c.post_id,c.timestamp,c.text,backend+c.link)
                            for c,r in getattr(self.source,'unavailable',[]) if r=='public_media_too_big']
            clips = list({c.link:c for c in [*reader_clips,*clips]}.values())
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
            evidence = [f'{c.link}: {goal_match_reason(c,job,now) or "matched"}' for c in related]
            self.diagnose(row, reason, '; '.join([*sorted(set(reasons)),*evidence])[:2000])
            # Preference for the domestic broadcaster; the second source remains
            # useful for European matches and as a fallback when a clip is missing.
            options = [c for c in clips if matches_goal(c, job, now)
                       and sum(matches_goal(c, other, now) for _, other in current) == 1]
            temporal_options=[c for c in clips if not matches_goal(c,job,now)
                              and temporal_matches(c,job,peers,clips,now,active)]
            options = options or temporal_options
            visual_data = {}
            if not options:
                from broadcast_video_evidence import candidate as visual_candidate, matches as visual_matches
                from goal_video_media import compact
                with self.store.db() as db:
                    used_posts={r[0] for r in db.execute('SELECT post FROM goal_video_used WHERE chat_id=?',(row[0],))}
                candidates=[c for c in clips if c.link not in used_posts and visual_candidate(c,job,now)]
                # Bound work, but never select the first out of several possible videos.
                attempt_key=lambda c:(row[0],row[1],c.link)
                if len(candidates)<=8 and all(now-self.visual_checked.get(attempt_key(c),0)>=60 for c in candidates):
                    complete=True
                    for c in candidates:
                        self.visual_checked[attempt_key(c)]=now
                        try:
                            raw=(desktop_download(c) if c.url.startswith('desktop-reader:') else
                                 reader_download(c) if c.url.startswith('telegram-reader:') else self.download(c.url))
                            data=compact(raw)
                            if (visual_matches(c,job,data,now) and not any(
                                    temporal_identity(other)!=temporal_identity(job) and visual_matches(c,other,data,now)
                                    for other in peers)):
                                visual_data[c.link]=data
                        except VideoError as error:
                            complete=False
                            self.diagnose(row,'waiting','scoreboard_'+str(error))
                    if complete and len(visual_data)==1:
                        options=[c for c in candidates if c.link in visual_data]
                    elif len(visual_data)>1:
                        self.diagnose(row,'ambiguous','multiple_scoreboard_matches')
                    elif complete and candidates:
                        self.diagnose(row,'identity_unresolved','scoreboard_not_confirmed')
                self.visual_checked={k:v for k,v in self.visual_checked.items() if now-v<900}
            if not options:
                blocked = [c for c,r in getattr(self.source,'unavailable',[]) if r=='public_media_too_big'
                           and not goal_match_reason(c,job,now)]
                if blocked:
                    self.diagnose(row,'public_media_too_big', '; '.join(c.link for c in blocked))
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
                    data = visual_data.get(clip.link)
                    if data is None:
                        data = (desktop_download(clip) if clip.url.startswith('desktop-reader:') else
                                reader_download(clip) if clip.url.startswith('telegram-reader:') else self.download(clip.url))
                    from goal_video_media import compact
                    data = compact(data)
                except VideoError as error:
                    self.diagnose(row, 'waiting' if str(error)=='desktop_pending' else 'download_failed', str(error))
                    if str(error) != 'desktop_pending':
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
                    by_time=not matches_goal(clip,job,self.clock())
                    if by_time and clip.link in visual_data:
                        with self.store.db() as db:
                            fresh_peers=[json.loads(r[0]) for r in db.execute('SELECT payload FROM goal_video_jobs WHERE chat_id=? AND created>=?',
                                                                           (row[0],self.clock()-2*3600))]
                        if not visual_matches(clip,job,data,self.clock()) or any(
                                temporal_identity(other)!=temporal_identity(job) and visual_matches(clip,other,data,self.clock())
                                for other in fresh_peers):break
                        by_time=False
                    if by_time:
                        with self.store.db() as db:
                            fresh_peers=[json.loads(r[0]) for r in db.execute('SELECT payload FROM goal_video_jobs WHERE chat_id=? AND created>=?',
                                                                           (row[0],self.clock()-2*3600))]
                        fresh_clips=self.source.recent()
                        if self.source.errors:
                            self.diagnose(row,'source_unavailable','time_window_recheck_failed')
                            break
                        if not temporal_matches(clip,job,fresh_peers,[*clips,*fresh_clips],self.clock(),
                                                self.store.get(PREFIX+'fnk_selection',[])):
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
                        self.diagnose(row, 'attached', f'time_window: delay={clip.timestamp-job["created"]:.0f}s' if by_time else '')
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
