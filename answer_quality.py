"""Local repetition checks and topic selection; no extra model or network calls."""
import re
from difflib import SequenceMatcher

from common import parody_text

STOP = set(('а и в на не но ну по с со то это ето ты ти я он она они мы вы мне меня '
            'тебе тебя тут там так вот уже еще ещо что шо как да бля блять про кого '
            'где когда зачем почему кто какой какая какие сколько мой моя мое мои '
            'его ее их этот эта эти есть если речь именно реально сейчас сечас '
            'нужно надо можно будет было било бы би или для от до из у без при '
            'все всё только просто опять хочешь хочеш иди проверь сравни спроси').split())
REFERENCES = re.compile(r'\b(?:он|она|они|оно|его|е[её]|их|это|ето|этот|эта|эти|'
                        r'такой|такая|такие|так|там|столько)\b', re.I)
CALL = re.compile(r'^\s*(?:(?:@[a-z0-9_]+|бубус|бубуська|володька|володько)[\s,:!]+)+',re.I)


def words(text):
    return re.findall(r'\w+',parody_text(text).casefold().replace('ё','е'))


def topics(text):
    result=set()
    for word in words(text):
        if word in STOP or word.isdigit() or len(word)<3:
            continue
        # A bounded shared stem handles карточки/карточках, доставку/доставка.
        result.add(word[:5] if re.fullmatch('[а-я]+',word) else word)
    return result


def contextual_request(text):
    body=CALL.sub('',text.strip())
    return (len(words(body))<=1 or bool(REFERENCES.search(body)) or bool(re.fullmatch(
        r'(?:а\s+)?(?:почему|зачем|как|когда|сколько|кто|где)[?!\.\s]*',body,re.I)) or bool(re.match(
        r'^(?:подробнее|разверни|продолжи|объясни проще|я про другое|нет я про|'
        r'а (?:почему|как|если|зачем|когда|наоборот))\b',body,re.I)))


def question_context(rows,request,quoted='',dialogue=(),fresh_call=False):
    # Explicit reply chains already carry their own context. Ambient bot output
    # must never become evidence or drag a new question back to an old subject.
    if fresh_call or quoted or dialogue:
        return []
    human=[r for r in rows if r['human'] and not r['text'].startswith('/') and r['text']!=request]
    if not contextual_request(request):
        subject=topics(request)
        human=[r for r in human if topics(r['text']) & subject]
    selected,budget=[],1400
    for row in reversed(human[-6:]):
        cost=len(row['text'])
        if cost>budget:
            break
        selected.append(row)
        budget-=cost
    return list(reversed(selected))


def explanation_problem(text,previous,subject=''):
    candidate=words(text)
    # Repeated literal code/commands are often needed in a useful explanation;
    # compare prose for partial-copy checks, and the whole answer for exact ones.
    prose=lambda value:re.sub(r'```.*?```|`[^`]*`',' ',value,flags=re.S)
    current=words(prose(text))
    content=topics(prose(text))
    requested=topics(subject)
    opening=words(re.split(r'[.!?\n]',text,maxsplit=1)[0])
    repeated_openings=0
    for old in previous:
        if candidate==words(old):
            return 'repeated_response'
        other=words(prose(old))
        match=SequenceMatcher(None,current,other,autojunk=False)
        if min(len(current),len(other))>=8 and match.ratio()>=0.82:
            return 'repeated_response'
        old_content=topics(prose(old))
        shared=content & old_content
        if (min(len(content),len(old_content))>=6 and len(shared)>=6 and
                len(shared)/min(len(content),len(old_content))>=0.8 and len(shared)/len(content)>=0.7):
            return 'repeated_response'
        recycled=shared-requested
        if len(recycled)>=4 and len(recycled)/max(1,len(content))>=0.55:
            return 'stale_context'
        for block in match.get_matching_blocks():
            repeated=topics(' '.join(current[block.a:block.a+block.size]))
            if block.size>=6 and len(repeated-requested)>=3 and not repeated & requested:
                return 'stale_context'
        old_opening=words(re.split(r'[.!?\n]',old,maxsplit=1)[0])
        if 3<=len(opening)<=8 and opening==old_opening:
            repeated_openings+=1
    if repeated_openings>=2 and not topics(' '.join(opening)) & requested:
        return 'repeated_opening'
    return None
