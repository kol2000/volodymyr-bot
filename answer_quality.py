"""Local repetition checks and topic selection; no extra model or network calls."""
import re
from difflib import SequenceMatcher

from common import clean_text, parody_text
from conversation import dialogue_roles


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


def banter_coherence_problem(text):
    """Reject longer particle/insult piles, not a general grammar checker."""
    tokens = words(text)
    empty = set(('а и но ну же уж уже опять тут там ти ты я он она они мы вы это ето '
                 'шо що что че чо как где когда зачем почему ещё ещо еще бы би '
                 'был было било была были будет есть сечас сейчас то вот так да нет '
                 'бля блядь блять сука сученок сучонок хуйня лох дурак свиноголовый').split())
    if len(tokens) >= 5 and set(tokens) <= empty:
        return 'unconnected_particles'
    return None


def banter_relation_problem(text, request):
    roles = dialogue_roles(request)
    source = words(roles['message_without_address'])
    candidate = words(text)
    habit = re.search(r'\b(?:всегда|постоянно|как обычно)\b', text, re.I)
    if habit and habit[0].casefold() not in request.casefold() and not re.search(
            r'\b(?:не|если|разве)\s*(?:он\s+|ты\s+|ти\s+)?$', text[:habit.start()], re.I):
        return 'unsupported_habit'
    # Only explicit claims about remembered/datable events. This does not try to
    # classify every joke or reject ordinary hypothetical suggestions.
    if not re.search(r'\b(?:если|допустим|представь)\b', text, re.I):
        for marker in ('вчера', 'позавчера', 'на прошлой неделе'):
            if marker in text.casefold() and marker not in request.casefold() and re.search(
                    r'\b(?:был[аи]?|встречал\w*|видел\w*|ходил\w*|говорил\w*|писал\w*|обсуждал\w*)\b', text, re.I):
                return 'unsupported_memory'
        if re.search(r'\bя\s+(?:же\s+)?помню\b', text, re.I) and not re.search(r'помн|вспом', request, re.I):
            return 'unsupported_memory'
    if roles.get('reply_scope') in ('reported_speech', 'third_person'):
        if re.search(r'\b(?:значит\s+(?:точно\s+)?(?:правда|так и есть|он прав)|если[^.!?]{0,60}считает\s*[,—–-]\s*(?:значит\s+)?так и есть)\b', text, re.I):
            return 'reported_truth_assertion'
        action = r'(?:читал|написал|сказал|перепутал|вернулся|приш[её]л)[аи]?'
        if re.search(r'(?<!не )\bя\s+(?:(?:же|ведь|тогда|уже|опять|снова|вс[её]|это|ето)\s+){0,3}'+action+r'\b(?!\s+бы\b)', text, re.I):
            return 'reported_action_reversal'
        if re.search(r'\b(?:значит|так что)\s+(?:я\s+)?(?:читаю|пишу|говорю|спрашиваю|вернулся)\b',text,re.I):
            return 'reported_action_reversal'
    if roles.get('reply_scope') in ('reported_speech', 'third_person') and roles.get('referenced_alias'):
        alias = roles['referenced_alias']
        # A comma after a name also introduces parentheticals/apposition:
        # "Цацик, видимо, решил..." is a third-person statement, not a call.
        if re.match(r'^\s*(?:(?:ну|а|эй)\s+)?' + re.escape(alias) +
                    r'[\s,]+(?:а\s+)?(?:ты|ти|тебе|тебя|твой|твоя)\b', text, re.I):
            return 'reported_addressee_reversal'
    if roles['address_kind'] in ('bot_address', 'ambiguous'):
        alias = roles['bot_alias']
        # Returning the bot's own address as a vocative to the user swaps roles.
        if re.match(r'^\s*(?:(?:ну|а|эй)\s+)?' + re.escape(alias) + r'(?:\s*,|\s+(?:а\s+)?(?:ты|ти)\b)', text, re.I):
            return 'address_role_reversal'
        if re.match(r'^\s*' + re.escape(alias) + r'\s*[—–-]?\s*(?:это|ето)\s+(?:ты|ти)\b', text, re.I):
            return 'address_role_reversal'
        candidate = [word for word in candidate if word != alias]
    if roles.get('event_roles', {}).get('event') == 'arrival':
        for match in re.finditer(r'[^.!?]+[.!?]?', text):
            clause = match[0].casefold()
            waiting = re.search(r'\b(?:дождал(?:ся|ась)|ждал[аи]?)\b', clause)
            if waiting:
                prefix = clause[:waiting.start()]
                first_person = re.search(r'\bя\b', prefix)
                addressed_waiter = re.search(r'\b(?:ты|ти)\b', prefix) or re.search(r'\bменя\b', clause[waiting.end():])
                negated = re.search(r'\bне\s*$', prefix)
                if not negated and (first_person or (not addressed_waiter and not clause.rstrip().endswith('?'))):
                    return 'event_role_reversal'
            if re.search(r'\b(?:ты|ти)\b.{0,30}\b(?:явился|появился|приш[её]л|вернулся)\b', clause):
                return 'event_role_reversal'
    if len(source) >= 3 and len(candidate) >= 3:
        if SequenceMatcher(None, source, candidate, autojunk=False).ratio() >= 0.85:
            return 'request_echo'
    return None


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


UNCERTAIN = re.compile(r'\b(?:может|могут|могла|могло|возможно|вероятно|возможн\w*|'
                       r'иногда|часто|обычно|не всегда|не обязательно|не означает|не доказывает)\b', re.I)
CAUSAL = re.compile(r'\b(?:причин\w*|вызыва\w*|снижа\w*|из[ -]за|проблема в|дело в)\b', re.I)


def certainty_problem(text, source):
    """A causal rewrite must not promote a source hypothesis to a diagnosis.

    This is a bounded linguistic check, not a general semantic verifier. Match
    shared topics per clause so an unrelated 'может' cannot license a verdict.
    Advice-only extracts do not assert a cause and need no modal word.
    """
    prose = lambda value: re.sub(r'```.*?```|`[^`]*`|«[^»]*»|"[^"]*"', ' ', value, flags=re.S)
    split = lambda value: re.split(r'[.!?;\n]|\b(?:но|однако|зато)\b', prose(value))
    hypotheses = [topics(clause) for clause in split(source)
                  if UNCERTAIN.search(clause) and CAUSAL.search(clause)]
    for clause in split(text):
        if (CAUSAL.search(clause) and not UNCERTAIN.search(clause)
                and not re.match(r'^\s*(?:проверь|сравни|провер\w*|сравн\w*)\b', clause, re.I)
                and any(topics(clause) & subject for subject in hypotheses)):
            return 'lost_uncertainty'
    return None


def concise_problem(text, source):
    """Catch a known lost-action rewrite, not general Russian grammar."""
    if (source and re.search(r'\bсравн\w*\b', source, re.I)
            and re.search(r'\bпроверь\s+(?:кабель|роутер|провод)\s+и\s+'
                          r'(?:рядом|возле|около|по кабелю)\b', text, re.I)):
        return 'incomplete_rewrite'
    return certainty_problem(text, source)


def concise_extract(source):
    """Keep complete leading sentences; never cut words or invent a rewrite."""
    if not source or any(mark in source for mark in ('`', '\n', '«', '»', '"')):
        return None
    limit = concise_word_limit(source)
    selected = []
    for sentence in re.split(r'(?<=[.!?])\s+', source.strip()):
        if not re.search(r'[.!?]$', sentence) or len((' '.join(selected + [sentence])).split()) > limit:
            break
        selected.append(sentence)
    candidate = ' '.join(selected)
    return candidate if candidate and len(candidate.split()) < len(source.split()) else None


def grounding_problem(text, subject, diagnostics=None):
    """Reject narrow, unsupported diagnostic guarantees, not ordinary advice."""
    def reject(reason, rule, fragment):
        if diagnostics is not None:
            diagnostics.append(dict(rule=rule, fragment=clean_text(fragment.strip())[:1000]))
        return reason
    if not re.search(r'\b(?:сервер\w*|роутер\w*|wifi|вайфай|кабел\w*|соединени\w*|'
                     r'резерв\w*|б[эе]кап\w*|копи\w*)\b', subject, re.I):
        return None
    prose = re.sub(r'```.*?```|`[^`]*`|«[^»]*»|"[^"]*"', ' ', text, flags=re.S)
    prose = prose.casefold().replace('ё', 'е')
    if re.search(r'\bпроверь\s+(?:кабель|роутер|провод)\s+и\s+'
                 r'(?:рядом|возле|около|по кабелю)\b', prose):
        return reject('incomplete_rewrite', 'grounding.incomplete_action', prose)
    for clause in re.split(r'[.!?;\n]|\b(?:но|однако|зато)\b', prose):
        if (re.search(r'\b(?:кабел\w*|провод\w*)\b', clause)
                and re.search(r'\bбез (?:любых |каких-либо )?внешних влияний\b', clause)
                and not re.search(r'\b(?:не гарантиру\w*|не означает|не доказывает|нельзя утверждать)\b', clause)):
            return reject('unsupported_certainty', 'grounding.cable_external_immunity', clause)
    backup = bool(re.search(r'\b(?:резерв\w*|б[эе]кап\w*|копи\w*)\b', subject + ' ' + prose, re.I))
    clauses = re.split(r'[.!?;\n]|\b(?:но|однако|зато)\b', prose)
    for index, clause in enumerate(clauses):
        condition = re.search(r'\bесли\b.{0,70}\b(?:скорость|сигнал|по кабелю|wifi)\b'
                              r'.{0,35}?(?:выраст\w*|увелич\w*|улучш\w*|быстрее|пропад\w*|'
                              r'появ\w*|стабильн\w*)\b(?P<conclusion>.*)', clause)
        if not condition and re.search(r'\b(?:wifi|вайфай|кабел\w*|роутер\w*|соединени\w*|скорост\w*)\b', subject + ' ' + clause, re.I):
            condition = re.search(r'\bесли\s+разница\s+(?:есть|заметна)\b(?P<conclusion>.*)', clause)
        if condition:
            conclusion = condition['conclusion']
            if (re.search(r'\b(?:виноват\w*|проблема в|дело в|причина в|сбой в)\b', conclusion)
                    and not UNCERTAIN.search(conclusion)
                    and not re.search(r'\b(?:проверь|проверить|не факт|нельзя утверждать)\b', conclusion)):
                return reject('unsupported_certainty', 'grounding.network_comparison_verdict', clause)
        if re.search(r'\b(?:не (?:факт|всегда|обязательно|гарантиру\w*|означает|доказ\w*)|'
                     r'нельзя (?:гарантировать|утверждать)|недостаточно|не доказывает)\b', clause):
            continue
        if re.search(r'\b(?:возможно|вероятно|может|могут|могла|могло|скорее|попробуй)\b', clause):
            continue
        if re.search(r'\bесли\b.{0,90}\b(?:скорость|сигнал)\b.{0,90}\bвиноват\w*\b', clause):
            return reject('unsupported_certainty', 'grounding.network_culprit_after_condition', clause)
        log_reference = re.search(r'\b(?:там|логи|логах|логов|журнал\w*)\b', clause)
        exact_reason = re.search(r'\b(?:точн\w*.{0,16}причин\w*|причин\w*.{0,16}точн\w*)\b', clause)
        assertion = re.search(r'\b(?:будет|есть|найд\w*|укаж\w*|покаж\w*|содерж\w*|написан\w*|'
                              r'даст|дадут|видно|всегда|обязательно)\b', clause)
        if log_reference and exact_reason and assertion:
            return reject('unsupported_certainty', 'grounding.logs_guarantee', clause)
        # Restoration failure is not proof of corrupt media. Handle conclusions
        # with an implicit noun too: "ошибка — значит битая".
        evidence = ' '.join(clauses[max(0, index - 1):index + 1])
        failed_restore = re.search(r'\b(?:ошиб\w*|неудач\w*|не (?:получ\w*|восстан\w*|откр\w*|чита\w*|работа\w*))\b', evidence)
        # "Если нет — битая" refers to the preceding successful check. A
        # connective such as "значит" is optional in this conditional verdict.
        negative_condition = re.search(r'\b(?:если нет|иначе|в противном случае)\b', clause)
        if negative_condition and re.search(r'\b(?:восстан\w*|чита\w*|работа\w*|проверк\w*|ошиб\w*|копи\w*)\b', prose):
            failed_restore = True
        corruption = r'(?:бит(?:ая|ый|ые|ое|ую|а|ы)?|поврежден\w*|испорчен\w*|сломан\w*)\b'
        verdict = re.search(r'\b(?:значит|следовательно|поэтому|выходит|тогда)\b'
                            r'(?:(?!\bне\b)[^.!?;\n]){0,40}\b' + corruption, clause)
        if not verdict and (failed_restore or negative_condition):
            verdict = re.search(r'(?:[—–-]|\bто\b|\bиначе\b|\bв противном случае\b)\s*'
                                r'(?:(?:копия|б[эе]кап|она|он)\s+)?(?:либо\s+|или\s+)?' + corruption, clause)
        if backup and failed_restore and verdict:
            return reject('unsupported_certainty', 'grounding.restore_failure_verdict', clause)
        if re.search(r'\bтам точно (?:написано|видно|будет)|'
                     r'\b(?:логи|журнал\w*)[^.!?\n]{0,40}\b(?:точно|всегда)\b|'
                     r'\bвс[её] будет норм\w*|\bточно (?:решит|поможет)\b|'
                     r'\b(?:кабел\w*|провод\w*)[^.!?\n]{0,60}\bбез (?:любых )?потерь\b|'
                     r'\b(?:копия|б[эе]кап) (?:сломана?|испорчен\w*|битый)\b', clause):
            rule = 'grounding.cable_no_loss' if re.search(r'\b(?:кабел\w*|провод\w*).*\bбез (?:любых )?потерь\b', clause) else 'grounding.absolute_guarantee'
            return reject('unsupported_certainty', rule, clause)
    return None


def checked_concise_extract(source, subject, previous=()):
    candidate = concise_extract(source)
    if (candidate and not answer_problem(candidate, thoughtful=True)
            and not explanation_problem(candidate, previous, subject, reference=source)
            and not grounding_problem(candidate, subject + ' ' + source)
            and not concise_problem(candidate, source)):
        return candidate
    return None


def diagnostic_retry_guidance(rule):
    if rule == 'grounding.cable_external_immunity':
        return ('Убери абсолютную гарантию защищённости кабеля от внешних факторов. '
                'Объясни возможные различия подключений без обещаний полной невосприимчивости.')
    if rule == 'grounding.cable_no_loss':
        return ('Убери гарантию передачи без потерь. Объясни различия WiFi и кабеля '
                'через возможные помехи расстояние и загрузку. Сравнение скорости '
                'помогает сузить версии но не устанавливает единственную причину.')
    if rule == 'grounding.restore_failure_verdict':
        return ('Сохрани задачу проверки резервной копии: тестовое восстановление в отдельное '
                'место сверка полноты данных и проверка файлов или приложения. '
                'Читаемость отдельных файлов не подтверждает всю копию. '
                'Сбой восстановления сам по себе не доказывает повреждение копии: '
                'причину выясняют по ошибке доступа совместимости или настроек. '
                'Не заменяй проверку копии одним разбором ошибок.')
    if rule in ('grounding.network_comparison_verdict', 'grounding.network_culprit_after_condition'):
        return ('Сохрани объяснение и сравнение подключений. Разница скорости лишь сужает '
                'возможные причины; не исключай провайдера и не назначай виновника по одному сравнению.')
    return ''


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
