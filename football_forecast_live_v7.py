#!/usr/bin/env python3
"""Offline snapshot-based forward forecasts, separate from the running Telegram bot.

No network, no service changes. Existing V1, V2, V3 formulas are reused without
retraining. CSV kickoff timezone is a *user-confirmed assumption*, not inferred.
Scores on target day are NEVER used for that day's forecast.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from football_backtest import Fixture, LEAGUES, SEASONS, read_fixtures
from football_compare_v2 import candidate_prediction
from football_compare_v3 import elo_probabilities, update_rating
from football_predictions import estimate, canonical

CODES = {'EPL':'E0', 'LaLiga':'SP1', 'Bundesliga':'D1', 'SerieA':'I1', 'Ligue1':'F1'}
SOURCE = Path('state/football_live/source')
MAPPING = Path('state/football_live/team_mapping.json')
HISTORY = Path('state/football_backtest/data')
DB = Path('state/football_live/forecasts_v7.sqlite3')
VERSION = 'research_v7_v1_v2_dc-0.08_v3_elo25'


def get_date(raw):
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Непонятная дата: {raw!r}')


def get_time(raw):
    raw = (raw or '').strip()
    if not raw:
        raise ValueError('Время не указано')
    try:
        return datetime.strptime(raw, '%H:%M').time()
    except ValueError:
        raise ValueError(f'Непонятное время: {raw!r}')


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'Div','Date','HomeTeam','AwayTeam'}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError(f'Нет обязательных столбцов CSV: {path}')
        return list(reader)


def source_snapshot(files):
    return {str(p): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                      'mtime_utc': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
            for p in files}


def load_matches(history, source, mapping):
    prev, skipped = read_fixtures(history, list(LEAGUES), list(SEASONS))
    aliases = json.loads(mapping.read_text(encoding='utf-8'))
    all_matches = list(prev)
    stats = defaultdict(int)
    for league in LEAGUES:
        league_map = aliases[league]['verified']
        no_history = set(aliases[league]['no_top_division_history'])
        rows = read_csv(source / f'{league}_2026-27.csv')
        for row in rows:
            if row.get('Div','').strip() != CODES[league]:
                raise ValueError(f'{league}: неожиданный код дивизиона в сезонном CSV')
            home = (row.get('HomeTeam') or '').strip()
            away = (row.get('AwayTeam') or '').strip()
            if not home or not away or home == away:
                raise ValueError(f'{league}: некорректные команды {home}/{away}')
            for name in (home,away):
                if name not in league_map and name not in no_history:
                    raise ValueError(f'{league}: неизвестная команда {name!r}; повторите V6')
            h, a = (row.get('FTHG') or '').strip(), (row.get('FTAG') or '').strip()
            if not h and not a:
                continue
            if not h.isdigit() or not a.isdigit() or max(int(h), int(a)) > 15:
                raise ValueError(f'{league}: неполный или неверный счёт {home} {h}:{a} {away}')
            gh,ga=int(h),int(a)
            if (row.get('FTR') or '').strip() not in ('H' if gh>ga else 'A' if gh<ga else 'D', ''):
                raise ValueError(f'{league}: результат FTR противоречит счёту')
            home_id=league_map.get(home) or f'__new_2627__{home}'
            away_id=league_map.get(away) or f'__new_2627__{away}'
            match=Fixture(get_date(row['Date']),league,home_id,away_id,(gh,ga))
            all_matches.append(match)
            stats[league]+=1
    if len(all_matches) != len(set((m.league,m.day,m.home,m.away) for m in all_matches)):
        raise ValueError('Повторяющийся матч в объединённой истории')
    return sorted(all_matches,key=lambda m:(m.day,m.league,m.home,m.away)),aliases,stats,skipped


def forecast_at(match, past, ratings):
    # All dates strictly less than the kickoff date; never use same-day scores.
    prior = [m for m in past if m.league == match['league'] and m.day < match['day']]
    day=match['day']
    home,away=match['home_id'],match['away_id']
    p2=candidate_prediction(home,away,prior,match['league'],day)
    if p2 is None:
        return None,'V2: недостаточно истории по командам/лиге'
    def recent(team):
        rr=[]
        for m in reversed(prior):
            if team in (m.home,m.away) and 0<(day-m.day).days<=180:
                rr.append(dict(day=m.day.isoformat(),home=canonical(m.home),away=canonical(m.away),score=m.goals))
            if len(rr)==8: break
        return rr
    home_rows,away_rows=recent(home),recent(away)
    if min(len(home_rows),len(away_rows))<5:
        return None,'V1: менее пяти результатов у одной из команд за 180 дней'
    raw=estimate(home,away,home_rows,away_rows)
    p1=tuple(float(x)/100 for x in raw['probabilities'])
    p3=elo_probabilities(p2,ratings[match['league'],home],ratings[match['league'],away])
    if any(len(p)!=3 or any(not math.isfinite(x) or x<0 or x>1 for x in p) or abs(sum(p)-1)>1e-6 for p in (p1,p2,p3)):
        raise ValueError('Модель вернула неправильные вероятности')
    return {'V1':p1,'V2':p2,'V3':p3},None


def rating_before(matches, date_limit):
    ratings=defaultdict(lambda:1500.0)
    for m in matches:
        if m.day>=date_limit: break
        update_rating(ratings,m)
    return ratings


def fixtures_for(source, aliases, now, zone, lead_minutes):
    rows=read_csv(source/'fixtures.csv')
    selected=[]
    seen=set()
    failures=[]
    for row in rows:
        code=(row.get('Div') or '').strip()
        if code not in CODES.values(): continue
        league=next(k for k,v in CODES.items() if v==code)
        try:
            day=get_date(row['Date'])
            clock=get_time(row.get('Time'))
            local=datetime.combine(day,clock,tzinfo=zone)
            kickoff=local.astimezone(timezone.utc)
        except (KeyError,ValueError) as exc:
            failures.append((league,row.get('HomeTeam'),row.get('AwayTeam'),str(exc)))
            continue
        if kickoff <= now+timedelta(minutes=lead_minutes): continue
        h=(row.get('HomeTeam') or '').strip()
        a=(row.get('AwayTeam') or '').strip()
        known=aliases[league]['verified']
        new=set(aliases[league]['no_top_division_history'])
        if h not in known and h not in new or a not in known and a not in new:
            failures.append((league,h,a,'Неизвестная команда в расписании'))
            continue
        key=(league,day.isoformat(),h,a)
        if key in seen:
            raise ValueError(f'Дублирующаяся встреча расписания: {key}')
        seen.add(key)
        selected.append(dict(league=league,day=day,home=h,away=a,
                             home_id=known.get(h) or f'__new_2627__{h}',
                             away_id=known.get(a) or f'__new_2627__{a}',
                             kickoff_utc=kickoff))
    return sorted(selected,key=lambda x:x['kickoff_utc']),failures


def create_db(db):
    db.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(db) as cx:
        cx.execute('''CREATE TABLE IF NOT EXISTS predictions (
          league TEXT NOT NULL, match_date TEXT NOT NULL,
          home TEXT NOT NULL, away TEXT NOT NULL, model TEXT NOT NULL,
          kickoff_utc TEXT NOT NULL, forecast_utc TEXT NOT NULL,
          p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
          model_version TEXT NOT NULL, source_hashes TEXT NOT NULL,
          kickoff_zone TEXT NOT NULL,
          PRIMARY KEY (league,match_date,home,away,model)
        )''')
        # No UPDATE is used. Additional trigger protects accidental changes later.
        cx.execute('''CREATE TRIGGER IF NOT EXISTS predictions_no_update
          BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT,'predictions are immutable'); END''')
        cx.execute('''CREATE TRIGGER IF NOT EXISTS predictions_no_delete
          BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT,'predictions are immutable'); END''')


def record(db, match, forecasts, now, hashes, zone):
    n=0
    with sqlite3.connect(db) as cx:
        for model,p in forecasts.items():
            cursor=cx.execute('''INSERT OR IGNORE INTO predictions
              (league,match_date,home,away,model,kickoff_utc,forecast_utc,
               p_home,p_draw,p_away,model_version,source_hashes,kickoff_zone)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                match['league'],match['day'].isoformat(),match['home'],match['away'],model,
                match['kickoff_utc'].isoformat(),now.isoformat(),*p,VERSION,
                json.dumps(hashes,sort_keys=True),zone))
            n+=cursor.rowcount
    return n


def main(argv=None):
    ap=argparse.ArgumentParser(description='Read-only preview or immutable separate SQLite snapshots')
    ap.add_argument('--source',type=Path,default=SOURCE)
    ap.add_argument('--mapping',type=Path,default=MAPPING)
    ap.add_argument('--history',type=Path,default=HISTORY)
    ap.add_argument('--db',type=Path,default=DB)
    ap.add_argument('--kickoff-zone',default='Europe/London',help='Assumed source local timezone; MUST independently verify before recording')
    ap.add_argument('--lead-minutes',type=int,default=60)
    ap.add_argument('--record',action='store_true',help='Save immutable separate DB after manual timezone validation')
    ap.add_argument('--confirmed-kickoffs',action='store_true',help='Explicit acknowledgement of independently verified kickoff times')
    args=ap.parse_args(argv)
    if args.lead_minutes<0: ap.error('lead-minutes must be >=0')
    if args.record and not args.confirmed_kickoffs: ap.error('--record requires --confirmed-kickoffs')
    try: zone=ZoneInfo(args.kickoff_zone)
    except Exception as exc: ap.error(f'Invalid timezone: {exc}')
    now=datetime.now(timezone.utc)
    files=[args.mapping,args.source/'fixtures.csv']+[args.source/f'{k}_2026-27.csv' for k in LEAGUES]
    files += [args.history/f'{k}_{s}.json' for k in LEAGUES for s in SEASONS]
    hashes=source_snapshot(files)
    matches,aliases,stats,skipped=load_matches(args.history,args.source,args.mapping)
    fixtures,failures=fixtures_for(args.source,aliases,now,zone,args.lead_minutes)
    print('Снимок данных:',now.isoformat(),'UTC; часовой пояс календаря (предположение):',args.kickoff_zone)
    print('История:',len(matches),'завершённых встреч; текущий сезон:',dict(stats))
    print('Матчи после временного порога:',len(fixtures),'ошибки расписания:',len(failures))
    for f in failures[:15]: print('ПРОПУСК:',f)
    if args.record: create_db(args.db)
    written=0; covered=0
    for fixture in fixtures:
        rates=rating_before(matches,fixture['day'])
        probabilities,reason=forecast_at(fixture,matches,rates)
        title=f"{fixture['league']} {fixture['day']} {fixture['home']} — {fixture['away']}"
        if probabilities is None:
            print('SKIP:',title,';',reason)
            continue
        covered+=1
        p=probabilities['V3']
        print(f"{title} | UTC {fixture['kickoff_utc']:%Y-%m-%d %H:%M} | V3 {p[0]:.1%}/{p[1]:.1%}/{p[2]:.1%} | V1 {probabilities['V1'][0]:.1%}/{probabilities['V1'][1]:.1%}/{probabilities['V1'][2]:.1%}")
        if args.record:
            # Source snapshot itself must be strictly older than kickoff; protects postgame import.
            last_source=max(datetime.fromisoformat(meta['mtime_utc']) for meta in hashes.values())
            if last_source>=fixture['kickoff_utc']:
                print('  НЕ ЗАПИСАНО: один из входных файлов сохранён после начала матча')
                continue
            written+=record(args.db,fixture,probabilities,now,hashes,args.kickoff_zone)
    print('Смогли спрогнозировать:',covered,'из',len(fixtures))
    if args.record: print('Добавлено новых неизменяемых записей:',written,'; база:',args.db)
    else: print('РЕЖИМ PREVIEW: ничего не записано. Для записи нужны отдельно подтверждённые времена начала.')

if __name__=='__main__': main()
