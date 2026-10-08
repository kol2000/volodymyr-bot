"""Explicit praise and relevant approved examples, stored locally by the bot."""
import re

from community import NICKNAME
from retrieval import STOP, normalize

PRAISE = {'+', '++', '+1', 'плюс', 'правильно', 'верно', 'точно', 'молодец',
          'красавчик', 'браво', 'отлично', 'хороший ответ', 'хорошая фраза',
          'правильный ответ', 'так и надо', 'всё верно', 'все верно',
          'всё правильно', 'все правильно', 'вот ето правильно', 'вот это правильно',
          'спасибо', 'спасибо за ответ', 'запомни эту фразу', 'запомни ету фразу'}


def rating_signal(text):
    body = NICKNAME.sub('', text.strip(), count=1)
    body = ' '.join(body.casefold().strip(' \t\r\n.!?,').split())
    if body in ('+', '++', '+1', 'плюс') or re.fullmatch(r'👍[🏻🏼🏽🏾🏿]?', body):
        return 1
    if body in ('-', '−', '–', '—', '-1', '−1', 'минус') or re.fullmatch(r'👎[🏻🏼🏽🏾🏿]?', body):
        return -1
    return 0


def praise_signal(text):
    body = NICKNAME.sub('', text.strip(), count=1)
    body = ' '.join(body.casefold().strip(' \t\r\n.!?,').split())
    return body in PRAISE or bool(re.fullmatch(r'👍[🏻🏼🏽🏾🏿]?', body))


def relevant_approved(rows, text, limit=2):
    words = set(normalize(text).split()) - STOP
    if not words:
        return []
    ranked = []
    for row in rows:
        common = words & (set(normalize(row['context'] + ' ' + row['response']).split()) - STOP)
        if common:
            ranked.append((len(common), row['score'], row['timestamp'], row))
    ranked.sort(key=lambda item: item[:3], reverse=True)
    return [{'context': row['context'][:240], 'response': row['response'], 'approved': True}
            for _, _, _, row in ranked[:limit]]
