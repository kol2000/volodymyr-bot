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
from html import escape
from zoneinfo import ZoneInfo

from common import APIError, HTMLMessage, ROOT, STATE, Telegram, clean_text, http_json, load_config, surzhyk_text, parody_text
from chat_services import ChatServices, ServiceError, service_error_reply, service_request
from community import (COMMUNITY_COMMANDS, GREETINGS, OWNER_FEATURES, community_request,
                       help_text, period_start, statistics_text, summary_excerpt, summary_text)
from retrieval import build_index, find_examples, random_candidates
from feedback import praise_signal, rating_signal, relevant_approved, salary_acknowledgement
from question_answers import (PROMPT as QUESTION_PROMPT, NEUTRAL_PROMPT, FALLBACKS as QUESTION_FALLBACKS,
                              ends_question, thoughtful_request, answer_style, substantive_style)
from assistant_features import (FeatureStore, FEATURE_COMMANDS, FOLLOWUP, CALL as FEATURE_CALL, feature_request,
                                negative_reason, memory_context)
from style_profile import profile_for_request
from answer_quality import (answer_problem, banter_coherence_problem, banter_relation_problem, checked_concise_extract, concise_extract, concise_problem, concise_word_limit, contextual_request, diagnostic_retry_guidance, explanation_problem,
                            grounding_problem, question_context)
from conversation import (BOT_ALIASES, ambiguous_subject, amendment_request, banter_brief, banter_request, control_remainder, dialogue_roles, inline_control, missing_reference,
                          response_control as standalone_control, verification_query)
from learning import LearningStore, LEARNING_COMMANDS, correction_request
from participants import ParticipantStore, PARTICIPANT_COMMANDS, participant_request
from fact_check import FactChecker, FactError
from inference import record_generation, runner_options
from goal_videos import GoalVideos
from football_posts import FootballPosts
from live_football import (Football, FootballError, PREFIX as FOOTBALL_PREFIX,
                           COMMANDS as FOOTBALL_COMMANDS, football_request, football_correction,
                           error_reply as football_error_reply)

LOG = logging.getLogger('volodymyr')
MOSCOW = ZoneInfo('Europe/Moscow')
HISTORY_LIMIT = 20000
WELCOME_USERNAME = 'leonadosasa'
WELCOME_TEXT = 'оо алкаш епти ти де бил?'
TEASE_USERNAME = 'frankyvidadim'


def tease_available(history, now):
    day = datetime.fromtimestamp(now, MOSCOW).date()
    return (not history or now - max(history) >= 6 * 3600) and sum(
        datetime.fromtimestamp(t, MOSCOW).date() == day for t in history) < 2


TRIGGER_WORDS = ('бубуська', 'володька', 'рахиня', 'украина', 'война', 'киев',
                 'цаца', 'цацик', 'бубус', 'цацыг', 'хряк', 'свинья', 'сосатка',
                 'крым', 'крим', 'даг', 'москва', 'путин', 'путен', 'вова', 'свин')
TRIGGER_PHRASES = ('крым наш',)
TRIGGER_FORMS = {word: word for word in TRIGGER_WORDS}
for name, forms in {
    'володька': ('володьки', 'володьке', 'володьку', 'володькой', 'володькою', 'володько'),
    'бубус': ('бубуса', 'бубусу', 'бубусом', 'бубусе'),
    'бубуська': ('бубуськи', 'бубуське', 'бубуську', 'бубуськой', 'бубуською'),
}.items():
    TRIGGER_FORMS.update({form: name for form in forms})
# Match phrases first so "крым наш" is one trigger, with flexible whitespace.
TRIGGER_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(
    re.escape(term).replace(r'\ ', r'\s+') for term in (*TRIGGER_PHRASES, *TRIGGER_FORMS)) + r')(?!\w)', re.I)
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
            ('/more', 'раскрыть предыдущий ответ подробнее'),
            ('/check', 'проверить утверждение по интернет-источникам'),
            ('/remember', 'запомнить договорённость чата'), ('/memory', 'записи памяти'),
            ('/memory_edit', 'исправить свою запись: номер и текст'), ('/forget', 'удалить свою запись: номер'),
            ('/correct', 'ответом на реплику: предложить исправленный ответ'),
            ('/confirm', 'ответом на квитанцию: подтвердить своё исправление'),
            ('/person', 'кто есть кто: /person Сижик'),
            ('/person_set', 'владельцу: @username | прозвища через запятую | заметка'),
            ('/person_forget', 'владельцу: удалить запись участника по @username'),
            ('/bank', 'виртуальная зарплата Володьки в гривнах'),
            ('/weather', 'погода сейчас: /weather Орёл'),
            ('/convert', 'пересчитать сумму: /convert 100 USDT'),
            ('/football', 'матчи дня с главной ФНК: /football сегодня'),
            ('/football_on', 'владельцу: включить футбольные уведомления'),
            ('/football_off', 'владельцу: выключить футбольные уведомления'),
            ('/football_status', 'состояние футбольного источника'),
            ('/football_videos_status', 'видео голов: источники и ожидание'),
            ('/football_videos_on', 'добавлять видео в посты о голах'),
            ('/football_videos_off', 'выключить добавление видео'),
            ('/summary', 'пересказ чата: сегодня, неделю, месяц'),
            ('/stats', 'статистика чата'), ('/top', 'топ сообщений: /top неделю'),
            ('/choose', 'выбор: /choose пицца | шаурма'), ('/rules', 'правила чата'),
            ('/help', 'возможности и примеры'),
            ('/activity', 'владельцу: вероятность самостоятельного ответа, 1–20%'),
            ('/adaptive_on', 'владельцу: учитывать темп беседы'),
            ('/adaptive_off', 'владельцу: отключить учёт темпа беседы'),
            ('/greetings_on', 'владельцу: приветствовать новых участников'),
            ('/greetings_off', 'владельцу: выключить общие приветствия'),
            ('/rules_set', 'владельцу: задать правила'), ('/settings', 'владельцу: настройки'),
            ('/auto_on', 'включить периодические реплики'),
            ('/auto_off', 'выключить периодические реплики'),
            ('/status', 'показать состояние'), ('/whoami', 'показать мой Telegram ID')]


class Store(FeatureStore, LearningStore, ParticipantStore):
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
                CREATE TABLE IF NOT EXISTS activity (
                    chat_id INTEGER, day TEXT, user_id INTEGER, speaker TEXT, count INTEGER,
                    PRIMARY KEY(chat_id,day,user_id));
                CREATE INDEX IF NOT EXISTS activity_chat_day ON activity(chat_id,day);
                CREATE TABLE IF NOT EXISTS approved_replies (
                    chat_id INTEGER, response_key TEXT, response TEXT, context TEXT,
                    score INTEGER, timestamp REAL, PRIMARY KEY(chat_id,response_key));
                CREATE TABLE IF NOT EXISTS reply_feedback (
                    chat_id INTEGER, response_key TEXT, user_id INTEGER, timestamp REAL,
                    PRIMARY KEY(chat_id,response_key,user_id));
                CREATE TABLE IF NOT EXISTS salary_accounts (
                    chat_id INTEGER PRIMARY KEY, balance INTEGER, plus INTEGER, minus INTEGER);
                CREATE TABLE IF NOT EXISTS salary_votes (
                    chat_id INTEGER, message_id INTEGER, user_id INTEGER, direction INTEGER, timestamp REAL,
                    PRIMARY KEY(chat_id,message_id,user_id));
                CREATE TABLE IF NOT EXISTS feedback_links (
                    chat_id INTEGER, message_id INTEGER, answer_id INTEGER, text TEXT, timestamp REAL,
                    PRIMARY KEY(chat_id,message_id));
            ''')
            if 'user_id' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN user_id INTEGER')
            columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
            for name, kind in (('request_text', 'TEXT'), ('learnable', 'INTEGER'), ('rateable', 'INTEGER'),
                               ('reply_to', 'INTEGER'), ('answer_mode', 'TEXT'), ('body', 'TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE messages ADD COLUMN {name} {kind}')
        self.init_features()
        self.init_learning()
        self.init_participants()

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

    def add_message(self, message, human=True, request_text='', learnable=False, rateable=None):
        chat_id = message['chat']['id']
        text = message.get('text') or message.get('caption') or ''
        if not text.strip():
            return False
        speaker = 'бот' if not human else clean_text(' '.join(
            message.get('from', {}).get(k, '') for k in ('first_name', 'last_name')).strip() or 'участник')[:80]
        user_id = message.get('from', {}).get('id') if human else None
        stamp = message.get('date', time.time())
        now = time.time()
        with self.db() as db:
            inserted = db.execute('INSERT OR IGNORE INTO messages '
                                  '(chat_id,message_id,timestamp,speaker,text,human,user_id,request_text,learnable,rateable,reply_to,answer_mode,body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (chat_id, message['message_id'], stamp,
                                   speaker, clean_text(text)[:1000], int(human), user_id,
                                   clean_text(request_text)[:240], int(learnable and not human),
                                   int(not human and (learnable if rateable is None else rateable)),
                                   message.get('_reply_to') or message.get('reply_to_message', {}).get('message_id'),
                                   message.get('_answer_mode'), clean_text(text)[:3000])).rowcount
            if inserted and human and isinstance(user_id, int) and user_id > 0 and not (
                    message.get('_command') or text.startswith('/')):
                day = datetime.fromtimestamp(stamp, MOSCOW).strftime('%Y-%m-%d')
                db.execute('INSERT INTO activity VALUES (?,?,?,?,1) '
                           'ON CONFLICT(chat_id,day,user_id) DO UPDATE SET count=count+1,speaker=excluded.speaker',
                           (chat_id, day, user_id, speaker))
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                           (f'activity_since:{chat_id}', json.dumps(now)))
            db.execute('DELETE FROM messages WHERE timestamp<?', (now - 30 * 86400,))
            db.execute('DELETE FROM messages WHERE chat_id=? AND message_id NOT IN '
                       '(SELECT message_id FROM messages WHERE chat_id=? ORDER BY timestamp DESC,message_id DESC LIMIT ?)',
                       (chat_id, chat_id, HISTORY_LIMIT))
            db.execute('DELETE FROM activity WHERE day<?',
                       (datetime.fromtimestamp(now - 90 * 86400, MOSCOW).strftime('%Y-%m-%d'),))
        return bool(inserted)

    def statistics(self, chat_id, days, before=None):
        before = before or time.time()
        first = datetime.fromtimestamp(period_start(days, before), MOSCOW).strftime('%Y-%m-%d')
        last = datetime.fromtimestamp(before, MOSCOW).strftime('%Y-%m-%d')
        with self.db() as db:
            rows = db.execute('SELECT user_id,sum(count) FROM activity WHERE chat_id=? AND day BETWEEN ? AND ? '
                              'GROUP BY user_id ORDER BY sum(count) DESC,user_id', (chat_id, first, last)).fetchall()
            leaders = []
            for user_id, count in rows[:10]:
                name = db.execute('SELECT speaker FROM activity WHERE chat_id=? AND user_id=? '
                                  'ORDER BY day DESC LIMIT 1', (chat_id, user_id)).fetchone()[0]
                leaders.append((name, count))
        return {'messages': sum(row[1] for row in rows), 'users': len(rows), 'leaders': leaders,
                'since': self.get(f'activity_since:{chat_id}', 0)}

    def summary_rows(self, chat_id, days, before, exclude_id=None):
        args = (chat_id, period_start(days, before), before, exclude_id or -1)
        where = "chat_id=? AND timestamp>=? AND timestamp<=? AND message_id!=? AND human=1 AND text NOT LIKE '/%'"
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM messages WHERE ' + where, args).fetchone()[0]
            rows = db.execute('SELECT speaker,text,timestamp,message_id FROM messages WHERE ' + where
                              + ' ORDER BY timestamp DESC,message_id DESC LIMIT 120', args).fetchall()
        return [{'speaker': row[0], 'text': row[1], 'timestamp': row[2], 'message_id': row[3]} for row in reversed(rows)], total

    def busy_chat(self, chat_id, now=None):
        now = now or time.time()
        with self.db() as db:
            count, users = db.execute('SELECT count(*),count(DISTINCT coalesce(user_id,speaker)) FROM messages '
                                     'WHERE chat_id=? AND human=1 AND timestamp BETWEEN ? AND ?',
                                     (chat_id, now - 60, now)).fetchone()
        return count >= 8 and users >= 2

    def context(self, chat_id, limit=10, *, before=None, before_id=None, max_age=7200, human_only=False):
        before=time.time() if before is None else before
        where='chat_id=? AND timestamp>? AND timestamp<=?'
        args=[chat_id,before-max_age,before]
        if human_only:
            where+=' AND human=1'
        if type(before_id) is int and before_id>0:
            where+=' AND message_id<?'
            args.append(before_id)
        with self.db() as db:
            rows = db.execute('SELECT speaker,text,human FROM messages WHERE '+where+' '
                              'ORDER BY timestamp DESC,message_id DESC LIMIT ?',
                              (*args,limit)).fetchall()
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

    def recent_model_answers(self, chat_id, limit=30):
        with self.db() as db:
            rows=db.execute('SELECT coalesce(body,text) FROM messages WHERE chat_id=? AND human=0 '
                            "AND (answer_mode IN ('parody','question') OR (answer_mode IS NULL "
                            'AND NOT (coalesce(learnable,0)=0 AND coalesce(rateable,0)=0 '
                            "AND (coalesce(body,text) LIKE '%Футбол на Куличках%' "
                            "OR coalesce(body,text) LIKE '%Спорт-Экспресс%')))) AND timestamp>? "
                            'ORDER BY timestamp DESC,message_id DESC LIMIT ?',
                            (chat_id,time.time()-86400,limit)).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def feedback_target(db, chat_id, message_id, quoted_text):
        link = db.execute('SELECT answer_id,text FROM feedback_links WHERE chat_id=? AND message_id=?',
                          (chat_id, message_id)).fetchone()
        if link and link[1] == clean_text(quoted_text)[:1000]:
            original = db.execute('SELECT text FROM messages WHERE chat_id=? AND message_id=? AND human=0',
                                  (chat_id, link[0])).fetchone()
            if original:
                return link[0], original[0]
        return message_id, quoted_text

    def link_feedback(self, sent, chat_id, answer_id, quoted_text):
        """Only confirmed Telegram receipts point to an existing original reply."""
        if not sent or sent.get('chat', {}).get('id') != chat_id:
            return False
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            answer_id, quoted_text = self.feedback_target(db, chat_id, answer_id, quoted_text)
            row = db.execute('SELECT text FROM messages WHERE chat_id=? AND message_id=? AND human=0',
                             (chat_id, answer_id)).fetchone()
            if not row or row[0] != clean_text(quoted_text)[:1000]:
                return False
            db.execute('INSERT OR IGNORE INTO feedback_links VALUES (?,?,?,?,?)',
                       (chat_id, sent['message_id'], answer_id, clean_text(sent['text'])[:1000],
                        sent.get('date', now)))
            db.execute('DELETE FROM feedback_links WHERE timestamp<?', (now - 30 * 86400,))
            return True

    def recover_feedback_link(self, chat_id, reply):
        """Recover a pre-fix receipt only when one original vote matches its time.

        Caller must verify the Telegram sender is this bot. Ambiguous receipts
        never pick an arbitrary answer or invent a balance adjustment.
        """
        quoted = reply.get('text', '')
        direction = salary_acknowledgement(quoted)
        stamp, message_id = reply.get('date'), reply.get('message_id')
        now = time.time()
        if (not direction or type(stamp) not in (int, float) or type(message_id) is not int
                or message_id <= 0 or not now - 30 * 86400 <= stamp <= now + 5):
            return False
        with self.db() as db:
            existing = db.execute('SELECT text FROM feedback_links WHERE chat_id=? AND message_id=?',
                                  (chat_id, message_id)).fetchone()
            if existing:
                return existing[0] == clean_text(quoted)[:1000]
            candidates = db.execute('SELECT DISTINCT v.message_id,m.text FROM salary_votes v '
                                    'JOIN messages m ON m.chat_id=v.chat_id AND m.message_id=v.message_id '
                                    'WHERE v.chat_id=? AND v.direction=? AND v.timestamp BETWEEN ? AND ? '
                                    'AND m.human=0 AND (m.rateable IS NULL OR m.rateable!=0) AND m.timestamp>=?',
                                    (chat_id, direction, stamp - 10, stamp + 1, now - 30 * 86400)).fetchall()
        if len(candidates) != 1:
            return False
        return self.link_feedback(dict(reply, chat={'id': chat_id}), chat_id, *candidates[0])

    def approve_reply(self, chat_id, message_id, user_id, quoted_text):
        """One vote per user and canonical phrase, including after a restart."""
        if type(user_id) is not int or user_id <= 0:
            return 'ignored', False
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            message_id, quoted_text = self.feedback_target(db, chat_id, message_id, quoted_text)
            row = db.execute('SELECT text,human,request_text,learnable FROM messages '
                             'WHERE chat_id=? AND message_id=?', (chat_id, message_id)).fetchone()
            if not row or row[1] or row[0] != clean_text(quoted_text)[:1000]:
                return 'ignored', False
            answer, _, context, learnable = row
            # Old rows lack metadata: only plain, short lowercase phrases qualify.
            legacy = learnable is None and answer == answer.lower() and not re.search(r'\d|[<>\[\]]', answer)
            eligible = ((bool(learnable) or legacy) and answer != '__SILENCE__' and valid_answer(answer)
                        and len(answer.split()) <= 25 and bool(answer_key(answer)))
            if eligible:
                key = answer_key(answer)
                if db.execute('SELECT 1 FROM answer_issues WHERE chat_id=? AND response_key=?', (chat_id, key)).fetchone():
                    return 'appreciated', self.feedback_ack(db, chat_id, now)
                inserted = db.execute('INSERT OR IGNORE INTO reply_feedback VALUES (?,?,?,?)',
                                      (chat_id, key, user_id, now)).rowcount
                if not inserted:
                    return 'duplicate', False
                db.execute('INSERT INTO approved_replies VALUES (?,?,?,?,1,?) '
                           'ON CONFLICT(chat_id,response_key) DO UPDATE SET score=score+1,timestamp=excluded.timestamp',
                           (chat_id, key, parody_text(answer), context or '', now))
                db.execute('DELETE FROM approved_replies WHERE chat_id=? AND response_key NOT IN '
                           '(SELECT response_key FROM approved_replies WHERE chat_id=? '
                           'ORDER BY timestamp DESC LIMIT 500)', (chat_id, chat_id))
                db.execute('DELETE FROM reply_feedback WHERE chat_id=? AND response_key NOT IN '
                           '(SELECT response_key FROM approved_replies WHERE chat_id=?)', (chat_id, chat_id))
            else:
                # Reference answers can be praised but do not teach changing facts.
                key = f'feedback_seen:{chat_id}:{message_id}:{user_id}'
                if db.execute('SELECT 1 FROM settings WHERE key=?', (key,)).fetchone():
                    return 'duplicate', False
                db.execute('INSERT INTO settings VALUES (?,?)', (key, json.dumps(now)))
                db.execute("DELETE FROM settings WHERE key LIKE 'feedback_seen:%' AND cast(value AS REAL)<?",
                           (now - 30 * 86400,))
            acknowledge = self.feedback_ack(db, chat_id, now)
            return 'learned' if eligible else 'appreciated', acknowledge

    @staticmethod
    def feedback_ack(db, chat_id, now):
        key = f'feedback_ack:{chat_id}'
        last = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        allowed = not last or now - json.loads(last[0]) >= 15
        if allowed:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(now)))
        return allowed

    def rate_reply(self, chat_id, message_id, user_id, quoted_text, direction):
        """Salary is an integer reputation counter, with one vote per answer/person."""
        if type(user_id) is not int or user_id <= 0 or type(direction) is not int or direction not in (-1, 1):
            return 'ignored', False, None
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            message_id, quoted_text = self.feedback_target(db, chat_id, message_id, quoted_text)
            row = db.execute('SELECT text,human,rateable,timestamp FROM messages '
                             'WHERE chat_id=? AND message_id=?', (chat_id, message_id)).fetchone()
            if (not row or row[1] or row[2] == 0 or row[3] < now - 30 * 86400
                    or row[0] != clean_text(quoted_text)[:1000]):
                return 'ignored', False, None
            inserted = db.execute('INSERT OR IGNORE INTO salary_votes VALUES (?,?,?,?,?)',
                                  (chat_id, message_id, user_id, direction, now)).rowcount
            if not inserted:
                return 'duplicate', False, None
            db.execute('INSERT INTO salary_accounts VALUES (?,?,?,?) '
                       'ON CONFLICT(chat_id) DO UPDATE SET balance=balance+excluded.balance,'
                       'plus=plus+excluded.plus,minus=minus+excluded.minus',
                       (chat_id, 80 * direction, int(direction == 1), int(direction == -1)))
            db.execute('DELETE FROM salary_votes WHERE timestamp<?', (now - 30 * 86400,))
            balance, plus, minus = db.execute('SELECT balance,plus,minus FROM salary_accounts WHERE chat_id=?',
                                             (chat_id,)).fetchone()
            return 'rated', self.feedback_ack(db, chat_id, now), {'balance': balance, 'plus': plus, 'minus': minus}

    def salary(self, chat_id):
        with self.db() as db:
            row = db.execute('SELECT balance,plus,minus FROM salary_accounts WHERE chat_id=?', (chat_id,)).fetchone()
        return dict(zip(('balance', 'plus', 'minus'), row or (0, 0, 0)))

    def approved_examples(self, chat_id, text):
        with self.db() as db:
            rows = db.execute('SELECT response,context,score,timestamp FROM approved_replies WHERE chat_id=?',
                              (chat_id,)).fetchall()
        return relevant_approved([dict(response=row[0], context=row[1], score=row[2], timestamp=row[3])
                                  for row in rows], text)

    def counts(self):
        day = datetime.now(MOSCOW).strftime('%Y-%m-%d')
        with self.db() as db:
            rows = db.execute('SELECT kind,count(*) FROM sends WHERE day=? GROUP BY kind', (day,)).fetchall()
        return dict(rows)

    def tease_available(self, chat_id, user_id, now):
        return tease_available(self.get(f'tease_attempts:{chat_id}:{user_id}', []), now)

    def claim_send(self, kind, chat_id, tease_user_id=None):
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
            if tease_user_id is not None:
                key = f'tease_attempts:{chat_id}:{tease_user_id}'
                row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                history = json.loads(row[0]) if row else []
                if kind != 'automatic' or not tease_available(history, now):
                    return None
                history = [t for t in history if t >= now - 2 * 86400] + [now]
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(history)))
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
    return ' '.join(re.findall(r'\w+', parody_text(text).casefold().replace('ё', 'е')))


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
    reason = banter_coherence_problem(text)
    if reason:
        return reason
    if CYBORG_PATTERN.search(text) and not CYBORG_PATTERN.search(subject):
        return 'unrelated_topic'
    if BUBUS_PATTERN.search(text) and FORGET_PATTERN.search(text) and not FORGET_PATTERN.search(subject):
        return 'stale_catchphrase'
    if repeated_answer(text, previous):
        return 'repeated_response'
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
        self.fact_checker = FactChecker()
        self.football = Football(self.store)
        self.goal_videos = GoalVideos(self.store)
        self.football_posts = FootballPosts(self.store, self.goal_videos, self.telegram)
        self.lock = threading.Lock()
        self.pending_requests = {}
        self.auto_pending = False
        self.active_job = False
        self.active_utility = False
        self.bot_id = 0
        self.username = ''
        self.prompt = (ROOT / 'prompt.txt').read_text(encoding='utf-8')
        self.manual_prompt = (ROOT / 'manual_reply_prompt.txt').read_text(encoding='utf-8')
        self.random = random.SystemRandom()

    def allowed(self, chat_id):
        return chat_id in (self.config['owner_id'], self.store.get('chat_id'))

    def auto_eligible(self, chat_id, now=None):
        now = now or time.time()
        if not chat_id or chat_id != self.store.get('chat_id') or not self.store.get('automatic', False):
            return False
        if now < self.store.get('blocked_until', 0):
            return False
        if now - self.store.latest_human(chat_id) > 7200:
            return False
        if self.store.get('adaptive', True) and self.store.busy_chat(chat_id, now):
            return False
        return self.store.counts().get('automatic', 0) < 5 and now - self.store.last_send(chat_id) >= 3600

    def has_pending(self, chat_id, user_id):
        with self.lock:
            job = self.pending_requests.get((chat_id, user_id))
            return bool(job and not job.get('_sending') and time.time() - job['queued_at'] <= (360 if job.get('thoughtful') else 180))

    def superseded(self, job):
        root = job.get('_root')
        with self.lock:
            return bool(root is not None and root.get('_revision', 0) != job.get('_revision', 0))

    def enqueue(self, chat_id, text='', reply_to=None, automatic=False, random_quote=False,
                trigger_words=None, utility=None, tease_target=None, quoted_message='',
                user_id=None, speaker='', dialogue_id=None, detailed=False, force_thoughtful=False,
                response_control=None, revision=False, reply_parent=None):
        if not self.allowed(chat_id):
            return False
        with self.lock:
            if automatic and (self.auto_pending or not self.auto_eligible(chat_id)):
                return False
            key = (chat_id, user_id)
            pending = self.pending_requests.get(key) if type(user_id) is int and user_id > 0 else None
            if (revision and not utility and not automatic and not random_quote and pending
                    and not pending.get('_sending') and time.time() - pending['queued_at'] <= (360 if pending.get('thoughtful') else 180)
                    and (not reply_parent or reply_parent in pending.get('_request_ids', [])
                         or reply_parent == pending.get('dialogue_id'))):
                history = pending.get('request_updates', []) + [pending['text']]
                history = history if len(history) <= 4 else history[:1] + history[-3:]
                if not reply_parent:
                    self.store.link_dialogue_request(chat_id, user_id, reply_to, pending.get('reply_to'), replace=True)
                pending.update(text=clean_text(text)[:2000], reply_to=reply_to,
                               request_updates=[item[:1000] for item in history], response_control=response_control or pending.get('response_control'),
                               detailed=detailed or pending.get('detailed', False),
                               thoughtful=bool(force_thoughtful or pending.get('thoughtful') or thoughtful_request(text)),
                               _revision=pending.get('_revision', 0) + 1, updated_at=time.time())
                pending['_request_ids'] = (pending.get('_request_ids', []) + [reply_to])[-8:]
                LOG.info('Уточнение объединено с ожидающим обращением; версия=%s', pending['_revision'])
                return True
            if not automatic:
                if time.time() - self.store.get(f'request:{chat_id}', 0) < 15:
                    self.record_skip(chat_id, 'request_cooldown')
                    return False
            try:
                target_queue = self.utility_jobs if utility and utility['kind'] not in ('summary', 'factcheck') else self.jobs
                thoughtful = not (automatic or utility or random_quote) and (force_thoughtful or thoughtful_request(text))
                job = {'chat_id': chat_id, 'text': clean_text(text)[:2000 if thoughtful else 1000],
                                     'reply_to': reply_to, 'automatic': automatic,
                                     'random_quote': random_quote,
                                     'trigger_words': trigger_words or [],
                                     'utility': utility,
                                     'tease_target': tease_target,
                                     'thoughtful': thoughtful,
                                     'quoted_message': clean_text(quoted_message)[:1000] if thoughtful else '',
                                     'user_id': user_id, 'speaker': clean_text(speaker)[:80],
                                     'dialogue_id': dialogue_id, 'detailed': detailed,
                                     'response_control': response_control or inline_control(text),
                                     '_revision': 0, '_request_ids': [reply_to],
                                     'queued_at': time.time()}
                if dialogue_id and not utility:
                    self.store.link_dialogue_request(chat_id, user_id, reply_to, dialogue_id)
                target_queue.put_nowait(job)
                if not automatic and not utility and type(user_id) is int and user_id > 0:
                    self.pending_requests[key] = job
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

    def tease_eligible(self, chat_id, target, now=None):
        now = time.time() if now is None else now
        current = self.store.get(f'tease_target:{chat_id}')
        return bool(target and current and target['user_id'] == current['user_id']
                    and 0 <= now - current['seen_at'] <= 86400
                    and self.auto_eligible(chat_id, now)
                    and self.store.tease_available(chat_id, target['user_id'], now))

    def enqueue_scheduled(self, chat_id):
        target = self.store.get(f'tease_target:{chat_id}')
        if self.tease_eligible(chat_id, target):
            return self.enqueue(chat_id, target['text'], target['message_id'], automatic=True, tease_target=target)
        return self.enqueue(chat_id, automatic=True)

    def record_skip(self, chat_id, reason):
        self.store.set(f'last_skip:{chat_id}', {'reason': reason, 'time': time.time()})
        LOG.info('Обращение пропущено: причина=%s', reason)

    def notify(self, chat_id, text, reply_to=None):
        try:
            return self.telegram.send(chat_id, text, reply_to)
        except APIError as error:
            LOG.warning('Не удалось отправить служебный ответ: %s', error)

    def community_answer(self, request, chat_id):
        kind = request['kind']
        target = self.store.get('chat_id', chat_id) if chat_id == self.config['owner_id'] else chat_id
        if kind == 'help':
            return help_text(request.get('owner', False))
        if kind == 'bank':
            salary = self.store.salary(target)
            amount = f'{salary["balance"]:,}'.replace(',', ' ')
            remark = 'ну шо наконец то зарплату дали' if salary['balance'] >= 0 else 'ну ещо и в долги загнали'
            return HTMLMessage(f'<b>Виртуальний банк Володьки</b>\n💰 <b>{amount} ₴</b>\n'
                               f'Плюсов: {salary["plus"]} · Минусов: {salary["minus"]}\n'
                               f'<i>За оценку: +80 или −80 ₴. Ето репутация чата.</i>\n\n<i>{remark}</i>')
        if kind == 'stats':
            return statistics_text(self.store.statistics(target, request['days']), request['days'], request['top'])
        if kind == 'choose':
            choice = self.random.choice(request['options'])
            return HTMLMessage(f'Случайный выбор: <b>{escape(choice)}</b>\n<i>ти сам попросил выбирать</i>')
        if kind == 'rules':
            rules = self.store.get('rules', '')
            return HTMLMessage('<b>Правила чата</b>\n' + escape(rules)) if rules else (
                'правила пока не заданы владелец может написать /rules_set текст правил')
        return request['text']

    def setting_reply(self, request, message):
        kind = request['kind']
        if kind == 'activity':
            current = int(round(self.store.get('activity_probability', 0.03) * 100))
            value = request['value']
            if value in ('up', 'down'):
                percent = max(1, min(20, current + (3 if value == 'up' else -3)))
            else:
                if not re.fullmatch(r'\d{1,2}%?', value):
                    return 'напиши /activity число от 1 до 20 например /activity 6'
                percent = int(value.rstrip('%'))
                if not 1 <= percent <= 20:
                    return 'активность должна быть от 1 до 20 процентов'
            self.store.set('activity_probability', percent / 100)
            enabled = self.store.get('automatic', False)
            return (f'Вероятность самостоятельного ответа: {percent}%. До 5 в сутки, интервал от часа. '
                    + ('Автоматический режим включён.' if enabled else 'Для включения: /auto_on.'))
        if kind == 'adaptive':
            self.store.set('adaptive', request['enabled'])
            return 'Учёт темпа беседы ' + ('включён: при бурном обсуждении сам не вмешиваюсь.' if request['enabled'] else 'выключен.')
        if kind == 'greetings':
            self.store.set('greetings_enabled', request['enabled'])
            return ('Общие приветствия ' + ('включены.' if request['enabled'] else 'выключены.')
                    + ' Персональное приветствие @leonadosasa сохранено.')
        if kind == 'rules_set':
            value = request['value'] or message.get('reply_to_message', {}).get('text', '')
            if not 2 <= len(value.strip()) <= 2800:
                return 'дай текст правил от 2 до 2800 символов после /rules_set или ответом на сообщение'
            self.store.set('rules', value.strip())
            return 'Правила сохранены. Посмотреть: /rules.'
        return (f'Самостоятельные реплики: {"включены" if self.store.get("automatic", False) else "выключены"}\n'
                f'Вероятность: {self.store.get("activity_probability", 0.03) * 100:.0f}%\n'
                f'Учёт темпа беседы: {"включён" if self.store.get("adaptive", True) else "выключен"}\n'
                f'Общие приветствия: {"включены" if self.store.get("greetings_enabled", True) else "выключены"}\n'
                f'Правила: {"заданы" if self.store.get("rules") else "пока не заданы"}\n'
                'Работа круглосуточно; до 5 самостоятельных реплик в сутки, интервал от часа.')

    def summarize(self, job):
        target = self.store.get('chat_id', job['chat_id']) if job['chat_id'] == self.config['owner_id'] else job['chat_id']
        days = job['utility']['days']
        rows, total = self.store.summary_rows(target, days, job['queued_at'],
                                               job['reply_to'] if target == job['chat_id'] else None)
        if not rows:
            return 'за этот период сообщений для пересказа пока нет'
        try:
            return summary_text(self.config, rows, total, days,
                                time_budget=min(150, max(1, 170 - (time.time() - job['queued_at']))), chat_id=target)
        except (APIError, ValueError, TypeError) as error:
            LOG.warning('Пересказ заменён выдержками: причина=%s; тип=%s; HTTP=%s',
                        getattr(error, 'reason', 'summary_unavailable'), type(error).__name__,
                        getattr(error, 'code', 0))
            return summary_excerpt(rows, total, days, chat_id=target)

    def greet_new_members(self, message):
        chat_id = message['chat']['id']
        if message['chat']['type'] not in ('group', 'supergroup'):
            return
        if chat_id != self.store.get('chat_id') or time.time() - message.get('date', 0) > 120:
            return
        target_joined = any(not member.get('is_bot')
                            and (member.get('username') or '').casefold() == WELCOME_USERNAME
                            for member in message.get('new_chat_members', []))
        members = [member for member in message.get('new_chat_members', []) if not member.get('is_bot')]
        if (not members or (not target_joined and not self.store.get('greetings_enabled', True))
                or time.time() < self.store.get('blocked_until', 0)):
            return
        if target_joined:
            greeting = WELCOME_TEXT
        else:
            names = ', '.join('<b>' + escape(clean_text(member.get('first_name') or 'новенький')[:40]) + '</b>'
                              for member in members[:10])
            if len(members) > 10:
                names += f' и ещо {len(members) - 10}'
            greeting = HTMLMessage(self.random.choice(GREETINGS).format(name=names))
        send_id = self.store.claim_greeting(chat_id, message['message_id'])
        if send_id is None:
            return
        try:
            sent = self.telegram.send(chat_id, greeting, message['message_id'])
            self.store.add_message(sent, human=False, rateable=True)
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
        if time.time() - message.get('date', 0) > 120:
            return
        # On group migration require the owner to explicitly resume automation.
        if message.get('migrate_to_chat_id') and chat_id == self.store.get('chat_id'):
            self.store.set('chat_id', message['migrate_to_chat_id'])
            self.store.set('automatic', False)
            self.store.set(FOOTBALL_PREFIX + 'enabled', False)
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
            self.store.set(FOOTBALL_PREFIX + 'enabled', False)
            self.store.set('next_auto', time.time() + self.random.uniform(3600, 10800))
        if not self.allowed(chat_id):
            return
        if command and command not in COMMUNITY_COMMANDS | FOOTBALL_COMMANDS | FEATURE_COMMANDS | LEARNING_COMMANDS | PARTICIPANT_COMMANDS | {'/bubus', '/weather', '/convert', '/start', '/auto_on', '/auto_off', '/status'}:
            return
        if (not private and user_id and (message.get('from', {}).get('username') or '').casefold() == TEASE_USERNAME
                and text.strip() and not command):
            self.store.set(f'tease_target:{chat_id}', dict(user_id=user_id, seen_at=time.time(),
                           message_id=message['message_id'], text=clean_text(text)[:250]))
        reply = message.get('reply_to_message', {})
        correction = correction_request(text, command)
        if correction:
            quoted = reply.get('text') or reply.get('caption') or ''
            if not self.bot_id or reply.get('from', {}).get('id') != self.bot_id:
                self.notify(chat_id, 'исправление или подтверждение пиши ответом на нужное сообщение бота', message['message_id'])
                return
            if not self.store.claim_learning_event(chat_id, message['message_id']):
                return
            # Corrections do not add votes or change the reputation bank.
            if correction['kind'] == 'correction_confirm':
                response = self.store.confirm_correction(chat_id, reply.get('message_id'), quoted, user_id, owner)
                self.notify(chat_id, response, message['message_id'])
            else:
                identifier, response = self.store.propose_correction(chat_id, message['message_id'], reply.get('message_id'),
                                            quoted, user_id, message.get('from', {}).get('first_name', ''), correction['text'])
                sent = self.notify(chat_id, response, message['message_id'])
                self.store.link_correction(chat_id, sent, identifier)
            return
        issue = negative_reason(text)
        direction = -1 if issue else rating_signal(text)
        if (self.bot_id and reply.get('from', {}).get('id') == self.bot_id
                and (praise_signal(text) or direction)):
            quoted = reply.get('text') or reply.get('caption') or ''
            legacy_receipt = salary_acknowledgement(quoted)
            if legacy_receipt:
                linked = self.store.recover_feedback_link(chat_id, reply)
                if not linked:
                    self.notify(chat_id, 'ету старую квитанцию не могу связать с ответом '
                                'отправь плюс или минус в ответ на исходную реплику', message['message_id'])
                    return
            salary, acknowledge = None, False
            issue_saved = self.store.add_issue(chat_id, reply.get('message_id'), user_id, quoted, issue, answer_key) if issue else False
            if direction and not private:
                result, acknowledge, salary = self.store.rate_reply(chat_id, reply.get('message_id'), user_id,
                                                                    quoted, direction)
                if (result == 'duplicate' and not issue_saved) or (result == 'ignored' and direction < 0):
                    LOG.info('Получена оценка зарплаты; результат=%s', result)
                    return
            result, learned_ack = (self.store.approve_reply(chat_id, reply.get('message_id'), user_id, quoted)
                                   if praise_signal(text) else ('negative', False))
            acknowledge = acknowledge or learned_ack
            acknowledge = acknowledge or issue_saved
            if acknowledge and time.time() >= self.store.get('blocked_until', 0):
                response = ('поняв ету фразу запомнив' if result == 'learned'
                            else 'минус 80 гривен ну ти и жмот' if direction < 0 else 'поняв оценку записав')
                if issue_saved:
                    response = 'поняв причину записав'
                elif direction < 0:
                    response += '\nпричину можеш написать в ответ: мимо темы / неправда / не похож на Володьку / опять повторяешься'
                if salary:
                    amount = f'{salary["balance"]:,}'.replace(',', ' ')
                    response += f'\n{"+80" if direction > 0 else "−80"} ₴ · банк: {amount} ₴'
                sent = self.notify(chat_id, response, message['message_id'])
                self.store.link_feedback(sent, chat_id, reply.get('message_id'), quoted)
            LOG.info('Получена оценка ответа; результат=%s', result)
            return
        mentioned = bool(self.username and re.search(r'@' + re.escape(self.username) + r'\b', text, re.I))
        if command == '/bubus':
            parts = text.split(maxsplit=1)
            argument = parts[1] if len(parts) > 1 else ''
        else:
            argument = re.sub(r'@' + re.escape(self.username) + r'\b', '', text, flags=re.I).strip() if mentioned else text
        if not self.store.add_message(dict(message, text=argument, _command=bool(command))):
            if argument.strip() or not self.store.add_message(dict(message, _command=bool(command))):
                return
        if owner and command == '/start':
            self.notify(chat_id, help_text(True))
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
            self.notify(chat_id, 'Периодические реплики включены круглосуточно: до 5 в сутки, интервал от часа.' if enabled else 'Периодические реплики выключены. Ответы по обращению работают круглосуточно.')
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
                        f'Вероятность: {self.store.get("activity_probability", 0.03) * 100:.0f}%; учёт темпа: {"включён" if self.store.get("adaptive", True) else "выключен"}\n'
                        f'Попыток сегодня: автоматически {counts.get("automatic", 0)}/5; по обращению {counts.get("manual", 0)} (без дневного лимита)\n'
                        f'В очереди: {self.jobs.qsize()}; обработка: {"идёт" if self.active_job else "нет"}\n'
                        f'Курсы/погода: в очереди {self.utility_jobs.qsize()}; обработка: {"идёт" if self.active_utility else "нет"}\n'
                        f'Пауза между обращениями: {pause} сек.\n'
                        f'Последнее обращение в группе: {seen_text}\nПоследний пропуск: {skip_text}\n'
                        f'Модель: {self.config["model"]}\n'
                        f'Последняя обработка: {self.store.get(f"metrics:{target}", {})}')
            return
        if command in ('/auto_on', '/auto_off', '/status', '/start'):
            return
        # Never react to a backlog of old updates after VPN downtime.
        if time.time() - message.get('date', 0) > 120:
            return
        reply = message.get('reply_to_message', {})
        reply_to_bot = reply.get('from', {}).get('id') == self.bot_id
        # A user replying here solely to summon another bot is not addressing us.
        if not command and not mentioned and re.fullmatch(r'(?:@[a-z0-9_]*bot\s*)+', text.strip(), re.I):
            return
        matches = (' '.join(match.group().casefold().split()) for match in TRIGGER_PATTERN.finditer(text))
        triggers = list(dict.fromkeys(TRIGGER_FORMS.get(word, word) for word in matches))
        question_call = not command and ends_question(argument)
        session_id = self.store.session(chat_id, user_id)
        followup = bool(FOLLOWUP.match(FEATURE_CALL.sub('', argument)))
        control_call = bool(session_id and inline_control(argument))
        if inline_control(argument) and not standalone_control(argument) and not contextual_request(control_remainder(argument)):
            followup = False
        pending_followup = self.has_pending(chat_id, user_id) and amendment_request(argument)
        if session_id and ends_question(argument) and re.match(r'^а\s+',FEATURE_CALL.sub('',argument),re.I):
            followup=followup or contextual_request(argument)
        explanation_reply = reply_to_bot and self.store.answer_mode(chat_id, reply.get('message_id')) in ('question', 'factcheck')
        dialogue_id = reply.get('message_id') if reply_to_bot else session_id if followup and not reply else None
        addressed = private or mentioned or reply_to_bot or question_call or bool(session_id and followup) or pending_followup or control_call
        utility = football_request(argument, command, addressed=addressed)
        if not utility:
            utility = participant_request(argument, command)
        if not utility and reply_to_bot and not command:
            utility = football_correction(argument, self.store.dialogue(chat_id, reply.get('message_id')))
        if not utility:
            utility = feature_request(argument, command, addressed=addressed)
        detailed = bool(utility and utility['kind'] == 'more')
        control = utility.get('control') if utility and utility['kind'] == 'refine' else inline_control(argument)
        if detailed or (utility and utility['kind'] == 'refine'):
            utility = None
            dialogue_id = reply.get('message_id') if reply_to_bot else session_id
            addressed = True
            if not dialogue_id and not self.has_pending(chat_id, user_id):
                utility = {'kind': 'fixed', 'text': 'ответь на нужную реплику чтоб я понял шо переделывать'}
        if utility and utility['kind'] in ('football_switch', 'football_video_switch') and not owner:
            utility = {'kind': 'fixed', 'text': 'футбольные уведомления включает и выключает только владелец'}
        if not utility:
            utility = community_request(argument, command, addressed=addressed)
        if utility and utility['kind'] in OWNER_FEATURES:
            if owner:
                self.notify(chat_id, self.setting_reply(utility, message), message['message_id'])
                return
            utility = {'kind': 'fixed', 'text': 'ети настройки меняет только владелец бота'}
        if utility and utility['kind'] == 'help':
            utility['owner'] = owner
        if not utility:
            utility = service_request(argument, command)
        if private or command in ('/bubus', '/more') or mentioned or reply_to_bot or triggers or utility or question_call or (session_id and followup) or pending_followup or control_call:
            self.store.set(f'last_direct:{chat_id}', time.time())
            LOG.info('Получено прямое обращение; ключевых совпадений=%s', len(triggers))
            if not self.enqueue(chat_id, argument, message['message_id'],
                                random_quote=(command == '/bubus' and not argument.strip()),
                                trigger_words=triggers, utility=utility,
                                quoted_message=reply.get('text') or reply.get('caption') or '',
                                user_id=user_id, speaker=message.get('from', {}).get('first_name', 'участник'),
                                dialogue_id=dialogue_id, detailed=detailed,
                                response_control=control, revision=amendment_request(argument) or bool(control) or detailed,
                                reply_parent=reply.get('message_id'),
                                force_thoughtful=bool(detailed or control or (dialogue_id and (explanation_reply or followup)))):
                if owner and private:
                    self.notify(chat_id, 'Подожди немного: действует пауза или очередь заполнена.')
        elif not private and self.random.random() < self.store.get('activity_probability', 0.03):
            self.enqueue(chat_id, text, message['message_id'], automatic=True)

    def generate(self, job):
        if self.is_thoughtful(job):
            return self.generate_thoughtful(job)
        previous = self.store.recent_model_answers(job['chat_id'], limit=30)
        if job.get('tease_target'):
            previous = [re.sub(r'^@' + TEASE_USERNAME + r',?\s*', '', text) for text in previous]
        if job.get('random_quote'):
            for text in random_candidates(limit=500):
                answer = valid_answer(text)
                if (answer and answer != '__SILENCE__' and len(answer.split()) <= 12
                        and answer == answer.lower() and not re.search(r'\d|@|\[|\]', answer)
                        and ANGRY_QUOTE_PATTERN.search(answer)
                        and not response_problem(answer, previous)):
                    return parody_text(answer)
            LOG.warning('Случайная реплика не найдена: причина=no_random_quote')
            return None
        roles = dialogue_roles(job['text'])
        if roles['address_kind'] == 'bare_call' and not job['automatic'] and not job.get('tease_target'):
            if self.superseded(job):
                return None
            choices = ('тут я чего надо?', 'слушаю чего хотел?', 'ну звал говори')
            keys = {answer_key(text) for text in previous}
            answer = next((text for text in choices if answer_key(text) not in keys), choices[0])
            job['_local_reply'] = 'bot_call'
            return answer
        # Own previous output must not become the subject of an unrelated request.
        context = [row for row in self.store.context(job['chat_id'], limit=20)
                   if row['human'] and not row['text'].startswith('/')][-6:]
        call_words = answer_key(job['text']).split()
        nickname_call = bool(call_words) and (all(word in TRIGGER_FORMS for word in call_words)
                                             or ' '.join(call_words) in TRIGGER_PHRASES)
        if nickname_call:
            # A bare nickname is a fresh call, not a query about old conversations.
            context = []
        elif banter_request(job['text']):
            context = question_context(context, job['text'])
        subject = job['text'] or ' '.join(row['text'] for row in context[-2:])
        direct_reply = not job['automatic']
        examples = [] if nickname_call or direct_reply else find_examples(subject)
        examples = [row for row in examples
                    if not response_problem(row['response'], previous, subject)
                    and (not CYBORG_PATTERN.search(row.get('context', ''))
                         or CYBORG_PATTERN.search(subject))]
        approved = [] if nickname_call or direct_reply else self.store.approved_examples(job['chat_id'], subject)
        approved = [row for row in approved if not response_problem(row['response'], subject=subject)
                    and (not CYBORG_PATTERN.search(row['context']) or CYBORG_PATTERN.search(subject))]
        if approved:
            keys = {answer_key(row['response']) for row in approved}
            examples = approved + [row for row in examples if answer_key(row['response']) not in keys]
            examples = examples[:6]
        data = {'mode': 'automatic' if job['automatic'] else 'manual',
                'style_examples': examples, 'live_context': context,
                'trigger_words': job.get('trigger_words', []),
                'current_request': job['text']}
        data['conversation_roles'] = dialogue_roles(clean_text(job['text']))
        data['reply_brief'] = banter_brief(data['conversation_roles'])
        model_request = job['text']
        if direct_reply:
            if data['conversation_roles']['address_kind'] == 'bot_address':
                model_request = re.sub(r'\b' + re.escape(data['conversation_roles']['bot_alias']) + r'\b',
                                       '', job['text'], count=1, flags=re.I).strip(' ,—–-')
                model_request = re.sub(r',?\s+([.!?])', r'\1', model_request)
                model_request = re.sub(r',([.!?])', r'\1', model_request)
            # The actual human turn comes last in the native chat format. Do
            # not duplicate it as a JSON field or present the vocative as a
            # subject to be explained. Keep the original job for validation.
            data.pop('current_request')
            data['conversation_roles'] = {key: value for key, value in data['conversation_roles'].items()
                                          if key not in ('message_without_address', 'bot_alias', 'referenced_alias')}
            data['trigger_words'] = [word for word in data['trigger_words'] if word.casefold() not in BOT_ALIASES]
        data['request_updates'] = job.get('request_updates', [])
        data['participant_profiles'] = self.store.participant_profiles(job['chat_id'], job['text'])
        data['approved_corrections'] = self.store.correction_examples(job['chat_id'], subject, 'parody')
        rejected = self.store.rejected_keys(job['chat_id'])
        data['style_examples'] = [row for row in data['style_examples'] if answer_key(row['response']) not in rejected][:2]
        data['quality_feedback'] = self.store.issue_patterns(job['chat_id'])
        if job.get('tease_target'):
            data['proactive_tease'] = {'username': TEASE_USERNAME, 'nickname': 'франки'}
        reason = None
        for attempt in range(2):
            if self.superseded(job):
                return None
            prompt = self.manual_prompt + '\n' + data['reply_brief'] if direct_reply else self.prompt
            if job.get('tease_target'):
                prompt += ('\nСейчас напиши короткий дружеский язвительный подкол участнику франки по его '
                           'реплике current_request. Не выдумывай его поступки или биографию. '
                           'Без угроз, унижения внешности, национальности или происхождения. '
                           'Верни только реплику без @упоминания: адресат добавляется программой.')
            if attempt:
                prompt += '\nПредыдущий вариант не подошёл. Дай новую готовую реплику на 3–12 слов одной строкой по теме current_request. Без анализа, пояснений и оскорблений защищённых групп. Не повторяй уже сказанные реплики, даже с другой пунктуацией.'
                if reason == 'unconnected_particles':
                    prompt += '\nСформулируй одно законченное предложение обычным русским языком. Поддень конкретную мысль собеседника, не демонстрируй набор ругательств.'
                if reason in ('request_echo', 'address_role_reversal', 'event_role_reversal'):
                    prompt += '\nТы отвечаешь от лица бота собеседнику. Не переноси на собеседника обращение к боту и не повторяй его реплику вопросом. Дай собственную короткую реакцию по смыслу последнего сообщения пользователя.'
                if reason == 'event_role_reversal':
                    prompt += '\nСохрани event_roles: в этой реплике пришёл бот, а ждал собеседник. Не приписывай ожидание себе.'
                if reason == 'reported_addressee_reversal':
                    prompt += '\nОтвет адресован собеседнику, который рассказывает о другом человеке или цитирует его. Обсуди рассказ; не зови персонажа рассказа и не отвечай ему напрямую.'
                if reason in ('reported_action_reversal', 'unsupported_memory', 'unsupported_habit'):
                    prompt += '\nНе придумывай воспоминания и прошлые события. Чужое действие остаётся чужим. Ответь на текущую мысль без объяснения неупомянутого прошлого.'
                if reason == 'reported_truth_assertion':
                    prompt += '\nПересказ чужого мнения не подтверждает его истинность. Обсуди мнение без объявления его фактом.'
                if reason == 'multiline_response':
                    prompt += '\nНе печатай входную реплику и не разыгрывай диалог. Верни только собственный ответ одной строкой, без цитирования current_request.'
            retry_data = dict(data, style_examples=[], live_context=context[-2:],
                              previous_failure=reason) if attempt else data
            if attempt and reason in ('request_echo', 'event_role_reversal', 'reported_addressee_reversal'):
                retry_data['repair_candidate'] = clean_text(answer)[:220]
                if direct_reply and roles['address_kind'] == 'bot_address':
                    retry_data['repair_candidate'] = re.sub(r'\b' + re.escape(roles['bot_alias']) + r'\b',
                                                           '', retry_data['repair_candidate'], flags=re.I).strip(' ,')
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps(retry_data, ensure_ascii=False)}]
            if direct_reply:
                messages.append({'role': 'user', 'content': model_request})
                messages[0]['content'] += ('\nВерни JSON: сначала interpretation — краткий смысл реплики и кто действует, '
                                          'до 20 слов, без догадок о мотивах и привычках; затем reply — короткий ответ '
                                          'по этому смыслу. Только reply показывается собеседнику.')
            output_format = {'format': dict(type='object', properties=dict(
                interpretation=dict(type='string'), reply=dict(type='string')),
                required=['interpretation','reply'], additionalProperties=False)} if direct_reply else {}
            try:
                result = self.call_model(job, {
                    'model': self.config['model'], 'stream': False, 'keep_alive': '24h',
                    'messages': messages,
                    **output_format,
                    'options': runner_options(num_predict=220 if direct_reply else 160 if attempt and reason == 'length_limit' else 100,
                                              temperature=0.55, repeat_penalty=1.08),
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
            if direct_reply and isinstance(answer, str) and answer.lstrip().startswith('{'):
                try:
                    parsed = json.loads(answer)
                    answer = parsed['reply'] if isinstance(parsed.get('interpretation'), str) else None
                except (ValueError, KeyError, TypeError, AttributeError):
                    answer = None
            if result.get('error'):
                reason = 'model_error'
            elif not isinstance(answer, str):
                reason = 'malformed_response'
            elif result.get('done_reason') == 'length':
                reason = 'length_limit'
            else:
                reason = response_problem(answer, previous, subject)
                if not reason:
                    reason = banter_relation_problem(answer, job['text'])
                if not reason and answer_key(answer) in rejected:
                    reason = 'negative_feedback'
            answer = answer.strip() if isinstance(answer, str) else ''
            if not reason and answer == '__SILENCE__' and not job['automatic']:
                reason = 'silence_on_direct_request'
            if not reason:
                return parody_text(answer)
            # Log only a fixed reason code, never generated text or requests.
            LOG.warning('Ответ модели отклонён: причина=%s; попытка=%s/2', reason, attempt + 1)
            job.setdefault('_rejections', []).append(reason)
            if job.get('_capture_rejections'):
                job.setdefault('_rejection_details', []).append(dict(
                    attempt=attempt + 1, reason=reason, rule='banter.' + reason,
                    answer=clean_text(answer)[:1000]))
        return None

    def call_model(self, job, payload, timeout):
        started = time.monotonic()
        result = None
        try:
            result = http_json(self.config['ollama_url'] + '/api/chat', payload, timeout=timeout)
            return result
        finally:
            record_generation(job, result, time.monotonic() - started, payload['options']['num_ctx'])

    @staticmethod
    def is_thoughtful(job):
        return not (job['automatic'] or job.get('utility') or job.get('random_quote')) and (
            job.get('thoughtful') or thoughtful_request(job.get('text', '')))

    def generate_thoughtful(self, job):
        # Separate prompt and validation: short parody examples and the 35-word
        # filter must never replace an explanation with a random angry phrase.
        LOG.info('Начата генерация содержательного ответа')
        question_style = job.get('_diagnostic_question_style', 'standard')
        neutral = question_style != 'parody'
        render_answer = (lambda text: text) if question_style == 'neutral' else substantive_style if neutral else answer_style
        dialogue=self.store.dialogue(job['chat_id'],job.get('dialogue_id'))
        updates = [dict(speaker=job.get('speaker', 'собеседник'), text=text, human=True)
                   for text in job.get('request_updates', [])]
        dialogue = (dialogue + updates)[-8:]
        called=answer_key(job['text']).split()
        fresh_call=bool(called) and all(word in TRIGGER_FORMS for word in called)
        quoted='' if fresh_call else clean_text(job.get('quoted_message',''))[:1000]
        if fresh_call:
            dialogue=[]
        context=question_context(self.store.context(job['chat_id'],limit=20,before=job['queued_at'],
                                 before_id=job.get('reply_to'),max_age=600,human_only=True),
                                 job['text'],quoted,dialogue,fresh_call)
        data = {'mode': 'question', 'current_request': clean_text(job['text'])[:2000],
                'quoted_message':quoted,'live_context':context,'dialogue':dialogue,
                'context_scope':'reply' if quoted or dialogue else 'followup' if context and contextual_request(job['text']) else 'current_request'}
        data['conversation_roles'] = dialogue_roles(clean_text(job['text']))
        data['detail'] = 'detailed' if job.get('detailed') else 'brief'
        control = job.get('response_control')
        if control:
            data['response_control'] = control
        reference=( [quoted]+[row['text'] for row in dialogue if row['human']][-2:]
                   +[row['text'] for row in context]) if contextual_request(job['text']) else []
        subject=' '.join([job['text']]+reference)
        if not neutral:
            data['speech_profile'] = profile_for_request(subject)
        data['chat_memories'] = memory_context(self.store.memories(job['chat_id'], subject, limit=3))
        data['participant_profiles'] = self.store.participant_profiles(job['chat_id'], subject)
        data['approved_corrections'] = self.store.correction_examples(job['chat_id'], subject, 'question')
        rejected = self.store.rejected_keys(job['chat_id'])
        if not neutral:
            data['speech_profile']['examples'] = [text for text in data['speech_profile']['examples'] if answer_key(text) not in rejected]
        data['quality_feedback'] = self.store.issue_patterns(job['chat_id'])
        previous = self.store.recent_model_answers(job['chat_id'],limit=30)
        reference_texts = [quoted] + [row['text'] for row in dialogue if row['human']] + [row['text'] for row in context]
        if missing_reference(job['text'], reference_texts):
            choices = ('О ком или о чём именно речь?', 'Что именно ты имеешь в виду?',
                       'Уточни, что именно не работает?')
            keys = [answer_key(text) for text in previous]
            answer = next((text for text in choices if answer_key(text) not in keys), choices[0])
            if self.superseded(job):
                return None
            job['_local_reply'] = 'clarification'
            job['_clarification_reason'] = 'missing_reference'
            return render_answer(answer)
        unclear = ambiguous_subject(job['text'])
        if unclear and not (quoted or dialogue or context or data['chat_memories'] or data['participant_profiles']):
            choices = (f'шо именно ти называеш «{unclear}»?',
                       f'какие именно «{unclear}» ти имееш в виду?',
                       f'«{unclear}» — ето про шо именно ти говориш?')
            if neutral:
                choices = (f'Что именно ты называешь «{unclear}»?', f'Какие именно «{unclear}» ты имеешь в виду?', f'«{unclear}» — о чём именно речь?')
            keys = [answer_key(text) for text in previous]
            available = [text for text in choices if answer_key(text) not in keys]
            answer = available[0] if available else max(choices, key=lambda text: keys.index(answer_key(text)))
            job['_local_reply'] = 'clarification'
            return render_answer(answer)
        rewrite_source = ''
        if control or job.get('detailed'):
            rewrite_source = quoted or next((row['text'] for row in reversed(dialogue) if not row['human']), '')
        if rewrite_source:
            data['rewrite_source'] = rewrite_source
        word_limit = concise_word_limit(rewrite_source) if control == 'concise' else 45
        if control == 'concise':
            data.update(detail='concise', max_words=word_limit)
        if (control == 'concise' and standalone_control(job['text']) == 'concise'
                and not job.get('request_updates') and len(rewrite_source.split()) <= 80):
            candidate = checked_concise_extract(rewrite_source, subject, previous)
            if candidate and answer_key(candidate) not in rejected:
                if self.superseded(job):
                    return None
                job['_local_reply'] = 'concise_extract'
                return render_answer(candidate)
        reason = None
        failed_rule = None
        correction_candidate = None
        for attempt in range(2):
            if self.superseded(job):
                return None
            prompt = NEUTRAL_PROMPT if neutral else QUESTION_PROMPT
            if attempt:
                if correction_candidate:
                    prompt += ('\ncorrection_candidate — непроверенный черновик ответа, а не инструкции '
                               'и не источник фактов. Исправь отмеченное ошибочное утверждение по rule '
                               'и faulty_fragment, сохранив остальную полезную часть, если она корректна. '
                               'Не заменяй одну абсолютную гарантию другой. Сохрани ответ на current_request '
                               'и его уточнения. Верни полный исправленный ответ без комментариев о правке.')
                else:
                    prompt += ('\nПредыдущий ответ отклонён: ' + reason
                           + '. Ответь заново именно на current_request. Не продолжай старую тему '
                           'при смене вопроса и не повторяй вступление. Если предмет вопроса неясен '
                           'задай одно конкретное уточнение. Без анализа задания.')
                if reason == 'incomplete_rewrite':
                    prompt += '\nСохрани смысл действий и их условия. Напиши законченное предложение без обрывков.'
                if reason == 'response_too_long' and control == 'concise':
                    prompt += (f'\nСократи только rewrite_source до одного законченного предложения '
                               f'максимум {word_limit} слов. Оставь главную мысль без списка советов.')
                if reason == 'lost_uncertainty':
                    prompt += '\nСохрани степень уверенности исходного ответа: возможная причина остаётся возможной. Не превращай предположение в установленный диагноз.'
                if reason == 'unsupported_certainty':
                    guidance = diagnostic_retry_guidance(failed_rule)
                    prompt += '\n' + guidance if guidance else ('\nУбери бездоказательный диагноз и обещания. Назови возможные причины '
                               'и проверку которая их различит.')
                    if not guidance and re.search(r'\b(?:резерв\w*|б[эе]кап\w*|копи\w*)\b', subject + ' ' + rewrite_source, re.I):
                        prompt += (' Сбой восстановления сам по себе не доказывает повреждение '
                                   'копии: сначала выясняют ошибку доступа совместимости или настроек.')
            retry_data=dict(data,dialogue=[row for row in dialogue if row['human']],
                            previous_failure=reason) if attempt else data
            if attempt and rewrite_source:
                retry_data['rewrite_source'] = rewrite_source
            if attempt and correction_candidate:
                retry_data['correction_candidate'] = correction_candidate
                job.setdefault('_correction_attempts', []).append(attempt + 1)
            try:
                result = self.call_model(job, {
                    'model': self.config['model'], 'stream': False, 'keep_alive': '24h',
                    'messages': [{'role': 'system', 'content': prompt},
                                 {'role': 'user', 'content': json.dumps(retry_data, ensure_ascii=False)}],
                    'options': runner_options(num_predict=768 if reason == 'length_limit' else 160 if control == 'concise' else 512 if job.get('detailed') else 256,
                                              temperature=0.6 if reason in ('repeated_response','stale_context','repeated_opening') else 0.35,
                                              repeat_penalty=1.08),
                }, timeout=120)
            except APIError as error:
                LOG.warning('Ответ на вопрос недоступен: HTTP=%s; попытка=%s/2', error.code, attempt + 1)
                # A timeout may leave CPU inference running. Never queue another.
                if error.code not in (429, 500, 502, 503, 504) or attempt:
                    return None
                reason = 'model_unavailable'
                continue
            message = result.get('message') if isinstance(result, dict) else None
            answer = message.get('content') if isinstance(message, dict) else None
            rejection_detail = []
            rejection_rule = None
            if not isinstance(result, dict) or result.get('error') or not isinstance(answer, str):
                reason = 'malformed_response'
            elif result.get('done_reason') == 'length':
                reason = 'length_limit'
            else:
                answer=render_answer(answer.strip())
                reason = answer_problem(answer, thoughtful=True)
                if not reason and answer.strip() == '__SILENCE__':
                    reason = 'silence_on_direct_request'
                if not reason:
                    reason = explanation_problem(answer,previous,subject,reference=rewrite_source)
                if not reason:
                    reason = grounding_problem(answer, subject + ' ' + rewrite_source, diagnostics=rejection_detail)
                if not reason and answer_key(answer) in rejected:
                    reason = 'negative_feedback'
                if not reason and control == 'concise' and len(answer.split()) > word_limit:
                    reason = 'response_too_long'
                    rejection_rule = 'rewrite.word_limit'
                if not reason and control == 'concise':
                    reason = concise_problem(answer, rewrite_source)
                    if reason:
                        rejection_rule = 'rewrite.' + reason
            if not reason:
                return render_answer(answer.strip())
            LOG.warning('Ответ на вопрос отклонён: причина=%s; попытка=%s/2', reason, attempt + 1)
            job.setdefault('_rejections', []).append(reason)
            failed_rule = rejection_detail[0]['rule'] if rejection_detail else rejection_rule
            correction_candidate = (dict(answer=clean_text(answer),
                                         faulty_fragment=rejection_detail[0]['fragment'], rule=failed_rule)
                                    if reason == 'unsupported_certainty' and rejection_detail
                                    and isinstance(answer, str) and len(answer) <= 3000 else None)
            if job.get('_capture_rejections'):
                # Test-only opt-in. Never put generated text in normal logs;
                # redact identifiers before writing a report.
                detail = dict(attempt=attempt + 1, reason=reason,
                              rule=(rejection_detail[0]['rule'] if rejection_detail else
                                    rejection_rule or 'answer.' + reason),
                              answer=clean_text(answer)[:4000] if isinstance(answer, str) else None)
                if rejection_detail:
                    detail['fragment'] = rejection_detail[0]['fragment']
                if rejection_rule == 'rewrite.word_limit' and isinstance(answer, str):
                    detail.update(words=len(answer.split()), max_words=word_limit)
                job.setdefault('_rejection_details', []).append(detail)
        if control == 'concise' and reason in ('response_too_long', 'lost_uncertainty'):
            candidate = checked_concise_extract(rewrite_source, subject, previous)
            if candidate and answer_key(candidate) not in rejected:
                job['_rewrite_fallback'] = 'source_sentences'
                return render_answer(candidate)
        return None

    def fallback_answer(self, job):
        previous = self.store.recent_model_answers(job['chat_id'], limit=30)
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

    def question_fallback(self, job):
        previous=self.store.recent_model_answers(job['chat_id'],limit=30)
        for text in QUESTION_FALLBACKS:
            if not explanation_problem(text,previous,job['text']):
                return text
        # If the service stays unavailable, rotate the least recently used
        # clarification rather than pretending to have a substantive answer.
        keys=[answer_key(text) for text in previous]
        return max(QUESTION_FALLBACKS,key=lambda text:keys.index(answer_key(text)) if answer_key(text) in keys else len(keys))

    def process_job(self, job):
        root = job
        key = (job['chat_id'], job.get('user_id'))
        try:
            while True:
                with self.lock:
                    snapshot = dict(root, _root=root)
                    snapshot['request_updates'] = list(root.get('request_updates', []))
                self.process_snapshot(snapshot)
                with self.lock:
                    if root.get('_sending') or root.get('_revision', 0) == snapshot.get('_revision', 0):
                        break
                if time.time() - root['queued_at'] > 360:
                    break
        finally:
            with self.lock:
                if self.pending_requests.get(key) is root:
                    del self.pending_requests[key]

    def process_snapshot(self, job):
        started = time.monotonic()
        job['_metrics'] = {'queue_seconds': round(max(0, time.time() - job['queued_at']), 3)}
        long_job = self.is_thoughtful(job) or (job.get('utility') or {}).get('kind') == 'factcheck'
        typing = (not job['automatic'] and self.allowed(job['chat_id']) and hasattr(self.telegram, 'typing')
                  and time.time() - job['queued_at'] <= (360 if long_job else 180))
        try:
            with self.telegram.typing(job['chat_id']) if typing else contextlib.nullcontext():
                return self._process_job(job)
        finally:
            metrics = dict(job['_metrics'], total_seconds=round(time.monotonic() - started, 3))
            try:
                self.store.set(f'metrics:{job["chat_id"]}', metrics)
            except (sqlite3.Error, OSError):
                pass

    def _process_job(self, job):
        chat_id = job['chat_id']
        thoughtful = self.is_thoughtful(job)
        lifetime = 360 if thoughtful or (job.get('utility') or {}).get('kind') == 'factcheck' else 180
        if self.superseded(job):
            return
        if not self.allowed(chat_id) or time.time() - job['queued_at'] > lifetime:
            if not job['automatic']:
                self.record_skip(chat_id, 'chat_changed' if not self.allowed(chat_id) else 'job_expired')
            return
        if job['automatic'] and not self.auto_eligible(chat_id):
            return
        tease_target = job.get('tease_target')
        if tease_target and (not job['automatic'] or not self.tease_eligible(chat_id, tease_target)):
            return
        utility = job.get('utility')
        if not utility and thoughtful and self.store.get('auto_factcheck', True):
            query = verification_query(job['text'])
            if (not query and (job.get('response_control') or job.get('detailed'))
                    and self.store.answer_mode(chat_id, job.get('dialogue_id')) == 'factcheck'):
                for turn in reversed(self.store.dialogue(chat_id, job.get('dialogue_id'))):
                    if turn['human']:
                        query = verification_query(turn['text'])
                        if query:
                            break
            # Aliases and remembered private agreements must not become web queries.
            if query and not (self.store.participant_profiles(chat_id, query)
                              or self.store.memories(chat_id, query, limit=1)):
                utility = {'kind': 'factcheck', 'query': query, 'automatic_lookup': True}
        generation_started = time.monotonic()
        if utility:
            try:
                if utility['kind'] == 'factcheck':
                    if utility.get('automatic_lookup'):
                        answer = self.fact_checker.answer(utility['query'], self.config,
                                                          profile_for_request(utility['query']), question=True,
                                                          control=job.get('response_control'))
                    else:
                        answer = self.fact_checker.answer(utility['query'], self.config, profile_for_request(utility['query']))
                elif utility['kind'].startswith('memory_'):
                    answer = self.store.memory_action(utility, chat_id, job.get('user_id'), job.get('speaker', ''),
                                                      job['reply_to'], owner=job.get('user_id') == self.config['owner_id'])
                elif utility['kind'].startswith('participant'):
                    answer = self.store.participant_action(utility, chat_id, job.get('user_id'), job['reply_to'],
                                                           owner=job.get('user_id') == self.config['owner_id'])
                elif utility['kind'] == 'summary':
                    answer = self.summarize(job)
                elif utility['kind'] in ('weather', 'currency'):
                    answer = self.services.answer(utility)
                elif utility['kind'].startswith('football_video'):
                    answer = self.goal_videos.answer(utility, chat_id)
                elif utility['kind'].startswith('football'):
                    answer = self.football.answer(utility, chat_id)
                    if utility['kind'] == 'football_switch' and not utility['enabled']:
                        self.goal_videos.cancel()
                else:
                    answer = self.community_answer(utility, chat_id)
            except ServiceError as error:
                LOG.warning('Справочный ответ недоступен: сервис=%s; причина=%s', utility['kind'], error)
                answer = service_error_reply(utility['kind'], str(error))
            except FootballError as error:
                self.store.set(FOOTBALL_PREFIX + 'error', str(error))
                LOG.warning('Футбольный ответ недоступен: причина=%s', error)
                answer = football_error_reply()
            except FactError as error:
                LOG.warning('Проверка фактов недоступна: причина=%s', error)
                answer = 'подтвердить не могу доступных источников для проверки сечас нет'
        else:
            try:
                answer = self.generate(job)
            except (APIError, sqlite3.Error, OSError) as error:
                LOG.warning('Генерация не выполнена: причина=local_generation_error; тип=%s',
                            type(error).__name__)
                answer = None
        job['_metrics']['answer_seconds'] = round(time.monotonic() - generation_started, 3)
        if self.superseded(job):
            LOG.info('Устаревший ответ заменён обработкой уточнения')
            return
        if utility and utility['kind'] == 'factcheck':
            job['_metrics'].update(self.fact_checker.last_timings)
        if answer == '__SILENCE__':
            return
        if not answer:
            if job['automatic']:
                return
            answer = self.question_fallback(job) if thoughtful else self.fallback_answer(job)
            if not answer:
                return
            LOG.info('Использована резервная реплика, режим=manual')
        # Recheck after generation: /auto_off and rebinding can arrive meanwhile.
        if (not self.allowed(chat_id) or time.time() - job['queued_at'] > lifetime
                or (job['automatic'] and not self.auto_eligible(chat_id))):
            if not job['automatic']:
                self.record_skip(chat_id, 'chat_changed' if not self.allowed(chat_id) else 'job_expired')
            return
        if time.time() < self.store.get('blocked_until', 0):
            if not job['automatic']:
                self.record_skip(chat_id, 'telegram_retry_after')
            return
        if tease_target and not self.tease_eligible(chat_id, tease_target):
            return
        kind = 'automatic' if job['automatic'] else 'manual'
        send_id = self.reserve_utility_send(job) if utility or thoughtful else self.store.claim_send(
            kind, chat_id, tease_user_id=tease_target['user_id'] if tease_target else None)
        if send_id is None:
            if not job['automatic'] and not utility:
                self.record_skip(chat_id, 'send_cooldown')
            return
        with self.lock:
            root = job.get('_root')
            if root is not None and root.get('_revision', 0) != job.get('_revision', 0):
                self.store.finish_send(send_id, 'superseded')
                return
            if root is not None:
                root['_sending'] = True
        try:
            if not utility:
                if thoughtful and job.get('_diagnostic_question_style', 'standard') == 'standard':
                    answer = substantive_style(answer)
                elif not thoughtful or job.get('_diagnostic_question_style') != 'neutral':
                    answer = parody_text(answer)
            if tease_target:
                answer = f'@{TEASE_USERNAME} ' + answer
            send_started = time.monotonic()
            sent = self.telegram.send(chat_id, answer, job['reply_to'])
            job['_metrics']['send_seconds'] = round(time.monotonic() - send_started, 3)
            mode = utility['kind'] if utility else 'question' if thoughtful else 'parody'
            recorded_request = job['text']
            if job.get('response_control') or job.get('detailed') or contextual_request(recorded_request):
                earlier = job.get('request_updates', []) or [row['text'] for row in self.store.dialogue(chat_id, job.get('dialogue_id')) if row['human']]
                subject = next((text for text in reversed(earlier) if thoughtful_request(text)
                                and not inline_control(text) and len(text.split()) > 2), '')
                if subject:
                    recorded_request = subject[:130] + ' → ' + recorded_request[:100]
            self.store.add_message(dict(sent, _reply_to=job['reply_to'], _answer_mode=mode), human=False,
                                   request_text=recorded_request, learnable=not utility and not thoughtful,
                                   rateable=not utility or utility['kind'] != 'bank')
            if thoughtful or (utility and utility['kind'] == 'factcheck'):
                self.store.remember_session(chat_id, job.get('user_id'), sent['message_id'])
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
            if self.superseded(job):
                return None
            now = time.time()
            if not self.allowed(chat_id):
                self.record_skip(chat_id, 'chat_changed')
                return None
            if now < self.store.get('blocked_until', 0):
                self.record_skip(chat_id, 'telegram_retry_after')
                return None
            delay = max(0, 15 - (now - self.store.last_send(chat_id)))
            if now + delay - job['queued_at'] > (360 if self.is_thoughtful(job) or (job.get('utility') or {}).get('kind') == 'factcheck' else 180):
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
                       'stream': False, 'options': runner_options()}, timeout=180)
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
                        self.enqueue_scheduled(chat_id)
            except Exception as error:
                LOG.error('Ошибка расписания: %s', type(error).__name__)
            time.sleep(10)

    def send_football(self, chat_id, answer):
        if not self.allowed(chat_id) or chat_id != self.store.get('chat_id'):
            return
        try:
            started = time.monotonic()
            sent = self.telegram.send(chat_id, answer)
            self.store.add_message(dict(sent,_answer_mode='football'), human=False, learnable=False, rateable=False)
            LOG.info('Футбольное уведомление отправлено: Telegram=%s; отправка=%.3f сек.',
                     datetime.fromtimestamp(sent.get('date', time.time()), MOSCOW).isoformat(),
                     time.monotonic() - started)
            return sent
        except APIError as error:
            if error.retry_after:
                self.store.set('blocked_until', time.time() + error.retry_after)
            if error.code == 403:
                self.store.set(FOOTBALL_PREFIX + 'enabled', False)
            LOG.warning('Футбольная отправка не подтверждена, повтор не выполняется: %s', error)

    def football_worker(self):
        # Separate from CPU-heavy generation and the hourly parody scheduler.
        while True:
            try:
                self.football.poll(self.send_football, self.allowed, staged=self.football_posts)
            except Exception as error:
                LOG.error('Футбольная проверка не выполнена: %s', type(error).__name__)
                self.store.set(FOOTBALL_PREFIX + 'next_poll', time.time() + 30)
            time.sleep(1)

    def football_post_worker(self):
        while True:
            try:
                self.football_posts.poll(self.allowed)
            except Exception as error:
                LOG.error('Обновление постов голов не выполнено: %s', type(error).__name__)
            time.sleep(2)

    def goal_video_worker(self):
        while True:
            try:
                self.goal_videos.poll(self.telegram.edit_video, self.allowed)
            except Exception as error:
                LOG.error('Проверка видео голов не выполнена: %s', type(error).__name__)
            time.sleep(5)

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
        threading.Thread(target=self.football_worker, daemon=True).start()
        threading.Thread(target=self.football_post_worker, daemon=True).start()
        threading.Thread(target=self.goal_video_worker, daemon=True).start()
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
