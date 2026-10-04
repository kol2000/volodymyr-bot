#!/usr/bin/env python3
import contextlib
import json
import logging
import queue
import random
import re
import sqlite3
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from common import APIError, ROOT, STATE, Telegram, clean_text, http_json, load_config
from chat_services import ChatServices, ServiceError, service_error_reply, service_request
from retrieval import build_index, find_examples, random_candidates

LOG = logging.getLogger('volodymyr')
MOSCOW = ZoneInfo('Europe/Moscow')
WELCOME_USERNAME = 'leonadosasa'
WELCOME_TEXT = 'оо алкаш епти ти де бил?'
TRIGGER_WORDS = ('бубуська', 'володька', 'рахиня', 'украина', 'война', 'киев',
                 'цаца', 'цацик', 'бубус', 'цацыг', 'хряк', 'свинья', 'сосатка',
                 'крым', 'крим', 'даг')
TRIGGER_PHRASES = ('крым наш',)
# Match phrases first so "крым наш" is one trigger, with flexible whitespace.
TRIGGER_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(
    re.escape(term).replace(r'\ ', r'\s+') for term in (*TRIGGER_PHRASES, *TRIGGER_WORDS)) + r')(?!\w)', re.I)
ANGRY_QUOTE_PATTERN = re.compile(r'\b(?:бля\w*|сука\w*|заеб\w*|задолб\w*|пизд\w*|'
                                 r'ёб\w*|еба\w*|ебл\w*|охуе\w*|дурак\w*|идиот\w*|'
                                 r'туп\w*|нахуй|нахер|нахуя|завали|хер\w*)\b', re.I)
# Locally written replies: no model, private archive or user text is required.
# More than thirty distinct generic replies keep recent-answer exclusion usable.
FALLBACK_REPLIES = (
    'ну и шо ти этим хотел сказать',
    'бля можно хоть раз нормально спросить',
    'шо за шум опять без повода',
    'ти сначала мысль собери потом пиши',
    'ето всё или ещо концерт будет',
    'да сколько можно одно и то же',
    'ну ти и устроил базар конечно',
    'бля опять начинаеш со своей хернёй',
    'шо ти от меня то хочеш',
    'ну давай конкретнее без етих выкрутасов',
    'ти ещо громче напиши может поможет',
    'ето у тебя вопрос или просто шум',
    'да поняв я поняв хватит орать',
    'опять мне ето разгребать шо ли',
    'ну всё началось бля по новой',
    'ти хоть сам понял шо написал',
    'шо за привычка меня дёргать постоянно',
    'бля ну объясни нормально чего надо',
    'ну начинай уже чего хотел то',
    'ето ти сечас серьёзно спрашиваеш',
    'шо за допрос с порога опять',
    'да тише ти весь чат разбудил',
    'ну давай без етих загадок бля',
    'и долго ти так будеш заводиться',
    'я тут чего шум поднял',
    'шо опять зацепиться больше не за что',
    'бля у тебя талант устраивать суету',
    'ну хоть суть скажи для начала',
    'ето уже третья серия твоего выступления',
    'ти можеш по делу хоть немного',
    'шо опять надо объяснять на пальцах',
    'ну и чего ти добился етим криком',
    'бля дай хоть мысль закончить',
    'да вижу я тебя не кипятись',
    'опять ти со своим представлением пришёл',
    'ну спрашивай уже раз позвал',
)
CYBORG_PATTERN = re.compile(r'\b(?:киборг[а-яё]*|cyborg[a-z]*)\b', re.I)
BUBUS_PATTERN = re.compile(r'\bбубус[а-яё]*\b', re.I)
FORGET_PATTERN = re.compile(r'\b(?:забы[а-яё]*|заби[а-яё]*)\b', re.I)
REPEAT_FILLER = set('а и в на не но ну по с то это ты я он она они мы вы мне меня тебе тебя '
                    'тут там так вот уже еще что как да бля блять'.split())
FALLBACK_TOPICS = (
    (re.compile(r'(?i)\b(?:украина|война|киев|рахиня|крым|крим|даг)\b'), (
        'опять политоту притащил бля сколько можно',
        'шо опять диванный эксперт проснулся',
        'ти без етих споров хоть вечер можеш',
        'ну всё опять спорить до утра будеш',
    )),
    (re.compile(r'(?i)\b(?:бубуська|володька|цаца|цацик|бубус|цацыг|хряк|свинья|сосатка)\b'), (
        'шо опять бубуса дёргаеш делать нечего',
        'цацик бля у тебя других слов нет',
        'ти меня позвал или просто орёшь',
        'да тут я бля хватит звать',
    )),
    (re.compile(r'(?i)\b(?:интернет\w*|роутер\w*|вайфай\w*)\b'), (
        'бля опять роутер тебе вечер испортил',
        'шо интернет опять решил отдохнуть',
        'ну перезапусти роутер хватит его уговаривать',
        'ето интернет или ежедневный повод поорать',
    )),
    (re.compile(r'(?i)\b(?:пиво|пивас\w*|пивка|пивко)\b'), (
        'ти кроме пива ещо о чём думаеш',
        'шо опять весь разговор к пиву свёл',
        'ну началось опять где пивас бля',
        'бля у тебя вечная повестка про пиво',
    )),
)
COMMANDS = [('/bubus', 'случайная фраза; с текстом — ответ по теме'),
            ('/weather', 'погода сейчас: /weather Орёл'),
            ('/convert', 'пересчитать сумму: /convert 100 USDT'),
            ('/auto_on', 'включить периодические реплики'),
            ('/auto_off', 'выключить периодические реплики'),
            ('/status', 'показать состояние'), ('/whoami', 'показать мой Telegram ID')]


class Store:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS messages (
                    chat_id INTEGER, message_id INTEGER, timestamp REAL,
                    speaker TEXT, text TEXT, human INTEGER,
                    PRIMARY KEY(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS sends (
                    id INTEGER PRIMARY KEY, timestamp REAL, day TEXT,
                    kind TEXT, chat_id INTEGER, status TEXT);
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id,timestamp);
                CREATE TABLE IF NOT EXISTS greetings (
                    chat_id INTEGER, message_id INTEGER, timestamp REAL,
                    PRIMARY KEY(chat_id,message_id));
            ''')

    @contextlib.contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def add_message(self, message, human=True):
        chat_id = message['chat']['id']
        text = message.get('text') or message.get('caption') or ''
        if not text.strip():
            return False
        speaker = 'бот' if not human else message.get('from', {}).get('first_name', 'участник')[:40]
        with self.db() as db:
            inserted = db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)',
                                  (chat_id, message['message_id'], message.get('date', time.time()),
                                   speaker, clean_text(text)[:1000], int(human))).rowcount
            db.execute('DELETE FROM messages WHERE chat_id=? AND message_id NOT IN '
                       '(SELECT message_id FROM messages WHERE chat_id=? ORDER BY timestamp DESC,message_id DESC LIMIT 200)',
                       (chat_id, chat_id))
        return bool(inserted)

    def context(self, chat_id, limit=10):
        with self.db() as db:
            rows = db.execute('SELECT speaker,text,human FROM messages WHERE chat_id=? AND timestamp>? '
                              'ORDER BY timestamp DESC,message_id DESC LIMIT ?',
                              (chat_id, time.time() - 7200, limit)).fetchall()
        return [{'speaker': speaker, 'text': text[:250], 'human': bool(human)}
                for speaker, text, human in reversed(rows)]

    def latest_human(self, chat_id):
        with self.db() as db:
            row = db.execute('SELECT max(timestamp) FROM messages WHERE chat_id=? AND human=1', (chat_id,)).fetchone()
        return row[0] or 0

    def recent_answers(self, chat_id, limit=10):
        with self.db() as db:
            rows = db.execute('SELECT text FROM messages WHERE chat_id=? AND human=0 '
                              'ORDER BY timestamp DESC,message_id DESC LIMIT ?',
                              (chat_id, limit)).fetchall()
        return [row[0] for row in rows]

    def counts(self):
        day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
        with self.db() as db:
            rows = db.execute('SELECT kind,count(*) FROM sends WHERE day=? GROUP BY kind', (day,)).fetchall()
        return dict(rows)

    def claim_send(self, kind, chat_id):
        now = time.time()
        day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if kind == 'automatic':
                count = db.execute('SELECT count(*) FROM sends WHERE day=? AND kind=?', (day, kind)).fetchone()[0]
                if count >= 5:
                    LOG.info('Отправка пропущена: причина=automatic_daily_limit')
                    return None
            row = db.execute('SELECT max(timestamp) FROM sends WHERE chat_id=?', (chat_id,)).fetchone()
            last = row[0] or 0
            if now - last < (3600 if kind == 'automatic' else 15):
                LOG.info('Отправка пропущена: причина=send_cooldown; режим=%s', kind)
                return None
            cursor = db.execute('INSERT INTO sends(timestamp,day,kind,chat_id,status) VALUES (?,?,?,?,?)',
                                (now, day, kind, chat_id, 'attempt'))
            db.execute('DELETE FROM sends WHERE timestamp<?', (now - 30 * 86400,))
            return cursor.lastrowid

    def finish_send(self, send_id, status):
        with self.db() as db:
            db.execute('UPDATE sends SET status=? WHERE id=?', (status, send_id))

    def claim_greeting(self, chat_id, message_id):
        """Reserve this join event before sending, including uncertain sends."""
        now = time.time()
        day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted = db.execute('INSERT OR IGNORE INTO greetings VALUES (?,?,?)',
                                  (chat_id, message_id, now)).rowcount
            if not inserted:
                return None
            cursor = db.execute('INSERT INTO sends(timestamp,day,kind,chat_id,status) VALUES (?,?,?,?,?)',
                                (now, day, 'greeting', chat_id, 'attempt'))
            db.execute('DELETE FROM greetings WHERE timestamp<?', (now - 30 * 86400,))
            db.execute('DELETE FROM sends WHERE timestamp<?', (now - 30 * 86400,))
            return cursor.lastrowid

    def last_send(self, chat_id):
        with self.db() as db:
            row = db.execute('SELECT max(timestamp) FROM sends WHERE chat_id=?', (chat_id,)).fetchone()
        return row[0] or 0


def surzhyk_text(text):
    """Enforce recurrent archive spellings without removing every soft sign."""
    spellings = {'ты': 'ти', 'это': 'ето', 'ещё': 'ещо', 'еще': 'ещо',
                 'было': 'било', 'бы': 'би', 'сейчас': 'сечас'}

    def replace(match):
        original = match.group()
        word = original.lower()
        changed = spellings.get(word, word)
        if changed.endswith(('ешь', 'ишь')):
            changed = changed[:-1]
        if changed == word:
            return original
        if original.isupper():
            return changed.upper()
        return changed.capitalize() if original[0].isupper() else changed

    return re.sub(r'\b[а-яё]+\b', replace, text, flags=re.I)


def answer_key(text):
    # Ignore capitalization, punctuation and spaces when detecting repeats.
    return ' '.join(re.findall(r'\w+', surzhyk_text(text).casefold().replace('ё', 'е')))


def repeat_words(text):
    aliases = {'ето': 'это', 'ти': 'ты', 'шо': 'что', 'че': 'что', 'ещо': 'еще'}
    words = []
    for word in answer_key(text).split():
        word = aliases.get(word, word)
        if word.startswith('бубус'):
            word = 'бубус'
        elif word.startswith(('забы', 'заби')):
            word = 'забыл'
        words.append(word)
    return words


def repeated_answer(text, previous):
    words = repeat_words(text)
    for old in previous:
        old_words = repeat_words(old)
        if words == old_words:
            return True
        match = SequenceMatcher(None, words, old_words, autojunk=False)
        if min(len(words), len(old_words)) >= 3 and match.ratio() >= 0.8:
            return True
        # Catch the same punchline with a different short opening, while allowing
        # ordinary style particles such as "ну ти шо" in otherwise fresh replies.
        block = match.find_longest_match()
        if block.size >= 3 and any(len(word) >= 5 and word not in REPEAT_FILLER
                                   for word in words[block.a:block.a + block.size]):
            return True
    return False


def response_problem(text, previous=(), subject=''):
    reason = answer_problem(text)
    if reason or text.strip() == '__SILENCE__':
        return reason
    if CYBORG_PATTERN.search(text) and not CYBORG_PATTERN.search(subject):
        return 'unrelated_topic'
    if BUBUS_PATTERN.search(text) and FORGET_PATTERN.search(text) and not FORGET_PATTERN.search(subject):
        return 'stale_catchphrase'
    if repeated_answer(text, previous):
        return 'repeated_response'
    return None


def answer_problem(text):
    text = text.strip()
    if text == '__SILENCE__':
        return None
    if len(text) < 2:
        return 'empty_response'
    if len(text) > 220 or len(text.split()) > 35:
        return 'response_too_long'
    if '<think' in text.lower() or '</think' in text.lower():
        return 'reasoning_in_response'
    if len(text.splitlines()) > 1:
        return 'multiline_response'
    if re.match(r'(?i)(хорошо[, ]+пользователь|пользователь (пишет|спрашивает)|нужно ответить|анализ|вот (ответ|реплика)|я должен)', text):
        return 'reasoning_in_response'
    if re.search(r'(?i)чурк|свинорус|хохл|негр|пидор|пидар|ниггер|жид[а-я]|москал', text):
        return 'restricted_terms'
    if re.search(r'\b\d{6,12}:[A-Za-z0-9_-]{25,}\b', text):
        return 'sensitive_data'
    if clean_text(text) != text:
        return 'sensitive_data'
    return None


def valid_answer(text):
    if answer_problem(text):
        return None
    return text.strip()


class Bot:
    def __init__(self, config, telegram=None, store=None):
        self.config = config
        self.telegram = telegram or Telegram(config['token'])
        self.store = store or Store(STATE / 'bot.sqlite3')
        self.jobs = queue.Queue(maxsize=4)
        self.utility_jobs = queue.Queue(maxsize=4)
        self.services = ChatServices()
        self.lock = threading.Lock()
        self.auto_pending = False
        self.active_job = False
        self.active_utility = False
        self.bot_id = 0
        self.username = ''
        self.prompt = (ROOT / 'prompt.txt').read_text(encoding='utf-8')
        self.random = random.SystemRandom()

    def allowed(self, chat_id):
        return chat_id in (self.config['owner_id'], self.store.get('chat_id'))

    def auto_eligible(self, chat_id, now=None):
        now = now or time.time()
        if not chat_id or chat_id != self.store.get('chat_id') or not self.store.get('automatic', False):
            return False
        if datetime.fromtimestamp(now, MOSCOW).hour < 10:
            return False
        if now < self.store.get('blocked_until', 0):
            return False
        if now - self.store.latest_human(chat_id) > 7200:
            return False
        return self.store.counts().get('automatic', 0) < 5 and now - self.store.last_send(chat_id) >= 3600

    def enqueue(self, chat_id, text='', reply_to=None, automatic=False, random_quote=False,
                trigger_words=None, utility=None):
        if not self.allowed(chat_id):
            return False
        with self.lock:
            if automatic and (self.auto_pending or not self.auto_eligible(chat_id)):
                return False
            if not automatic:
                if time.time() - self.store.get(f'request:{chat_id}', 0) < 15:
                    self.record_skip(chat_id, 'request_cooldown')
                    return False
            try:
                target_queue = self.utility_jobs if utility else self.jobs
                target_queue.put_nowait({'chat_id': chat_id, 'text': clean_text(text)[:1000],
                                     'reply_to': reply_to, 'automatic': automatic,
                                     'random_quote': random_quote,
                                     'trigger_words': trigger_words or [],
                                     'utility': utility,
                                     'queued_at': time.time()})
            except queue.Full:
                if not automatic:
                    self.record_skip(chat_id, 'queue_full')
                return False
            if automatic:
                self.auto_pending = True
            else:
                self.store.set(f'request:{chat_id}', time.time())
                LOG.info('Обращение поставлено в очередь')
        return True

    def record_skip(self, chat_id, reason):
        self.store.set(f'last_skip:{chat_id}', {'reason': reason, 'time': time.time()})
        LOG.info('Обращение пропущено: причина=%s', reason)

    def notify(self, chat_id, text, reply_to=None):
        try:
            self.telegram.send(chat_id, text, reply_to)
        except APIError as error:
            LOG.warning('Не удалось отправить служебный ответ: %s', error)

    def greet_new_members(self, message):
        chat_id = message['chat']['id']
        if message['chat']['type'] not in ('group', 'supergroup'):
            return
        if chat_id != self.store.get('chat_id') or time.time() - message.get('date', 0) > 120:
            return
        target_joined = any(not member.get('is_bot')
                            and (member.get('username') or '').casefold() == WELCOME_USERNAME
                            for member in message.get('new_chat_members', []))
        if not target_joined or time.time() < self.store.get('blocked_until', 0):
            return
        send_id = self.store.claim_greeting(chat_id, message['message_id'])
        if send_id is None:
            return
        try:
            sent = self.telegram.send(chat_id, WELCOME_TEXT, message['message_id'])
            self.store.add_message(sent, human=False)
            self.store.finish_send(send_id, 'sent')
            LOG.info('Приветствие при входе отправлено')
        except APIError as error:
            self.store.finish_send(send_id, 'failed_or_uncertain')
            if error.retry_after:
                self.store.set('blocked_until', time.time() + error.retry_after)
            if error.code == 403:
                self.store.set('automatic', False)
            LOG.warning('Приветствие не подтверждено, повтор не выполняется: %s', error)

    def handle(self, update):
        message = update.get('message')
        if not message:
            return
        # The service-message sender can be an inviter, including another bot.
        # Match the joining member, never the sender or the displayed name.
        if message.get('new_chat_members'):
            self.greet_new_members(message)
            return
        if message.get('from', {}).get('is_bot'):
            return
        chat_id = message['chat']['id']
        user_id = message.get('from', {}).get('id')
        owner = user_id == self.config['owner_id']
        private = message['chat']['type'] == 'private'
        text = message.get('text') or message.get('caption') or ''
        # On group migration require the owner to explicitly resume automation.
        if message.get('migrate_to_chat_id') and chat_id == self.store.get('chat_id'):
            self.store.set('chat_id', message['migrate_to_chat_id'])
            self.store.set('automatic', False)
            return
        command = text.split(maxsplit=1)[0].lower() if text.startswith('/') else ''
        if '@' in command and command.split('@', 1)[1] != self.username.lower():
            return
        command = command.split('@')[0]
        if private and command == '/whoami':
            self.notify(chat_id, f'Твой Telegram ID: {user_id}')
            return
        if private and not owner:
            return
        if (owner and command == '/bubus' and not private
                and time.time() - message.get('date', 0) <= 120
                and chat_id != self.store.get('chat_id')):
            self.store.set('chat_id', chat_id)
            self.store.set('automatic', False)
            self.store.set('next_auto', time.time() + self.random.uniform(3600, 10800))
        if not self.allowed(chat_id):
            return
        if owner and command in ('/start', '/help'):
            self.notify(chat_id, 'Я бот-пародия. Первый /bubus от владельца подключает группу; периодические реплики остаются выключенными.\n' + '\n'.join(f'{cmd} — {description}' for cmd, description in COMMANDS))
            return
        if owner and command in ('/auto_on', '/auto_off'):
            target = self.store.get('chat_id')
            if not target:
                self.notify(chat_id, 'Сначала отправь /bubus в нужной группе.')
                return
            enabled = command == '/auto_on'
            self.store.set('automatic', enabled)
            if enabled:
                self.store.set('next_auto', time.time() + self.random.uniform(3600, 10800))
            self.notify(chat_id, 'Периодические реплики включены: до 5 в сутки, интервал от часа, тихие часы 00:00–10:00 МСК.' if enabled else 'Периодические реплики выключены. Ответы по обращению работают.')
            return
        if owner and command == '/status':
            counts = self.store.counts()
            target = self.store.get('chat_id', chat_id)
            seen = self.store.get(f'last_direct:{target}', 0)
            seen_text = datetime.fromtimestamp(seen, MOSCOW).strftime('%H:%M:%S МСК') if seen else 'ещё не получено'
            skip = self.store.get(f'last_skip:{target}')
            skip_text = (f'{skip["reason"]} ({datetime.fromtimestamp(skip["time"], MOSCOW).strftime("%H:%M:%S МСК")})'
                         if skip else 'нет')
            pause = max(0, int(15 - (time.time() - max(self.store.get(f'request:{target}', 0),
                                                      self.store.last_send(target))) + 0.999))
            self.notify(chat_id, f'Группа: {self.store.get("chat_id", "не подключена")}\n'
                        f'Автоматически: {"включено" if self.store.get("automatic", False) else "выключено"}\n'
                        f'Попыток сегодня: автоматически {counts.get("automatic", 0)}/5; по обращению {counts.get("manual", 0)} (без дневного лимита)\n'
                        f'В очереди: {self.jobs.qsize()}; обработка: {"идёт" if self.active_job else "нет"}\n'
                        f'Курсы/погода: в очереди {self.utility_jobs.qsize()}; обработка: {"идёт" if self.active_utility else "нет"}\n'
                        f'Пауза между обращениями: {pause} сек.\n'
                        f'Последнее обращение в группе: {seen_text}\nПоследний пропуск: {skip_text}\n'
                        f'Модель: {self.config["model"]}')
            return
        if command and command not in ('/bubus', '/weather', '/convert'):
            return
        # Never react to a backlog of old updates after VPN downtime.
        if time.time() - message.get('date', 0) > 120:
            return
        reply = message.get('reply_to_message', {})
        reply_to_bot = reply.get('from', {}).get('id') == self.bot_id
        mentioned = bool(self.username and re.search(r'@' + re.escape(self.username) + r'\b', text, re.I))
        # A user replying here solely to summon another bot is not addressing us.
        if not command and not mentioned and re.fullmatch(r'(?:@[a-z0-9_]*bot\s*)+', text.strip(), re.I):
            return
        triggers = list(dict.fromkeys(' '.join(match.group().casefold().split())
                                     for match in TRIGGER_PATTERN.finditer(text)))
        if command == '/bubus':
            parts = text.split(maxsplit=1)
            argument = parts[1] if len(parts) > 1 else ''
        else:
            argument = re.sub(r'@' + re.escape(self.username) + r'\b', '', text, flags=re.I).strip() if mentioned else text
        utility = service_request(argument, command)
        if not self.store.add_message(dict(message, text=argument)):
            # An empty /bubus or bare mention still uses duplicate detection,
            # while generation receives an empty request to use live context.
            if argument.strip() or not self.store.add_message(message):
                return
        if private or command == '/bubus' or mentioned or reply_to_bot or triggers or utility:
            self.store.set(f'last_direct:{chat_id}', time.time())
            LOG.info('Получено прямое обращение; ключевых совпадений=%s', len(triggers))
            if not self.enqueue(chat_id, argument, message['message_id'],
                                random_quote=(command == '/bubus' and not argument.strip()),
                                trigger_words=triggers, utility=utility):
                if owner and private:
                    self.notify(chat_id, 'Подожди немного: действует пауза или очередь заполнена.')
        elif not private and self.random.random() < 0.03:
            self.enqueue(chat_id, text, message['message_id'], automatic=True)

    def generate(self, job):
        previous = self.store.recent_answers(job['chat_id'], limit=30)
        if job.get('random_quote'):
            for text in random_candidates(limit=500):
                answer = valid_answer(text)
                if (answer and answer != '__SILENCE__' and len(answer.split()) <= 12
                        and answer == answer.lower() and not re.search(r'\d|@|\[|\]', answer)
                        and ANGRY_QUOTE_PATTERN.search(answer)
                        and not response_problem(answer, previous)):
                    return surzhyk_text(answer)
            LOG.warning('Случайная реплика не найдена: причина=no_random_quote')
            return None
        # Own previous output must not become the subject of an unrelated request.
        context = [row for row in self.store.context(job['chat_id'], limit=20)
                   if row['human'] and not row['text'].startswith('/')][-6:]
        call_words = answer_key(job['text']).split()
        nickname_call = bool(call_words) and (all(word in TRIGGER_WORDS for word in call_words)
                                             or ' '.join(call_words) in TRIGGER_PHRASES)
        if nickname_call:
            # A bare nickname is a fresh call, not a query about old conversations.
            context = []
        subject = job['text'] or ' '.join(row['text'] for row in context[-2:])
        examples = [] if nickname_call else find_examples(subject)
        examples = [row for row in examples
                    if not response_problem(row['response'], previous, subject)
                    and (not CYBORG_PATTERN.search(row.get('context', ''))
                         or CYBORG_PATTERN.search(subject))]
        data = {'mode': 'automatic' if job['automatic'] else 'manual',
                'style_examples': examples, 'live_context': context,
                'trigger_words': job.get('trigger_words', []),
                'current_request': job['text']}
        reason = None
        for attempt in range(2):
            prompt = self.prompt
            if attempt:
                prompt += '\nПредыдущий вариант не подошёл. Дай новую готовую реплику на 3–12 слов одной строкой по теме current_request. Без анализа, пояснений и оскорблений защищённых групп. Не повторяй уже сказанные реплики, даже с другой пунктуацией.'
            retry_data = dict(data, style_examples=[], live_context=context[-2:],
                              previous_failure=reason) if attempt else data
            try:
                result = http_json(self.config['ollama_url'] + '/api/chat', {
                    'model': self.config['model'], 'stream': False, 'keep_alive': '24h',
                    'messages': [{'role': 'system', 'content': prompt},
                                 {'role': 'user', 'content': json.dumps(retry_data, ensure_ascii=False)}],
                    'options': {'num_ctx': 4096, 'num_thread': 16,
                                'num_predict': 160 if attempt and reason == 'length_limit' else 100,
                                'temperature': 0.75, 'repeat_penalty': 1.08},
                }, timeout=90)
            except APIError as error:
                # A timed-out local generation may still be running. Do not pile
                # another request onto it; use the local fallback for direct calls.
                reason = 'model_unavailable'
                LOG.warning('Генерация недоступна: причина=%s; HTTP=%s; попытка=%s/2',
                            reason, error.code, attempt + 1)
                if error.code not in (429, 500, 502, 503, 504) or attempt:
                    return None
                continue
            if not isinstance(result, dict):
                result = {}
            message = result.get('message')
            answer = message.get('content') if isinstance(message, dict) else None
            if result.get('error'):
                reason = 'model_error'
            elif not isinstance(answer, str):
                reason = 'malformed_response'
            elif result.get('done_reason') == 'length':
                reason = 'length_limit'
            else:
                reason = response_problem(answer, previous, subject)
            answer = answer.strip() if isinstance(answer, str) else ''
            if not reason and answer == '__SILENCE__' and not job['automatic']:
                reason = 'silence_on_direct_request'
            if not reason:
                return surzhyk_text(answer)
            # Log only a fixed reason code, never generated text or requests.
            LOG.warning('Ответ модели отклонён: причина=%s; попытка=%s/2', reason, attempt + 1)
        return None

    def fallback_answer(self, job):
        previous = self.store.recent_answers(job['chat_id'], limit=30)
        previous_keys = {answer_key(text) for text in previous}
        pools = [replies for pattern, replies in FALLBACK_TOPICS if pattern.search(job.get('text', ''))]
        pools.append(FALLBACK_REPLIES)
        for pool in pools:
            available = [text for text in pool
                         if valid_answer(text) and answer_key(text) not in previous_keys]
            if available:
                varied = [text for text in available if not repeated_answer(text, previous)]
                return self.random.choice(varied or available)
        return None

    def process_job(self, job):
        chat_id = job['chat_id']
        if not self.allowed(chat_id) or time.time() - job['queued_at'] > 180:
            if not job['automatic']:
                self.record_skip(chat_id, 'chat_changed' if not self.allowed(chat_id) else 'job_expired')
            return
        if job['automatic'] and not self.auto_eligible(chat_id):
            return
        utility = job.get('utility')
        if utility:
            try:
                answer = self.services.answer(utility)
            except ServiceError as error:
                LOG.warning('Справочный ответ недоступен: сервис=%s; причина=%s', utility['kind'], error)
                answer = service_error_reply(utility['kind'], str(error))
        else:
            try:
                answer = self.generate(job)
            except (APIError, sqlite3.Error, OSError) as error:
                LOG.warning('Генерация не выполнена: причина=local_generation_error; тип=%s',
                            type(error).__name__)
                answer = None
        if answer == '__SILENCE__':
            return
        if not answer:
            if job['automatic']:
                return
            answer = self.fallback_answer(job)
            if not answer:
                return
            LOG.info('Использована резервная реплика, режим=manual')
        # Recheck after generation: /auto_off and rebinding can arrive meanwhile.
        if (not self.allowed(chat_id) or time.time() - job['queued_at'] > 180
                or (job['automatic'] and not self.auto_eligible(chat_id))):
            if not job['automatic']:
                self.record_skip(chat_id, 'chat_changed' if not self.allowed(chat_id) else 'job_expired')
            return
        if time.time() < self.store.get('blocked_until', 0):
            if not job['automatic']:
                self.record_skip(chat_id, 'telegram_retry_after')
            return
        kind = 'automatic' if job['automatic'] else 'manual'
        send_id = self.reserve_utility_send(job) if utility else self.store.claim_send(kind, chat_id)
        if send_id is None:
            if not job['automatic'] and not utility:
                self.record_skip(chat_id, 'send_cooldown')
            return
        try:
            sent = self.telegram.send(chat_id, answer if utility else surzhyk_text(answer), job['reply_to'])
            self.store.add_message(sent, human=False)
            self.store.finish_send(send_id, 'sent')
            LOG.info('Реплика отправлена, режим=%s', kind)
        except APIError as error:
            self.store.finish_send(send_id, 'failed_or_uncertain')
            if error.retry_after:
                self.store.set('blocked_until', time.time() + error.retry_after)
            if error.code == 403:
                self.store.set('automatic', False)
            LOG.warning('Отправка не подтверждена, повтор не выполняется: %s', error)

    def reserve_utility_send(self, job):
        # Fast API calls must not lose their answer to a simultaneous LLM reply.
        chat_id = job['chat_id']
        while True:
            now = time.time()
            if not self.allowed(chat_id):
                self.record_skip(chat_id, 'chat_changed')
                return None
            if now < self.store.get('blocked_until', 0):
                self.record_skip(chat_id, 'telegram_retry_after')
                return None
            delay = max(0, 15 - (now - self.store.last_send(chat_id)))
            if now + delay - job['queued_at'] > 180:
                self.record_skip(chat_id, 'job_expired')
                return None
            if delay:
                time.sleep(delay)
                continue
            send_id = self.store.claim_send('manual', chat_id)
            if send_id is not None:
                return send_id
            time.sleep(0.1)

    def utility_worker(self):
        while True:
            job = self.utility_jobs.get()
            try:
                self.active_utility = True
                LOG.info('Начата обработка справочного запроса; сервис=%s', job['utility']['kind'])
                self.process_job(job)
            except Exception as error:
                LOG.error('Справочное задание не выполнено: %s', type(error).__name__)
            finally:
                self.active_utility = False
                self.utility_jobs.task_done()

    def worker(self):
        try:
            http_json(self.config['ollama_url'] + '/api/chat',
                      {'model': self.config['model'], 'messages': [], 'keep_alive': '24h',
                       'stream': False, 'options': {'num_ctx': 4096, 'num_thread': 16}}, timeout=180)
            LOG.info('Модель загружена в память')
        except APIError as error:
            LOG.warning('Предзагрузка модели не удалась: %s', error)
        while True:
            job = self.jobs.get()
            try:
                self.active_job = True
                LOG.info('Начата обработка обращения; режим=%s', 'automatic' if job['automatic'] else 'manual')
                self.process_job(job)
            except Exception as error:
                # Do not log prompt text, Telegram token, or the complete response.
                LOG.error('Задание не выполнено: %s', type(error).__name__)
            finally:
                self.active_job = False
                if job['automatic']:
                    with self.lock:
                        self.auto_pending = False
                self.jobs.task_done()

    def scheduler(self):
        while True:
            try:
                now = time.time()
                next_time = self.store.get('next_auto', now + 3600)
                chat_id = self.store.get('chat_id')
                if now >= next_time:
                    self.store.set('next_auto', now + self.random.uniform(3600, 10800))
                    if self.auto_eligible(chat_id):
                        self.enqueue(chat_id, automatic=True)
            except Exception as error:
                LOG.error('Ошибка расписания: %s', type(error).__name__)
            time.sleep(10)

    def run(self):
        identity = self.telegram.call('getMe')
        self.bot_id = identity['id']
        self.username = identity['username']
        if self.telegram.call('getWebhookInfo').get('url'):
            raise APIError('У этого токена уже настроен webhook; нужен отдельный бот')
        self.telegram.call('setMyCommands', commands=[{'command': cmd[1:], 'description': desc} for cmd, desc in COMMANDS])
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.utility_worker, daemon=True).start()
        threading.Thread(target=self.scheduler, daemon=True).start()
        offset = self.store.get('offset', self.config.get('initial_offset', 0))
        LOG.info('Бот @%s запущен; периодический режим=%s', self.username, self.store.get('automatic', False))
        backoff = 2
        while True:
            try:
                updates = self.telegram.call('getUpdates', offset=offset, timeout=20,
                                             allowed_updates=['message'])
                for update in updates:
                    self.handle(update)
                    offset = update['update_id'] + 1
                    self.store.set('offset', offset)
                backoff = 2
            except APIError as error:
                LOG.warning('Получение сообщений недоступно: %s', error)
                time.sleep(max(backoff, min(error.retry_after, 60)))
                backoff = min(backoff * 2, 60)
            except Exception as error:
                LOG.error('Ошибка обработки обновления: %s', type(error).__name__)
                time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        STATE.mkdir(exist_ok=True, mode=0o700)
        LOG.info('Архивных примеров: %s', build_index())
        Bot(load_config()).run()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        LOG.error('Запуск не выполнен: %s', type(error).__name__)
        raise SystemExit(1)
