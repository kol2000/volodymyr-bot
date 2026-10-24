"""Cheap, conservative dialogue routing before CPU inference."""
import re

CALL = re.compile(r'^\s*(?:(?:@[a-z0-9_]+|бубус|бубуська|володька|володько)[\s,:!]+)+', re.I)
CONTROLS = {'короче': 'concise', 'покороче': 'concise', 'кратко': 'concise',
            'объясни нормально': 'plain', 'объясни проще': 'plain',
            'без приколов': 'serious', 'по делу': 'serious',
            'приведи пример': 'example', 'еще пример': 'example', 'ещё пример': 'example'}
AMENDMENT = re.compile(r'^(?:нет\b|не так\b|неправильно\b|я (?:про|имел в виду)\b|уточн(?:ю|ение)\b|'
                       r'поправка\b|точнее\b|а (?:если|почему|как|зачем|когда)\b)', re.I)
FACTUAL = re.compile(r'\b(?:почему|зачем|объясни|поясни|расскажи|помоги|как\s+(?:работает|устроен|'
                     r'сделать|настроить|исправить)|сколько|стоимость|цена|погода|сч[её]т|курс|'
                     r'сервер|соединени\w*|ошибк\w*|памят\w*|роутер|интернет|футбол)\b', re.I)
TEASING = re.compile(r'\b(?:цац[аыу]|цацик|цацыг|свиноголовин\w*|сосатка|хряк|свинья|бубуська)\b', re.I)
PUBLIC_SUBJECT = re.compile(r'\b(?:президент\w*|премьер[- ]министр\w*|правительств\w*|парламент\w*|'
                            r'чемпионат\w*|лига|лиги|рпл|кубок|футбол\w*|цска|спартак|зенит|'
                            r'ростов|акрон|реал|барселона|арсенал|ливерпуль|манчестер|'
                            r'росси\w*|украин\w*|франци\w*|германи\w*|казахстан\w*|'
                            r'великобритани\w*|сша|китай|москв\w*|'
                            r'python|linux|windows|ubuntu|debian|ollama|qwen\w*|telegram|'
                            r'openai|chatgpt|apple|microsoft|google|nvidia|amd|intel|'
                            r'bitcoin|ethereum|btc|eth|bnb|usdt|биткоин\w*|доллар\w*|'
                            r'инфляци\w*|центробанк\w*|цб|наса|nasa|spacex|мкс)\b', re.I)
UNSPECIFIED = set(('жёлтые желтые зелёные зеленые красные белые чёрные черные синие '
                   'большие маленькие старые новые дорогие дешёвые дешевые непонятные').split())

BOT_ALIASES = ('бубус', 'бубуська', 'володька', 'володько', 'цаца', 'цацик', 'цацыг')
BOT_ALIAS = r'(?:' + '|'.join(BOT_ALIASES) + r')'
REPORTING_VERB = r'(?:сказал[аи]?|написал[аи]?|ответил[аи]?|спросил[аи]?|сообщил[аи]?|рассказал[аи]?|объяснил[аи]?)'


def address_roles(kind, alias, body):
    result = dict(address_kind=kind, bot_alias=alias, message_without_address=body,
                  perspective=dict(incoming_you='bot', outgoing_you='speaker', incoming_i='speaker', outgoing_i='bot'))
    if re.match(r'^\s*(?:(?:ну|вот|наконец|наконец-то|ты|ти|опять|снова|и|же)\s+){0,4}'
                r'(?:явился|появился|приш[её]л|вернулся)\b', body, re.I):
        result['event_roles'] = dict(event='arrival', arrived='bot', waited='speaker')
    return result


def dialogue_roles(text):
    """Conservative surface cues: quoted names are not vocative addresses."""
    visible = re.sub(r'```.*?```|`[^`]*`|«[^»]*»|"[^"]*"', lambda m: ' ' * len(m[0]), text, flags=re.S)
    bare = re.fullmatch(r'\s*(' + BOT_ALIAS + r')[\s!?.]*', visible, re.I)
    # Do not interpret an otherwise quoted name as an empty bare call.
    if bare and visible.strip() == text.strip():
        return dict(address_kind='bare_call', bot_alias=bare[1].casefold(), message_without_address='')
    found = list(re.finditer(r'\b(' + BOT_ALIAS + r')\b', visible, re.I))
    for match in found:
        before, after = visible[:match.start()], visible[match.end():]
        start = not before.strip(' ,!?.')
        end = not after.strip(' ,!?.')
        second_person = bool(re.search(r'\b(?:ты|ти|тебя|тебе|твой|твоя)\b', visible, re.I))
        reported = start and bool(re.match(r'\s+' + REPORTING_VERB + r'\b', after, re.I))
        if reported:
            continue
        explicit_prefix = start and bool(re.match(r'\s*,|\s+(?:не\s+)?(?:умничай|тупи|отвечай|проснись|расскажи|объясни)\b', after, re.I))
        explicit_suffix = end and bool(re.search(r',\s*$|\b(?:явился|появился|приш[её]л|вернулся)\s+наконец\s*$', before, re.I))
        if ((start or end) and second_person) or explicit_prefix or explicit_suffix:
            body = (text[:match.start()] + text[match.end():]).strip(' ,!?.')
            return address_roles('bot_address', match[1].casefold(), body)
    # A boundary alias without a vocative cue can name a person or address the
    # bot. Preserve that ambiguity rather than pretending it is resolved.
    if found:
        match = found[0]
        before, after = visible[:match.start()], visible[match.end():]
        if (not before.strip(' ,!?.') and not re.match(
                r'\s+(?:' + REPORTING_VERB + r'|он|она|они)\b', after, re.I)):
            body = (text[:match.start()] + text[match.end():]).strip(' ,!?.')
            result = dict(address_kind='ambiguous', bot_alias=match[1].casefold(), message_without_address=body)
            return result
    quoted_alias = re.search(r'\b' + BOT_ALIAS + r'\b', text, re.I)
    result = dict(address_kind='mention' if found else 'quoted_mention' if quoted_alias else 'unspecified',
                  message_without_address=text)
    # Recognise only explicit reports, not any appearance of quotes or a name.
    # "Позови Цацика" may legitimately require addressing that person.
    reporting = re.search(r'^[^«"\n]{0,100}\b' + REPORTING_VERB + r'\s*:?\s*[«"]', text, re.I)
    third_person = found and (re.search(
        r'\b(?:мой|моя|наш|наша)\s+(?:друг|подруга|коллега|сосед|знакомый)\s*$',
        visible[:found[0].start()], re.I) or re.match(
        r'\s+' + REPORTING_VERB + r'\b', visible[found[0].end():], re.I))
    third_person = third_person or re.match(
        r'^\s*(?:(?:мой|моя|наш|наша)\s+(?:друг|подруга|коллега|сосед|знакомый)\b|'
        r'(?:он|она|они)\s+' + REPORTING_VERB + r'\b)', visible, re.I)
    relay = re.search(r'\b(?:ответь|скажи|передай|позови|обратись)\b', visible, re.I)
    if (reporting or third_person) and not relay:
        result['reply_scope'] = 'reported_speech' if reporting else 'third_person'
        result['referenced_alias'] = found[0][1].casefold() if found else quoted_alias[0].casefold() if quoted_alias else ''
    return result


def banter_brief(roles):
    """A small audience brief, not canned answers or a semantic classifier."""
    scope = roles.get('reply_scope')
    if scope == 'reported_speech':
        return 'Обсуди с собеседником пересказ чужих слов. Не отвечай человеку внутри цитаты и не принимай цитату за обращение к себе.'
    if scope == 'third_person':
        return 'Обсуди с собеседником названного третьего человека. Не обращайся к этому человеку напрямую и не присваивай его поступки себе.'
    if roles['address_kind'] == 'bot_address':
        if roles.get('event_roles', {}).get('event') == 'arrival':
            return 'Собеседник приветствует твоё возвращение. Вернулся ты, ждал собеседник. Откликнись от себя; причина отсутствия неизвестна.'
        return 'Это реплика тебе. Ответь от себя на её смысл. Не пересказывай обвинение обратно собеседнику и не объясняй ему кто из вас бот.'
    if roles['address_kind'] == 'ambiguous':
        return 'Адресат прозвища неясен. Реагируй на смысл без назначения прозвища собеседнику; если без адресата непонятно — уточни.'
    return 'Реагируй на текущую мысль собеседника. Не достраивай неупомянутые события и отношения.'


def ambiguous_subject(text):
    match = re.fullmatch(r'[«"“]?([а-яё]+)\s*\?+[!?.»”"\s]*', control_remainder(text), re.I)
    return match[1].casefold() if match and match[1].casefold() in UNSPECIFIED else None


REFERENCE_WORDS = set(('он она оно они это ето у меня нам мне а и но ну опять снова '
                       'вновь уже ещё ещо все всё совсем вообще почему зачем как что шо '
                       'не ни никак работает работают работал работала работали работало '
                       'работать перестал перестала перестали перестало стал стала стали стало '
                       'тормозит тормозят завис зависла зависло зависли зависает зависают '
                       'включается включаются запускается запускаются открывается открываются '
                       'отвечает отвечают подключается подключаются греется греются '
                       'помоги объясни пожалуйста привет понятно ладно спасибо ок да нет').split())


def missing_reference(text, references=()):
    """Bounded pronoun-only fault reports, not general noun/coreference parsing."""
    tokens = re.findall(r'[а-яёa-z0-9_]+', control_remainder(text).casefold())
    pronouns = {'он', 'она', 'оно', 'они', 'это', 'ето'}
    faults = {'работает', 'работают', 'работать', 'тормозит', 'тормозят', 'завис',
              'зависла', 'зависло', 'зависли', 'зависает', 'зависают', 'включается',
              'включаются', 'запускается', 'запускаются', 'открывается', 'открываются',
              'отвечает', 'отвечают', 'подключается', 'подключаются', 'греется', 'греются'}
    if not (set(tokens) & pronouns and set(tokens) & faults and set(tokens) <= REFERENCE_WORDS):
        return False
    for reference in references:
        named = re.findall(r'[а-яёa-z0-9_]+', reference.casefold())
        if any(len(word) >= 3 and word not in REFERENCE_WORDS for word in named):
            return False
    return True


def response_control(text):
    body = CALL.sub('', text.strip()).casefold().strip(' .!?,')
    return CONTROLS.get(body)


def inline_control(text):
    """A prefix modifies this answer only; quoted occurrences do not set a mode."""
    body = CALL.sub('', text.strip()).casefold()
    for phrase, control in sorted(CONTROLS.items(), key=lambda item: -len(item[0])):
        if re.match(re.escape(phrase) + r'(?:[\s,:.!?]|$)', body):
            return control
    return None


def control_remainder(text):
    body = CALL.sub('', text.strip())
    for phrase in sorted(CONTROLS, key=len, reverse=True):
        match = re.match(re.escape(phrase) + r'(?:[\s,:.!?]+|$)', body, re.I)
        if match:
            return body[match.end():].strip()
    return body


def banter_request(text):
    body = control_remainder(text)
    if inline_control(text) or FACTUAL.search(body):
        return False
    return bool(TEASING.search(body) and len(body.split()) <= 14)


def amendment_request(text):
    return bool(response_control(text) or AMENDMENT.match(CALL.sub('', text.strip())))


def elliptical_followup(text):
    """These complete turns need an explicit subject from a personal dialogue."""
    body = CALL.sub('', text.strip()).casefold().strip(' .!?,')
    body = re.sub(r'^а\s+', '', body)
    if body in ('почему', 'зачем', 'как'):
        return 'reason' if body != 'как' else 'method'
    if body in ('если наоборот', 'наоборот'):
        return 'reverse_condition'
    return None


def verification_query(text):
    """Only public, time-dependent questions. Never send private chat context out."""
    body = control_remainder(text)
    if banter_request(text) or len(body) < 8:
        return None
    # Personal devices, chat participants and quotations are not public searches.
    if re.search(r'@|https?://|\b(?:мой|моя|мо[её]|мои|меня|мне|тебя|ти|ты|наш\w*|'
                 r'чат\w*|парол\w*|токен\w*|ключ\w*|кошел[её]к)\b|(?:\d{1,3}\.){3}\d{1,3}', body, re.I):
        return None
    public = bool(re.search(r'\b(?:правда\s+ли|это\s+правда|ето\s+правда|кто\s+выиграл|'
                            r'кто\s+победил|последние\s+новости)\b', body, re.I))
    fresh = bool(re.search(r'\b(?:сейчас|сечас|сегодня|вчера|текущ\w*|актуальн\w*|последн\w*)\b', body, re.I))
    # Do not export an old dialogue to resolve "они/это". Clarify the public subject.
    without_time = re.sub(r'\b(?:а|они|он|она|оно|это|ето|стоят|стоит|сколько|сейчас|сечас|сегодня|вчера)\b', '', body, flags=re.I)
    named_subject = len(re.findall(r'[а-яёa-z]{3,}', without_time, re.I)) >= 1
    if not named_subject:
        return None
    # Unknown people or subjects require an explicit /check rather than silently
    # publishing a search query about a participant's private affairs.
    if (public or fresh) and PUBLIC_SUBJECT.search(body):
        return body[:300]
    return None
