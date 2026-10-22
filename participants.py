"""Owner-confirmed aliases, scoped to a chat. Conversation never writes profiles."""
import json
import re
import time
from html import escape

from common import HTMLMessage, clean_text, visible_text

PARTICIPANT_COMMANDS = {'/person', '/person_set', '/person_forget'}


def participant_request(text, command=''):
    if command not in PARTICIPANT_COMMANDS:
        return None
    parts = text.split(maxsplit=1)
    return {'kind': 'participant' + command[7:], 'value': parts[1].strip() if len(parts) > 1 else ''}


def alias_matches(text, alias):
    return bool(re.search(r'(?<![\w@])' + re.escape(alias.casefold().lstrip('@')) + r'(?!\w)',
                          text.casefold().replace('@', '')))


class ParticipantStore:
    def init_participants(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS participant_profiles (
                    chat_id INTEGER,username TEXT,aliases TEXT,note TEXT,author_id INTEGER,updated REAL,
                    PRIMARY KEY(chat_id,username));
                CREATE TABLE IF NOT EXISTS participant_events (
                    chat_id INTEGER,message_id INTEGER,username TEXT,operation TEXT,author_id INTEGER,timestamp REAL,
                    PRIMARY KEY(chat_id,message_id));
            ''')

    def participant_profiles(self, chat_id, query='', limit=4):
        with self.db() as db:
            rows = db.execute('SELECT username,aliases,note,author_id,updated FROM participant_profiles '
                              'WHERE chat_id=? ORDER BY updated DESC LIMIT 100', (chat_id,)).fetchall()
        found = []
        for username, raw, note, author, updated in rows:
            aliases = json.loads(raw)
            if not query or any(alias_matches(query, alias) for alias in [username] + aliases):
                found.append(dict(username=username, aliases=aliases, note=note, confirmed_by=author, updated=updated))
        return found[:limit]

    def participant_action(self, request, chat_id, user_id, message_id, owner=False):
        kind, value = request['kind'], request['value']
        if kind == 'participant':
            rows = self.participant_profiles(chat_id, value, limit=10)
            if not rows:
                return 'такого прозвища в справочнике нет владелец может добавить через /person_set'
            blocks = ['<b>Кто есть кто</b>']
            for row in rows:
                block = '<b>@' + escape(row['username']) + '</b> · ' + escape(', '.join(row['aliases']))
                block += '\n' + escape(row['note']) if row['note'] else ''
                if len(visible_text(HTMLMessage('\n\n'.join(blocks + [block]))).encode('utf-16-le')) // 2 > 3500:
                    break
                blocks.append(block)
            return HTMLMessage('\n\n'.join(blocks))
        if not owner:
            return 'справочник участников меняет только хозяин бота'
        parts = [part.strip() for part in value.split('|')]
        username = parts[0].lstrip('@').casefold()
        if not re.fullmatch('[a-z0-9_]{5,32}', username):
            return 'пиши /person_set @username | прозвище, другое прозвище | подтверждённая заметка'
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM participant_events WHERE chat_id=? AND message_id=?', (chat_id, message_id)).fetchone():
                return 'ету правку справочника уже обработав'
            if kind == 'participant_forget':
                deleted = db.execute('DELETE FROM participant_profiles WHERE chat_id=? AND username=?', (chat_id, username)).rowcount
                if not deleted:
                    return 'такой записи нет'
            else:
                if len(parts) not in (2, 3):
                    return 'пиши /person_set @username | прозвище, другое прозвище | подтверждённая заметка'
                aliases = list(dict.fromkeys(part.strip().casefold() for part in parts[1].split(',') if part.strip()))
                note = parts[2] if len(parts) == 3 else ''
                if (not 1 <= len(aliases) <= 12 or any(not 3 <= len(alias) <= 40 or not re.fullmatch(r'@?[\w -]+', alias) for alias in aliases)
                        or len(note) > 300 or clean_text(note) != note):
                    return 'дай от 1 до 12 прозвищ от 3 до 40 символов и заметку до 300 символов'
                wanted = {username, *(alias.lstrip('@') for alias in aliases)}
                rows = db.execute('SELECT username,aliases FROM participant_profiles WHERE chat_id=?', (chat_id,)).fetchall()
                for other, raw in rows:
                    if other != username and wanted & {other, *(a.lstrip('@') for a in json.loads(raw))}:
                        return 'ето прозвище уже связано с другим участником сначала исправь его запись'
                if len(rows) >= 100 and all(other != username for other, _ in rows):
                    return 'в справочнике уже 100 участников удали старую запись'
                db.execute('INSERT OR REPLACE INTO participant_profiles VALUES (?,?,?,?,?,?)',
                           (chat_id, username, json.dumps(aliases, ensure_ascii=False), note, user_id, now))
            db.execute('INSERT INTO participant_events VALUES (?,?,?,?,?,?)', (chat_id, message_id, username, kind, user_id, now))
            db.execute('DELETE FROM participant_events WHERE timestamp<?', (now - 90 * 86400,))
        return 'удалив запись участника' if kind == 'participant_forget' else 'прозвища записав ето одна запись участника'
