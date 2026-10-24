"""Explicit, attributed corrections; never autonomous model training."""
import re
import time

from answer_quality import answer_problem, topics, words
from common import clean_text, parody_text

LEARNING_COMMANDS = {'/correct', '/confirm'}


def correction_request(text, command=''):
    if command == '/confirm' or re.fullmatch(r'подтверждаю[.! ]*', text.strip(), re.I):
        return {'kind': 'correction_confirm'}
    match = re.match(r'^(?:правильно\s+так|исправление|исправь\s+ответ)\s*:\s*(.+)$', text.strip(), re.I | re.S)
    if command == '/correct':
        parts = text.split(maxsplit=1)
        return {'kind': 'correction_add', 'text': parts[1] if len(parts) > 1 else ''}
    return {'kind': 'correction_add', 'text': match[1]} if match else None


class LearningStore:
    def init_learning(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS answer_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,message_id INTEGER,
                    source_id INTEGER,user_id INTEGER,author TEXT,request TEXT,corrected TEXT,
                    mode TEXT,reason TEXT,created REAL,confirmed REAL,
                    UNIQUE(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS correction_receipts (
                    chat_id INTEGER,message_id INTEGER,correction_id INTEGER,text TEXT,
                    PRIMARY KEY(chat_id,message_id));
                CREATE TABLE IF NOT EXISTS learning_events (
                    chat_id INTEGER,message_id INTEGER,timestamp REAL,PRIMARY KEY(chat_id,message_id));
            ''')

    def claim_learning_event(self, chat_id, message_id):
        with self.db() as db:
            db.execute('DELETE FROM learning_events WHERE timestamp<?', (time.time() - 30 * 86400,))
            return bool(db.execute('INSERT OR IGNORE INTO learning_events VALUES (?,?,?)',
                                   (chat_id, message_id, time.time())).rowcount)

    def propose_correction(self, chat_id, message_id, source_id, quoted, user_id, author, text):
        value = clean_text(text).strip()
        if type(user_id) is not int or user_id <= 0 or not 3 <= len(value) <= 1200 or value != text.strip():
            return None, 'дай исправленный ответ от 3 до 1200 символов без личных данных'
        if answer_problem(value, thoughtful=True) or value == '__SILENCE__':
            return None, 'етот вариант не годится дай законченный ответ без служебного текста'
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            source_id, quoted = self.feedback_target(db, chat_id, source_id, quoted)
            row = db.execute('SELECT text,request_text,answer_mode FROM messages '
                             'WHERE chat_id=? AND message_id=? AND human=0', (chat_id, source_id)).fetchone()
            if (not row or row[0] != clean_text(quoted)[:1000] or
                    row[2] not in ('question', 'parody') or not row[1]):
                return None, 'исправление пиши ответом на мою реплику или объяснение'
            now = time.time()
            db.execute('DELETE FROM answer_corrections WHERE created<?', (now - 90 * 86400,))
            reason = db.execute('SELECT reason FROM answer_issues WHERE chat_id=? AND message_id=? '
                                'ORDER BY timestamp DESC LIMIT 1', (chat_id, source_id)).fetchone()
            db.execute('INSERT OR IGNORE INTO answer_corrections '
                       '(chat_id,message_id,source_id,user_id,author,request,corrected,mode,reason,created) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (chat_id, message_id, source_id, user_id, author[:80], row[1], parody_text(value),
                        row[2], reason[0] if reason else 'correction', now))
            identifier = db.execute('SELECT id FROM answer_corrections WHERE chat_id=? AND message_id=?',
                                    (chat_id, message_id)).fetchone()[0]
            db.execute('DELETE FROM answer_corrections WHERE chat_id=? AND id NOT IN '
                       '(SELECT id FROM answer_corrections WHERE chat_id=? ORDER BY id DESC LIMIT 500)', (chat_id, chat_id))
            db.execute('DELETE FROM correction_receipts WHERE NOT EXISTS '
                       '(SELECT 1 FROM answer_corrections c WHERE c.id=correction_receipts.correction_id)')
        return identifier, ('вариант записав ответь «подтверждаю» на ето сообщение '
                            'если его и надо учитывать в таком же вопросе')

    def link_correction(self, chat_id, sent, identifier):
        if identifier and sent and type(sent.get('message_id')) is int:
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO correction_receipts VALUES (?,?,?,?)',
                           (chat_id, sent['message_id'], identifier, clean_text(sent.get('text', ''))[:1000]))

    def confirm_correction(self, chat_id, receipt_id, quoted, user_id, owner=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT c.id,c.user_id,c.confirmed,r.text,c.source_id,c.corrected FROM correction_receipts r '
                             'JOIN answer_corrections c ON c.id=r.correction_id AND c.chat_id=r.chat_id '
                             'WHERE r.chat_id=? AND r.message_id=? AND c.created>=?',
                             (chat_id, receipt_id, time.time() - 30 * 86400)).fetchone()
            if not row or row[3] != clean_text(quoted)[:1000]:
                return 'подтверждение пиши ответом на сообщение с записанным исправлением'
            if not owner and row[1] != user_id:
                return 'подтверждает автор исправления или хозяин бота'
            if row[2]:
                return 'ето исправление уже подтверждено'
            db.execute('UPDATE answer_corrections SET confirmed=? WHERE id=?', (time.time(), row[0]))
            # A corrected answer must no longer prime the old praised response.
            old = db.execute('SELECT text FROM messages WHERE chat_id=? AND message_id=?', (chat_id, row[4])).fetchone()
            if old:
                key = ' '.join(words(old[0]))
                db.execute('DELETE FROM approved_replies WHERE chat_id=? AND response_key=?', (chat_id, key))
                db.execute('INSERT OR IGNORE INTO answer_issues VALUES (?,?,?,?,?,?)',
                           (chat_id, row[4], user_id, 'incorrect', key, time.time()))
        return 'подтвердив буду учитывать етот вариант как поправку участника'

    def correction_examples(self, chat_id, query, mode, limit=2, user_id=None):
        wanted = topics(query)
        if not wanted:
            return []
        with self.db() as db:
            rows = db.execute('SELECT c.author,c.request,c.corrected,c.reason,c.confirmed,c.user_id,c.source_id,c.created,h.user_id '
                              'FROM answer_corrections c LEFT JOIN messages s ON s.chat_id=c.chat_id AND s.message_id=c.source_id '
                              'LEFT JOIN messages h ON h.chat_id=s.chat_id AND h.message_id=s.reply_to AND h.human=1 '
                              'WHERE c.chat_id=? AND c.mode=? AND c.confirmed>=? AND c.created>=? ORDER BY c.confirmed DESC LIMIT 100',
                              (chat_id, mode, time.time() - 30 * 86400, time.time() - 30 * 86400)).fetchall()
        selected, seen = [], set()
        for author, request, answer, reason, confirmed, correcting_user, source_id, created, requesting_user in rows:
            # Ordered words preserve negation, direction and conditions. Topic
            # overlap alone cannot distinguish opposite questions.
            if tuple(words(query)) != tuple(words(request)):
                continue
            if re.search(r'\b(?:сегодня|вчера|завтра|сейчас|сечас|текущ\w*|курс|цен[аыу]|стоимост\w*|стоит|стоят|рубл\w*|грив\w*|доллар\w*|евро|usd|eur|rub|uah)\b|[₽₴$€]', request+' '+answer, re.I):
                continue
            if user_id is not None and requesting_user is not None and requesting_user != user_id:
                continue
            personal = re.search(r'\b(?:мой|моя|мо[её]|мои|моего|моей|моему|мою|моим|моих|моими|мо[её]м|меня|мне|наш\w*|my|mine|our)\b',request,re.I)
            if personal and (user_id is None or requesting_user != user_id):
                continue
            identity = tuple(words(request))
            if identity in seen:
                continue
            seen.add(identity)
            subject = topics(request)
            shared = wanted & subject
            if shared and len(shared) / len(wanted) >= 0.6 and len(shared) / max(1, len(subject)) >= 0.5:
                selected.append(dict(author=author, request=request, response=answer, reason=reason,
                                     confirmed=confirmed, participant_claim=True, scope='same_request',
                                     correcting_user=correcting_user, source_message_id=source_id,
                                     created=created, expires=created+30*86400))
        return selected[:limit]

    def issue_patterns(self, chat_id):
        with self.db() as db:
            rows = db.execute('SELECT reason,count(*) FROM answer_issues WHERE chat_id=? AND timestamp>=? '
                              'GROUP BY reason ORDER BY count(*) DESC,max(timestamp) DESC LIMIT 4',
                              (chat_id, time.time() - 30 * 86400)).fetchall()
        return [dict(reason=reason, count=count) for reason, count in rows]
