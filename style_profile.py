"""Aggregate corpus style without treating old chat statements as facts."""
import json
import re
import statistics
from collections import Counter
from functools import lru_cache

from common import parody_text
from retrieval import SOURCE, normalize, STOP

SAFE = set(('а и в на не но ну у по за с от до про же что шо как или то ето это ти ты я он она они мы вы '
            'мне меня тебе тебя тут там так вот уже еще ещо хоть опять только токо сам все всё да нет '
            'почему зачем где когда кто чего че скоко сколько ест есть будет било было би бы сечас '
            'знаю знаеш знаю понял поняв хочеш можеш думаеш пишеш говориш делать сделал сделав '
            'поменяли надо нормально болья менья более менее интернет роутер провод кабель пиво '
            'интернета работает работало проверь включи выключи бля блядь сука нахуй хер заебал '
            'пристал хватит орать ореш начинаеш опять ето').split())


@lru_cache(maxsize=1)
def corpus_profile(signature):
    lengths, particles, examples, commas, seen = [], Counter(), [], 0, set()
    with SOURCE.open(encoding='utf-8') as file:
        for line in file:
            row = json.loads(line)
            raw = row.get('text', '')
            words = re.findall(r'[а-яёіїєґa-z]+', raw.casefold())
            lengths.append(len(words))
            commas += int(',' in raw)
            particles.update(w for w in words if w in {'шо', 'ето', 'ти', 'ещо', 'поняв', 'токо', 'скоко'})
            if (2 <= len(words) <= 12 and set(words) <= SAFE and raw == raw.lower()
                    and not re.search(r'\d|@|https?://', raw)):
                response = parody_text(raw)
                if response and response not in seen and len(response) <= 180 and len(examples) < 800:
                    examples.append(dict(response=response, context=row.get('context', '')[:180]))
                    seen.add(response)
    return dict(corpus_rows=len(lengths), median_words=statistics.median(lengths) if lengths else 0,
                comma_fraction=round(commas / max(1, len(lengths)), 3), particles=dict(particles)), examples


def profile_for_request(text):
    try:
        stat = SOURCE.stat()
        profile, examples = corpus_profile((str(SOURCE), stat.st_mtime_ns, stat.st_size))
    except (OSError, ValueError, TypeError):
        return {'spelling': 'ти ето шо ещо болья менья', 'examples': []}
    words = set(normalize(text).split()) - STOP
    ranked = sorted(examples, key=lambda row: len(words & set(normalize(row['context'] + ' ' + row['response']).split())), reverse=True)
    # No old speaker names or contexts enter the prompt, only screened utterances.
    return dict(profile, preferred='русские буквы почти без запятых без вежливости',
                examples=[row['response'] for row in ranked[:2]])
