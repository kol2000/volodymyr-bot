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
from zoneinfo import ZoneInfo

from common import APIError, ROOT, STATE, Telegram, clean_text, http_json, load_config
from retrieval import build_index, find_examples, random_candidates

LOG = logging.getLogger('volodymyr')
MOSCOW = ZoneInfo('Europe/Moscow')
WELCOME_USERNAME = 'leonadosasa'
WELCOME_TEXT = 'оо алкаш епти ти де бил?'
TRIGGER_WORDS = ('бубуська', 'володька', 'рахиня', 'украина', 'война', 'киев',
                 'цаца', 'цацик', 'бубус')
TRIGGER_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, TRIGGER_WORDS)) + r')(?!\w)', re.I)
ANGRY_QUOTE_PATTERN = re.compile(r'\b(?:бля\w*|сука\w*|заеб\w*|задолб\w*|пизд\w*|'
                                 r'ёб\w*|еба\w*|ебл\w*|охуе\w*|дурак\w*|идиот\w*|'
                                 r'туп\w*|нахуй|нахер|нахуя|завали|хер\w*)\b', re.I)
COMMANDS = [('/bubus', 'случайная фраза; с текстом — ответ по теме'),
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
            limit = 5 if kind == 'automatic' else 30
            count = db.execute('SELECT count(*) FROM sends WHERE day=? AND kind=?', (day, kind)).fetchone()[0]
            if count >= limit:
                return None
            row = db.execute('SELECT max(timestamp) FROM sends WHERE chat_id=?', (chat_id,)).fetchone()
            last = row[0] or 0
            if now - last < (3600 if kind == 'automatic' else 15):
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


def answer_key(text):
    # Ignore capitalization, punctuation and spaces when detecting repeats.
    return ' '.join(re.findall(r'\w+', text.casefold().replace('ё', 'е')))


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
        self.lock = threading.Lock()
        self.auto_pending = False
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
                trigger_words=None):
        if not self.allowed(chat_id):
            return False
        with self.lock:
            if automatic and (self.auto_pending or not self.auto_eligible(chat_id)):
                return False
            if not automatic:
                if time.time() - self.store.get(f'request:{chat_id}', 0) < 15:
                    return False
                if self.store.counts().get('manual', 0) >= 30:
                    return False
            try:
                self.jobs.put_nowait({'chat_id': chat_id, 'text': clean_text(text)[:1000],
                                     'reply_to': reply_to, 'automatic': automatic,
                                     'random_quote': random_quote,
                                     'trigger_words': trigger_words or [],
                                     'queued_at': time.time()})
            except queue.Full:
                return False
            if automatic:
                self.auto_pending = True
            else:
                self.store.set(f'request:{chat_id}', time.time())
        return True

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
            self.notify(chat_id, f'Группа: {self.store.get("chat_id", "не подключена")}\n'
                        f'Автоматически: {"включено" if self.store.get("automatic", False) else "выключено"}\n'
                        f'Попыток сегодня: автоматически {counts.get("automatic", 0)}/5; по обращению {counts.get("manual", 0)}/30\n'
                        f'В очереди: {self.jobs.qsize()}\nМодель: {self.config["model"]}')
            return
        if command and command != '/bubus':
            return
        # Never react to a backlog of old updates after VPN downtime.
        if time.time() - message.get('date', 0) > 120:
            return
        reply = message.get('reply_to_message', {})
        reply_to_bot = reply.get('from', {}).get('id') == self.bot_id
        mentioned = bool(self.username and re.search(r'@' + re.escape(self.username) + r'\b', text, re.I))
        triggers = list(dict.fromkeys(match.group().casefold() for match in TRIGGER_PATTERN.finditer(text)))
        if command == '/bubus':
            parts = text.split(maxsplit=1)
            argument = parts[1] if len(parts) > 1 else ''
        else:
            argument = re.sub(r'@' + re.escape(self.username) + r'\b', '', text, flags=re.I).strip() if mentioned else text
        if not self.store.add_message(dict(message, text=argument)):
            # An empty /bubus or bare mention still uses duplicate detection,
            # while generation receives an empty request to use live context.
            if argument.strip() or not self.store.add_message(message):
                return
        if private or command == '/bubus' or mentioned or reply_to_bot or triggers:
            if not self.enqueue(chat_id, argument, message['message_id'],
                                random_quote=(command == '/bubus' and not argument.strip()),
                                trigger_words=triggers):
                if owner:
                    self.notify(chat_id, 'Подожди немного: бот занят или достигнут лимит ответов.')
        elif not private and self.random.random() < 0.03:
            self.enqueue(chat_id, text, message['message_id'], automatic=True)

    def generate(self, job):
        previous = self.store.recent_answers(job['chat_id'])
        previous_keys = {answer_key(text) for text in previous}
        if job.get('random_quote'):
            for text in random_candidates(limit=500):
                answer = valid_answer(text)
                if (answer and answer != '__SILENCE__' and len(answer.split()) <= 12
                        and answer == answer.lower() and not re.search(r'\d|@|\[|\]', answer)
                        and ANGRY_QUOTE_PATTERN.search(answer)
                        and answer_key(answer) not in previous_keys):
                    return answer
            LOG.warning('Случайная реплика не найдена: причина=no_random_quote')
            return None
        # Own previous output must not become the subject of an unrelated request.
        context = [row for row in self.store.context(job['chat_id'], limit=20)
                   if row['human'] and not row['text'].startswith('/')][-6:]
        query = job['text'] or ' '.join(row['text'] for row in context[-3:])
        examples = find_examples(query)
        examples = [row for row in examples if answer_key(row['response']) not in previous_keys]
        data = {'mode': 'automatic' if job['automatic'] else 'manual',
                'style_examples': examples, 'live_context': context,
                'recent_bot_responses_do_not_repeat': previous,
                'trigger_words': job.get('trigger_words', []),
                'current_request': job['text']}
        reason = None
        for attempt in range(2):
            prompt = self.prompt
            if attempt:
                prompt += '\nПредыдущий вариант не подошёл. Дай новую готовую реплику на 3–12 слов одной строкой по теме current_request. Без анализа, пояснений и оскорблений защищённых групп. Не повторяй уже сказанные реплики, даже с другой пунктуацией.'
            result = http_json(self.config['ollama_url'] + '/api/chat', {
                'model': self.config['model'], 'stream': False, 'keep_alive': '24h',
                'messages': [{'role': 'system', 'content': prompt},
                             {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
                'options': {'num_ctx': 4096, 'num_thread': 16,
                            'num_predict': 160 if attempt and reason == 'length_limit' else 100,
                            'temperature': 0.75, 'repeat_penalty': 1.08},
            }, timeout=180)
            answer = (result.get('message') or {}).get('content') or ''
            if result.get('error'):
                reason = 'model_error'
            elif result.get('done_reason') == 'length':
                reason = 'length_limit'
            else:
                reason = answer_problem(answer)
            answer = answer.strip()
            if not reason and answer == '__SILENCE__' and not job['automatic']:
                reason = 'silence_on_direct_request'
            if not reason and answer != '__SILENCE__' and answer_key(answer) in previous_keys:
                reason = 'repeated_response'
            if not reason:
                return answer
            # Log only a fixed reason code, never generated text or requests.
            LOG.warning('Ответ модели отклонён: причина=%s; попытка=%s/2', reason, attempt + 1)
        return None

    def process_job(self, job):
        chat_id = job['chat_id']
        if not self.allowed(chat_id) or time.time() - job['queued_at'] > 180:
            return
        if job['automatic'] and not self.auto_eligible(chat_id):
            return
        answer = self.generate(job)
        if answer == '__SILENCE__':
            return
        if not answer:
            if not job['automatic']:
                self.notify(chat_id, 'Не получилось ответить. Попробуй позже.', job['reply_to'])
            return
        # Recheck after generation: /auto_off and rebinding can arrive meanwhile.
        if not self.allowed(chat_id) or (job['automatic'] and not self.auto_eligible(chat_id)):
            return
        if time.time() < self.store.get('blocked_until', 0):
            return
        kind = 'automatic' if job['automatic'] else 'manual'
        send_id = self.store.claim_send(kind, chat_id)
        if send_id is None:
            return
        try:
            sent = self.telegram.send(chat_id, answer, job['reply_to'])
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
                self.process_job(job)
            except Exception as error:
                # Do not log prompt text, Telegram token, or the complete response.
                LOG.error('Задание не выполнено: %s', type(error).__name__)
            finally:
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
