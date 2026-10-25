"""Allowlisted live settings shared with the local administration panel."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

DEFAULT_TRIGGERS = ['бубуська','володька','рахиня','украина','война','киев','цаца','цацик',
                    'бубус','цацыг','хряк','свинья','сосатка','крым','крим','даг','москва',
                    'путин','путен','вова','свин','крым наш']
DEFAULTS = {'trigger_words':DEFAULT_TRIGGERS,'reply_tone':'ironic','respond_questions':True,
            'automatic':False,'activity_probability':0.03,'adaptive':True,
            'greetings_enabled':True,'rules':'','livefootball:enabled':False,
            'livefootball:videos:enabled':True}


def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):
        raise ValueError('Неизвестные настройки')
    clean={}
    for key,value in values.items():
        if key=='trigger_words':
            if not isinstance(value,list) or len(value)>100:
                raise ValueError('Укажи не более 100 слов или фраз')
            terms=[]
            for term in value:
                if not isinstance(term,str) or not 1<=len(term.strip())<=60 or any(ord(c)<32 for c in term):
                    raise ValueError('Каждая фраза должна содержать от 1 до 60 символов')
                terms.append(' '.join(term.casefold().split()))
            value=list(dict.fromkeys(terms))
        elif key=='reply_tone':
            if value not in ('ironic','calm'):raise ValueError('Неизвестная манера ответа')
        elif key=='rules':
            if not isinstance(value,str) or len(value)>2800:raise ValueError('Правила: максимум 2800 символов')
        elif key=='activity_probability':
            if type(value) not in (float,int) or not 0.01<=value<=0.20:raise ValueError('Активность: от 1 до 20%')
        elif type(value) is not bool:
            raise ValueError('Переключатель должен быть включён или выключен')
        clean[key]=value
    return clean


def trigger_matches(text, configured, default_pattern, forms):
    if configured is None:
        terms=[' '.join(m.group().casefold().split()) for m in default_pattern.finditer(text)]
        return list(dict.fromkeys(forms.get(term,term) for term in terms))
    try:terms=validate({'trigger_words':configured})['trigger_words']
    except ValueError:return []
    expanded={term:term for term in terms}
    expanded.update({form:base for form,base in forms.items() if base in terms})
    if not expanded:return []
    pattern=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(t).replace(r'\ ',r'\s+')
        for t in sorted(expanded,key=len,reverse=True))+r')(?!\w)',re.I)
    return list(dict.fromkeys(expanded[' '.join(m.group().casefold().split())] for m in pattern.finditer(text)))


class SettingsDB:
    def __init__(self,path):self.path=Path(path).resolve()

    def connect(self):
        return sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=5)

    def snapshot(self,db):
        raw=dict(db.execute('SELECT key,value FROM settings'))
        values={k:json.loads(raw[k]) if k in raw else v for k,v in DEFAULTS.items()}
        revision=hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return values,revision,raw

    def read(self):
        with closing(self.connect()) as db:
            values,revision,raw=self.snapshot(db)
            counts={}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='goal_video_jobs'").fetchone():
                counts=dict(db.execute('SELECT status,count(*) FROM goal_video_jobs GROUP BY status'))
            return dict(settings=values,revision=revision,status=dict(
                video_jobs=counts,last_football_check=json.loads(raw.get('livefootball:success','0')),
                football_action=json.loads(raw.get('panel:football_result','null')),
                football_pending=json.loads(raw.get('panel:football_request','null')),
                connected=bool(json.loads(raw.get('chat_id','null')))))

    def save(self,values,revision):
        values=validate(values)
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old,current,raw=self.snapshot(db)
            if revision!=current:raise ValueError('Настройки уже изменились. Обнови страницу и повтори.')
            if 'livefootball:enabled' in values:
                enabled=values.pop('livefootball:enabled')
                if enabled!=old['livefootball:enabled']:
                    if not json.loads(raw.get('chat_id','null')):raise ValueError('Сначала подключи чат к Бубусу')
                    request=dict(id=uuid.uuid4().hex,enabled=enabled,created=time.time(),chat_id=json.loads(raw['chat_id']))
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('panel:football_request',json.dumps(request)))
            for key,value in values.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))
        return self.read()


def apply_football_request(bot):
    request=bot.store.get('panel:football_request')
    if not isinstance(request,dict) or request.get('id')==bot.store.get('panel:football_done'):
        return
    try:
        if type(request.get('enabled')) is not bool or time.time()-request.get('created',0)>300:
            raise ValueError('expired')
        if not request.get('chat_id') or request['chat_id']!=bot.store.get('chat_id'):
            raise ValueError('chat_changed')
        bot.football.answer(dict(kind='football_switch',enabled=request['enabled']),bot.store.get('chat_id'))
        if not request['enabled']:bot.goal_videos.cancel()
        result=dict(id=request['id'],ok=True)
    except Exception as exc:
        result=dict(id=request.get('id'),ok=False,error=type(exc).__name__)
    bot.store.set('panel:football_result',result)
    bot.store.set('panel:football_done',request.get('id'))
