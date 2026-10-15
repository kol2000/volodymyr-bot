"""Scoped dialogue, explicit chat memories and answer-quality feedback."""
import json
import re
import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from common import HTMLMessage, clean_text

MOSCOW = ZoneInfo('Europe/Moscow')
FEATURE_COMMANDS = {'/remember', '/memory', '/memory_edit', '/forget', '/check', '/more'}
CALL = re.compile(r'^\s*(?:бубус|бубуська|володька|володько)[\s,:!]+', re.I)
FOLLOWUP = re.compile(r'^(?:подробнее|разверни|продолжи|поясни|а почему|а как|а зачем|а когда|'
                        r'а если(?: наоборот)?|а наоборот|я про другое|нет я про|а что насч[её]т|'
                        r'ещ[еёо] пример|объясни проще)(?:\b|$)', re.I)
REASONS = {'мимо темы': 'off_topic', 'неправда': 'incorrect', 'не похож на володьку': 'style',
           'не похоже на володьку': 'style', 'опять повторяешься': 'repeat',
           'опять повторяешся': 'repeat', 'повтор': 'repeat'}


def negative_reason(text):
    body = CALL.sub('', text.strip()).casefold().strip(' .!?,')
    body = re.sub(r'^(?:[-−–]\s*|минус\s+)', '', body)
    return REASONS.get(body)


def feature_request(text, command='', addressed=False):
    if command and command not in FEATURE_COMMANDS:
        return None
    body = text.strip()
    called = CALL.match(body)
    if command:
        body = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ''
    elif called:
        body, addressed = body[called.end():], True
    if not (command or addressed):
        return None
    if command == '/check' or re.match(r'^(?:проверь|проверь-ка)\s+', body, re.I):
        query = body if command else re.sub(r'^проверь(?:-ка)?\s+', '', body, flags=re.I)
        return {'kind': 'factcheck', 'query': clean_text(query)[:300]}
    if command == '/more' or re.fullmatch(r'подробнее[.!?]*', body, re.I):
        return {'kind': 'more'}
    if command == '/remember' or re.match(r'^запомни\s+', body, re.I):
        value = body if command else re.sub(r'^запомни\s+(?:что\s+)?', '', body, flags=re.I)
        return {'kind': 'memory_add', 'text': value}
    if command == '/memory' or re.fullmatch(r'что (?:ти |ты )?помнишь[?!.]*|память[?!.]*', body, re.I):
        return {'kind': 'memory_list', 'query': body if command else ''}
    if command == '/memory_edit' or re.match(r'^исправь\s+запись\s+', body, re.I):
        value = body if command else re.sub(r'^исправь\s+запись\s+', '', body, flags=re.I)
        found = re.fullmatch(r'(\d+)\s*[: ]\s*(.+)', value, re.S)
        return {'kind': 'memory_edit', 'id': int(found[1]), 'text': found[2]} if found else {
            'kind': 'fixed', 'text': 'пиши /memory_edit номер новый текст'}
    if command == '/forget' or re.match(r'^забудь\s+(?:запись\s+)?', body, re.I):
        value = body if command else re.sub(r'^забудь\s+(?:запись\s+)?', '', body, flags=re.I)
        return {'kind': 'memory_delete', 'id': int(value)} if value.strip().isdigit() else {
            'kind': 'fixed', 'text': 'пиши /forget номер записи из /memory'}
    return None


def memory_words(text):
    return {w[:5] for w in re.findall(r'[а-яёa-z]{4,}', text.casefold())
            if w not in {'когда', 'договорились', 'помнишь', 'володька', 'бубус'}}


class FeatureStore:
    def init_features(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS dialogue_sessions (
                    chat_id INTEGER,user_id INTEGER,message_id INTEGER,timestamp REAL,
                    PRIMARY KEY(chat_id,user_id));
                CREATE TABLE IF NOT EXISTS chat_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,user_id INTEGER,
                    author TEXT,text TEXT,created REAL,updated REAL,deleted INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS memory_events (
                    chat_id INTEGER,message_id INTEGER,memory_id INTEGER,user_id INTEGER,
                    operation TEXT,text TEXT,timestamp REAL,PRIMARY KEY(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS answer_issues (
                    chat_id INTEGER,message_id INTEGER,user_id INTEGER,reason TEXT,
                    response_key TEXT,timestamp REAL,PRIMARY KEY(chat_id,message_id,user_id));
            ''')

    def session(self, chat_id, user_id, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            row = db.execute('SELECT message_id FROM dialogue_sessions WHERE chat_id=? AND user_id=? '
                             'AND timestamp>=?', (chat_id, user_id, now - 900)).fetchone()
        return row[0] if row else None

    def remember_session(self, chat_id, user_id, message_id):
        if type(user_id) is int and user_id > 0:
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO dialogue_sessions VALUES (?,?,?,?)',
                           (chat_id, user_id, message_id, time.time()))
                db.execute('DELETE FROM dialogue_sessions WHERE timestamp<?', (time.time() - 86400,))

    def dialogue(self, chat_id, message_id, limit=8):
        chain, seen, budget = [], set(), 4200
        with self.db() as db:
            while message_id and message_id not in seen and len(chain) < limit:
                seen.add(message_id)
                row = db.execute('SELECT speaker,coalesce(body,text),human,reply_to,request_text,answer_mode FROM messages '
                                 'WHERE chat_id=? AND message_id=? AND timestamp>=?',
                                 (chat_id, message_id, time.time() - 30 * 86400)).fetchone()
                if not row:
                    break
                speaker, text, human, parent, request, mode = row
                if len(text) > 1200:
                    text = text[:780] + ' [сокращено] ' + text[-400:]
                text = text[:budget]
                if not text:
                    break
                chain.append(dict(speaker=speaker, text=text, human=bool(human)))
                budget -= len(text)
                if not parent and not human and request and budget > 0:
                    chain.append(dict(speaker='собеседник', text=request[:min(600, budget)], human=True))
                    break
                message_id = parent
        return list(reversed(chain))

    def answer_mode(self, chat_id, message_id):
        with self.db() as db:
            row = db.execute('SELECT answer_mode FROM messages WHERE chat_id=? AND message_id=? AND human=0',
                             (chat_id, message_id)).fetchone()
        return row[0] if row else None

    def add_issue(self, chat_id, message_id, user_id, quoted, reason, response_key):
        if type(user_id) is not int or user_id <= 0 or reason not in set(REASONS.values()):
            return False
        with self.db() as db:
            message_id, quoted = self.feedback_target(db, chat_id, message_id, quoted)
            row = db.execute('SELECT text,rateable FROM messages WHERE chat_id=? AND message_id=? AND human=0',
                             (chat_id, message_id)).fetchone()
            if not row or row[1] == 0 or row[0] != clean_text(quoted)[:1000]:
                return False
            key = response_key(row[0])
            inserted = db.execute('INSERT OR IGNORE INTO answer_issues VALUES (?,?,?,?,?,?)',
                                 (chat_id, message_id, user_id, reason, key, time.time())).rowcount
            # A negative example must not keep teaching its former praise.
            if inserted:
                db.execute('DELETE FROM approved_replies WHERE chat_id=? AND response_key=?', (chat_id, key))
            db.execute('DELETE FROM answer_issues WHERE timestamp<?', (time.time() - 30 * 86400,))
        return bool(inserted)

    def issues(self, chat_id, limit=8):
        with self.db() as db:
            rows = db.execute('SELECT i.reason,m.request_text,m.text FROM answer_issues i JOIN messages m '
                              'ON m.chat_id=i.chat_id AND m.message_id=i.message_id WHERE i.chat_id=? '
                              'ORDER BY i.timestamp DESC LIMIT ?', (chat_id, limit)).fetchall()
        return [dict(reason=r, request=q[:180], response=a[:250]) for r, q, a in rows]

    def rejected_keys(self, chat_id):
        with self.db() as db:
            rows = db.execute('SELECT DISTINCT response_key FROM answer_issues WHERE chat_id=? AND timestamp>=? '
                              'ORDER BY timestamp DESC LIMIT 500', (chat_id, time.time() - 30 * 86400)).fetchall()
        return {row[0] for row in rows}

    def memories(self, chat_id, query='', limit=10):
        with self.db() as db:
            rows = db.execute('SELECT id,author,text,created,updated,user_id FROM chat_memories '
                              'WHERE chat_id=? AND deleted=0 ORDER BY updated DESC LIMIT 200', (chat_id,)).fetchall()
        data = [dict(zip(('id', 'author', 'text', 'created', 'updated', 'user_id'), row)) for row in rows]
        words = memory_words(query)
        if query:
            data = [row for row in data if words & memory_words(row['text'])]
            data.sort(key=lambda row: len(words & memory_words(row['text'])), reverse=True)
        return data[:limit]

    def memory_action(self, request, chat_id, user_id, author, message_id, owner=False):
        kind = request['kind']
        if kind == 'memory_list':
            rows = self.memories(chat_id, request.get('query', ''))
            lines = ['<b>Шо записав</b>']
            for row in rows:
                when = datetime.fromtimestamp(row['updated'], MOSCOW).strftime('%d.%m.%Y %H:%M МСК')
                line = f'<b>#{row["id"]}</b> {escape(row["text"])}\n<i>{escape(row["author"])} · {when}</i>'
                if len(('\n\n'.join(lines) + line).encode('utf-16-le')) // 2 > 3400:
                    break
                lines.append(line)
            return HTMLMessage('\n\n'.join(lines) if rows else 'пока ничего такого не записав')
        value = clean_text(request.get('text', '')).strip()
        if type(user_id) is not int or user_id <= 0 or (kind != 'memory_delete' and not 2 <= len(value) <= 500):
            return 'дай запись от 2 до 500 символов и пиши от своего аккаунта'
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT memory_id FROM memory_events WHERE chat_id=? AND message_id=?',
                                  (chat_id, message_id)).fetchone()
            if existing:
                return f'ету запись уже обработав номер {existing[0]}'
            identifier = request.get('id')
            if kind == 'memory_add':
                count = db.execute('SELECT count(*) FROM chat_memories WHERE chat_id=? AND deleted=0', (chat_id,)).fetchone()[0]
                if count >= 200:
                    return 'память забита удали старую запись через /forget'
                identifier = db.execute('INSERT INTO chat_memories(chat_id,user_id,author,text,created,updated) '
                                        'VALUES (?,?,?,?,?,?)', (chat_id, user_id, author[:80], value, now, now)).lastrowid
            else:
                row = db.execute('SELECT user_id FROM chat_memories WHERE chat_id=? AND id=? AND deleted=0',
                                 (chat_id, identifier)).fetchone()
                if not row:
                    return 'такой записи нет смотри /memory'
                if not owner and row[0] != user_id:
                    return 'чужую запись меняет ее автор или хозяин бота'
                db.execute('UPDATE chat_memories SET text=CASE WHEN ? THEN text ELSE ? END,updated=?,deleted=? '
                           'WHERE chat_id=? AND id=?',
                           (kind == 'memory_delete', value, now, int(kind == 'memory_delete'), chat_id, identifier))
            db.execute('INSERT INTO memory_events VALUES (?,?,?,?,?,?,?)',
                       (chat_id, message_id, identifier, user_id, kind, value, now))
        return f'забув запись {identifier}' if kind == 'memory_delete' else f'записав номер {identifier} смотри /memory'


def memory_context(rows):
    return [dict(id=r['id'], author=r['author'], recorded=datetime.fromtimestamp(r['updated'], MOSCOW).isoformat(),
                 text=r['text']) for r in rows]
