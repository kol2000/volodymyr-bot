"""Shared, explicit 90-minute forecasts and immutable forward journal.
No Telegram client; no changes to historical V8 observations.
"""
import hashlib
import json
import math
import os
import re
import time
import tempfile
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

VERSION = 'all-competitions-v1'
ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / 'state/football_forecasts'


def competition_kind(name):
    n = name.casefold().replace('ё', 'е')
    if re.search(r'жен|молод|юнош|u[- ]?\d{2}|до \d{2}', n):
        return None
    if 'кубок россии' in n: return 'russian_cup'
    if 'лига чемпионов' in n: return 'champions_league'
    if 'лига европы' in n: return 'europa_league'
    if any(s in n for s in ('лига наций', 'чемпионат мира', 'чемпионат европы', 'сборные', 'отбор чм', 'отбор евро')) and 'клубный' not in n:
        return 'national'
    if any(s in n for s in ('премьер-лига россии', 'премьер-лига,', 'российская премьер', 'рпл')) or n.strip() == 'премьер-лига': return 'rpl'
    if any('чемпионат '+s in n for s in ('англии','испан','италии','германии','франции','португалии','нидерландов','голландии','турции','украины')): return 'european_league'
    return None


def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate(record):
    if record.get('schema') != VERSION or record.get('market') != '90_minutes_1x2': raise ValueError('invalid forecast schema/market')
    score = record['score']
    if not isinstance(score,(list,tuple)) or len(score)!=2 or not all(type(x) is int and 0<=x<=12 for x in score): raise ValueError('invalid score')
    probs = record['probabilities']
    if probs is not None:
        if len(probs)!=3 or not all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=1 for x in probs) or abs(sum(probs)-1)>1e-6: raise ValueError('invalid probabilities')
    if record['created'] >= record['kickoff'] or not all(math.isfinite(record[k]) for k in ('created','kickoff')): raise ValueError('forecast must precede kickoff')
    if record.get('production_eligible') is not False: raise ValueError('unvalidated quality flag')
    return record


def build(service, match, model=None):
    from football_predictions import BASE, LEAGUES, canonical, team_index, results, estimate
    from live_football import MOSCOW, FootballError
    now=service.clock()
    if match.status!='scheduled' or now>=match.kickoff: raise ValueError('not a pre-match fixture')
    kind=competition_kind(match.competition)
    if not kind: raise ValueError('unsupported competition')
    day=datetime.fromtimestamp(match.kickoff,MOSCOW).date()
    cutoff=min(day,datetime.fromtimestamp(now,MOSCOW).date())
    record=dict(schema=VERSION,match_id=match.id,competition=match.competition,kind=kind,
        home=match.home,away=match.away,kickoff=match.kickoff,created=now,
        market='90_minutes_1x2',source_url=getattr(match,"url",""),source_checked=getattr(match,"checked",0),
        production_eligible=False,probabilities=None,score=[1,1],method='neutral_prior',
        evidence=[],confidence='low',training_before=cutoff.isoformat())
    if kind=='national':
        national=national_estimate(match.home,match.away,cutoff)
        if national: record.update(national)
    else:
        season=day.year+int(day.month>=7);index={}
        # Use the same bounded cache and historical parsing as the existing bot.
        try:index.update(team_index(service.page(BASE+'/ruschamp/'),season))
        except FootballError:pass
        if not all(canonical(t) in index for t in (match.home,match.away)):
            from concurrent.futures import ThreadPoolExecutor
            def load_index(league):
                try:return team_index(service.page(BASE+'/'+league+'/'),season)
                except FootballError:return {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                for values in pool.map(load_index,LEAGUES[1:]):index.update(values)
        urls=[index.get(canonical(t)) for t in (match.home,match.away)]
        if all(urls):
            try:
                history=[results(service.page(u),t,cutoff) for u,t in zip(urls,(match.home,match.away))]
                value=estimate(match.home,match.away,*history)
                record.update(method='recent_form_poisson',probabilities=[p/100 for p in value['probabilities']],score=list(value['score']),
                    evidence=[dict(url=u,matches=len(rows),history_sha256=checksum(rows)) for u,rows in zip(urls,history)])
            except FootballError:pass
    if record['method']=='neutral_prior' and model:
        try:
            value=model(dict(query=f'{match.home} — {match.away}',day=day.isoformat(),home=match.home,away=match.away,market='90_minutes_1x2'))
            if isinstance(value,str):value=json.loads(value)
            candidate=value.get('score') if isinstance(value,dict) else None
            if isinstance(candidate,list) and len(candidate)==2 and all(type(n) is int and 0<=n<=12 for n in candidate):
                record.update(score=candidate,method='model_estimate')
        except (OSError,ValueError,TypeError,KeyError):pass
    # No invented probabilities for a model that supplied only a score.
    if service.clock() >= match.kickoff: raise ValueError("match started during calculation")
    return validate(record)


def national_estimate(home, away, cutoff, db=None):
    from goal_videos import norm
    from national_team_names import NATIONAL_NAMES,NATIONAL_CANONICAL
    from football_nations_v10 import load, probabilities, expected, INIT_ELO, K
    db=Path(db) if db else ROOT/'state/football_expansion/international_results.sqlite3'
    if not db.exists():return None
    def names(team):
        key=NATIONAL_CANONICAL.get(team.casefold(),team.casefold())
        return {norm(x) for x in (key,*NATIONAL_NAMES.get(key,()))}
    hs,aws=names(home),names(away)
    try:rows=[r for r in load(db) if r['match_date']<cutoff.isoformat()]
    except (OSError,ValueError,sqlite3.Error):return None
    ratings={};counts={};latest={}
    for row in rows:
        h,a=norm(row['home']),norm(row['away']);rh,ra=ratings.get(h,INIT_ELO),ratings.get(a,INIT_ELO)
        actual=1 if row['home_goals']>row['away_goals'] else 0 if row['home_goals']<row['away_goals'] else .5
        delta=K*(actual-expected(rh,ra,0 if row['neutral'] else 65))
        ratings[h]=rh+delta;ratings[a]=ra-delta
        for team in (h,a):counts[team]=counts.get(team,0)+1;latest[team]=row['match_date']
    home_keys=hs & ratings.keys();away_keys=aws & ratings.keys()
    if len(home_keys)!=1 or len(away_keys)!=1:return None
    h=home_keys.pop();a=away_keys.pop()
    if min(counts[h],counts[a])<5:return None
    # Venue is not supplied by the calendar: neutral prior, not invented home advantage.
    probs=probabilities(ratings[h],ratings[a],True)
    outcome=max(range(3),key=lambda i:probs[i]);score=([1,0],[1,1],[0,1])[outcome]
    return dict(method='national_elo',probabilities=list(probs),score=score,
        evidence=[dict(source='international_results.sqlite3',matches=len(rows),history_sha256=checksum(rows),latest=[latest[h],latest[a]],venue='unknown_assumed_neutral')])


def render(record):
    from common import HTMLMessage,bubus_spelling
    from live_football import MOSCOW
    validate(record);h,a=record['home'],record['away'];s=record['score'];p=record['probabilities']
    outcome=max(range(3),key=lambda i:p[i]) if p else (0 if s[0]>s[1] else 2 if s[0]<s[1] else 1)
    pick=('победа '+h,'ничья','победа '+a)[outcome]
    label={'recent_form_poisson':'Расчёт по предыдущим матчам чемпионатов','national_elo':'Экспериментальный рейтинг сборных; место проведения не подтверждено','model_estimate':'Оценка модели без подтверждённой свежей статистики','neutral_prior':'Нейтральный базовый сценарий: статистика и модель недоступны'}[record['method']]
    lines=[f'⚽ <b>Прогноз: {escape(h)} — {escape(a)}</b>',datetime.fromtimestamp(record['kickoff'],MOSCOW).strftime('%d.%m %H:%M МСК'),'',f'Мой вариант по исходу: <b>{escape(pick)}</b>',f'Предполагаемый счёт: <b>{s[0]}:{s[1]}</b>']
    if p:lines+=['П1 {:.0f}% · Х {:.0f}% · П2 {:.0f}%'.format(*(x*100 for x in p))]
    footer_start=len(lines)
    lines+=['',label+'. Точность ещё проверяется. Уверенность низкая. Без гарантий.']
    if record['kind'] in ('russian_cup','champions_league','europa_league'):lines+=['Составы, травмы, ротация и разница силы чемпионатов могут существенно изменить исход.']
    lines=[('<i>'+line+'</i>') if i>=footer_start and line else line for i,line in enumerate(lines)]
    return bubus_spelling(HTMLMessage('\n'.join(lines)))


def write_once(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.forecast-')
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chmod(temp,0o600)
        try:os.link(temp,path)
        except FileExistsError:return False
    finally:os.unlink(temp)
    return True


def identity(record):
    from soccer365_football import identity as team_id
    return checksum(dict(kind=record['kind'],home=team_id(record['home']),away=team_id(record['away']),kickoff=record['kickoff']))[:32]


def register(record, folder=JOURNAL):
    validate(record)
    if time.time() >= record["kickoff"]: raise ValueError("registration after kickoff")
    for existing_path in (Path(folder)/'observations').glob('*.json'):
        existing=read_observation(existing_path)
        if existing['match_id']==record['match_id']:
            if any(existing[k]!=record[k] for k in ('home','away','competition')):raise ValueError('forecast identity conflict')
            return existing
    path=Path(folder)/'observations'/(identity(record)+'.json')
    payload=dict(forecast=record,sha256=checksum(record))
    if not write_once(path,payload):
        existing=read_observation(path)
        if identity(existing)!=identity(record):raise ValueError('forecast identity conflict')
        return existing
    return record


def read_observation(path):
    saved=json.loads(Path(path).read_text(encoding='utf-8'));record=validate(saved['forecast'])
    if checksum(record)!=saved['sha256']:raise ValueError('forecast checksum mismatch')
    return record


def settle(record, match, folder=JOURNAL, now=None):
    now=datetime.now(timezone.utc).timestamp() if now is None else now
    validate(record)
    if any(getattr(match,k)!=record[k] for k in ('home','away','competition')) or match.id!=record['match_id']:raise ValueError('result identity mismatch')
    if match.kickoff!=record['kickoff']:return 'schedule_changed'
    if match.status!='finished' or now<match.kickoff+2*3600:return 'pending'
    score=[match.home_score,match.away_score]
    if not all(type(v) is int and 0<=v<=30 for v in score):return 'invalid_score'
    # Knockout scoreboards can include extra time/shootouts. Require a complete
    # event ledger and never silently grade those totals as a 90-minute result.
    if record['kind'] in ('russian_cup','champions_league','europa_league','national'):
        if not match.goals or len(match.goals)!=sum(score):return 'regulation_unconfirmed'
        regular=[0,0];previous=[0,0]
        for goal in match.goals:
            values=list(goal.score);diff=[values[i]-previous[i] for i in (0,1)]
            if diff not in ([1,0],[0,1]):return 'regulation_unconfirmed'
            minute=re.match(r'(\d+)',goal.minute)
            if not minute:return 'regulation_unconfirmed'
            if int(minute[1])<=90:regular=[regular[i]+diff[i] for i in (0,1)]
            previous=values
        if previous!=score:return 'regulation_unconfirmed'
        score=regular
    actual=0 if score[0]>score[1] else 2 if score[0]<score[1] else 1
    result=dict(match_id=match.id,competition=match.competition,score_90=score,actual=actual,forecast_sha256=checksum(record),source=match.url,checked=now)
    path=Path(folder)/'results'/(identity(record)+'.json')
    if not write_once(path,result):
        saved=json.loads(path.read_text())
        if saved['score_90']!=score or saved['forecast_sha256']!=result['forecast_sha256']:return 'result_conflict'
        return 'existing'
    return 'recorded'
