"""Search the supplied corpus locally with SQLite FTS5."""
import json
import re
import sqlite3
from common import ROOT, STATE

INDEX = STATE / 'examples.sqlite3'
SOURCE = ROOT / 'data' / 'examples.jsonl'
STOP = set('а и в во на не но ну у по за с со к ко от до про же что шо как или то ето это ти ты я он она они мы вы мне меня тебе тебя тут там так вот уже ещё ещо'.split())
SPELLING = {'интернета': 'интернет', 'интернетом': 'интернет', 'сейчас': 'сечас',
            'еще': 'ещо', 'ещё': 'ещо', 'было': 'било', 'бы': 'би'}


def normalize(text):
    words = re.findall(r'[а-яёіїєґa-z]{2,}', text.lower())
    return ' '.join(SPELLING.get(word, word) for word in words)


def build_index():
    STATE.mkdir(exist_ok=True, mode=0o700)
    with sqlite3.connect(INDEX) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        signature = str(SOURCE.stat().st_mtime_ns) + ':' + str(SOURCE.stat().st_size)
        previous = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        if previous and previous[0] == signature:
            return db.execute('SELECT count(*) FROM examples').fetchone()[0]
        db.execute('DROP TABLE IF EXISTS examples')
        db.execute('CREATE VIRTUAL TABLE examples USING fts5(search, response UNINDEXED, context UNINDEXED, source_id UNINDEXED, tokenize="unicode61")')
        count = 0
        with SOURCE.open(encoding='utf-8') as file:
            for line in file:
                row = json.loads(line)
                db.execute('INSERT INTO examples VALUES (?,?,?,?)',
                           (normalize(row['context'] + ' ' + row['text']), row['text'],
                            row['context'], row['id']))
                count += 1
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('source',?)", (signature,))
    return count


def find_examples(text, limit=6):
    tokens = list(dict.fromkeys(word for word in normalize(text).split() if word not in STOP))[:12]
    selected = []
    if tokens:
        query = ' OR '.join('"' + word + '"' for word in tokens)
        with sqlite3.connect(INDEX) as db:
            rows = db.execute('SELECT response,context,source_id FROM examples WHERE examples MATCH ? ORDER BY bm25(examples) LIMIT 50', (query,)).fetchall()
        seen = set()
        # Prefer real response pairs to standalone phrases among relevant results.
        rows.sort(key=lambda row: not bool(row[1]))
        for response, context, source_id in rows:
            if response.lower() in seen:
                continue
            seen.add(response.lower())
            selected.append({'context': context[:180], 'response': response[:180]})
            if len(selected) == limit:
                break
    return selected


def random_candidates(limit=100):
    """Draw a small random batch; output validation remains in the bot."""
    with sqlite3.connect(INDEX) as db:
        rows = db.execute('SELECT response FROM examples ORDER BY random() LIMIT ?',
                          (limit,)).fetchall()
    return [row[0] for row in rows]
