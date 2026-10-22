"""Local repetition checks and topic selection; no extra model or network calls."""
import re
from difflib import SequenceMatcher

from common import clean_text, parody_text


def answer_problem(text, thoughtful=False):
    text = text.strip()
    if text == '__SILENCE__':
        return None
    if len(text) < 2:
        return 'empty_response'
    too_long = (len(text) > 3000 or len(text.encode('utf-16-le')) // 2 > 3500) if thoughtful else (
        len(text) > 220 or len(text.split()) > 35)
    if too_long:
        return 'response_too_long'
    if '<think' in text.lower() or '</think' in text.lower():
        return 'reasoning_in_response'
    if thoughtful and text.count('```') % 2:
        return 'unfinished_code'
    if not thoughtful and len(text.splitlines()) > 1:
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


def specific_clarification(text):
    body = ' '.join(words(text))
    return bool(re.search(r'(?:что|шо)(?: именно)?(?: ти)? (?:называ\w*|име\w* в виду)|'
                          r'каки\w* именно|о чем (?:именно )?речь|про (?:что|шо|кого) (?:именно )?речь|'
                          r'про (?:что|шо|кого) именно (?:ти )?(?:говор\w*|спраш\w*)|'
                          r'каки\w* (?:ти )?име\w* в виду|уточни (?:предмет|что|шо|про)', body))


def concise_word_limit(source):
    return max(8, min(45, int(len(source.split()) * 0.7))) if source else 45


def grounding_problem(text, subject):
    """Reject narrow, unsupported diagnostic guarantees, not ordinary advice."""
    if not re.search(r'\b(?:сервер\w*|роутер\w*|wifi|вайфай|кабел\w*|соединени\w*|'
                     r'резерв\w*|б[эе]кап\w*|копи\w*)\b', subject, re.I):
        return None
    prose = re.sub(r'```.*?```|`[^`]*`|«[^»]*»|"[^"]*"', ' ', text, flags=re.S)
    prose = prose.casefold().replace('ё', 'е')
    backup = bool(re.search(r'\b(?:резерв\w*|б[эе]кап\w*|копи\w*)\b', subject + ' ' + prose, re.I))
    clauses = re.split(r'[.!?;\n]|\b(?:но|однако|зато)\b', prose)
    for index, clause in enumerate(clauses):
        if re.search(r'\b(?:не (?:факт|всегда|обязательно|гарантиру\w*|означает|доказ\w*)|'
                     r'нельзя (?:гарантировать|утверждать)|недостаточно|не доказывает)\b', clause):
            continue
        if re.search(r'\b(?:возможно|вероятно|может|могут|могла|могло|скорее|попробуй)\b', clause):
            continue
        log_reference = re.search(r'\b(?:там|логи|логах|логов|журнал\w*)\b', clause)
        exact_reason = re.search(r'\b(?:точн\w*.{0,16}причин\w*|причин\w*.{0,16}точн\w*)\b', clause)
        assertion = re.search(r'\b(?:будет|есть|найд\w*|укаж\w*|покаж\w*|содерж\w*|написан\w*|'
                              r'даст|дадут|видно|всегда|обязательно)\b', clause)
        if log_reference and exact_reason and assertion:
            return 'unsupported_certainty'
        # Restoration failure is not proof of corrupt media. Handle conclusions
        # with an implicit noun too: "ошибка — значит битая".
        evidence = ' '.join(clauses[max(0, index - 1):index + 1])
        failed_restore = re.search(r'\b(?:ошиб\w*|неудач\w*|не (?:получ\w*|восстан\w*|откр\w*))\b', evidence)
        verdict = re.search(r'\b(?:значит|следовательно|поэтому|выходит|тогда)\b'
                            r'(?:(?!\bне\b)[^.!?;\n]){0,40}\b'
                            r'(?:бит(?:ая|ый|ые|ое|ую|а|ы)?|поврежден\w*|испорчен\w*|сломан\w*)\b', clause)
        if backup and failed_restore and verdict:
            return 'unsupported_certainty'
        if re.search(r'\bтам точно (?:написано|видно|будет)|'
                     r'\b(?:логи|журнал\w*)[^.!?\n]{0,40}\b(?:точно|всегда)\b|'
                     r'\bвс[её] будет норм\w*|\bточно (?:решит|поможет)\b|'
                     r'\b(?:кабел\w*|провод\w*)[^.!?\n]{0,60}\bбез (?:любых )?потерь\b|'
                     r'\b(?:копия|б[эе]кап) (?:сломана?|испорчен\w*|битый)\b', clause):
            return 'unsupported_certainty'
    return None


def explanation_problem(text,previous,subject='',reference=''):
    candidate=words(text)
    # Repeated literal code/commands are often needed in a useful explanation;
    # compare prose for partial-copy checks, and the whole answer for exact ones.
    prose=lambda value:re.sub(r'```.*?```|`[^`]*`',' ',value,flags=re.S)
    current=words(prose(text))
    content=topics(prose(text))
    requested=topics(subject) | topics(prose(reference))
    opening=words(re.split(r'[.!?\n]',text,maxsplit=1)[0])
    repeated_openings=0
    for old in previous:
        if candidate==words(old):
            return 'repeated_response'
        # An explicit rewrite may reuse its source's ideas and wording. Other
        # answers still get full repeat checks; a verbatim source is rejected.
        if reference and words(old) == words(reference):
            continue
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
