#!/usr/bin/env python3
"""Read-only scoring of immutable V7 pre-match forecasts against local CSV results.

Does not fetch URLs or change any database. Scores only games whose kickoff has
passed and whose final score is present in a current-season CSV snapshot.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3

CODES = {'EPL':'E0', 'LaLiga':'SP1', 'Bundesliga':'D1', 'SerieA':'I1', 'Ligue1':'F1'}
DB = Path('state/football_live/forecasts_v7.sqlite3')
SOURCE = Path('state/football_live/source')

def get_date(raw):
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Непонятная дата: {raw!r}')

def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'Div','Date','HomeTeam','AwayTeam'}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError(f'Нет обязательных столбцов CSV: {path}')
        return list(reader)



def load_results(source: Path):
    results = {}
    for league in CODES:
        path = source / f'{league}_2026-27.csv'
        for row in read_csv(path):
            if (row.get('Div') or '').strip() != CODES[league]:
                raise ValueError(f'Неверный Div в {path}')
            h, a = (row.get('HomeTeam') or '').strip(), (row.get('AwayTeam') or '').strip()
            gh, ga = (row.get('FTHG') or '').strip(), (row.get('FTAG') or '').strip()
            if not gh and not ga:
                continue
            if not gh.isdigit() or not ga.isdigit():
                raise ValueError(f'Неверный счёт {league} {h} {a}: {gh}:{ga}')
            x, y = int(gh), int(ga)
            if x > 15 or y > 15:
                raise ValueError(f'Счёт требует проверки {league} {h} {a}: {x}:{y}')
            result_letter = 'H' if x > y else 'A' if x < y else 'D'
            if (row.get('FTR') or '').strip() not in ('', result_letter):
                raise ValueError(f'Противоречие итогового результата {league} {h} {a}')
            key = league, get_date(row['Date']).isoformat(), h, a
            if key in results:
                raise ValueError(f'Повтор результата {key}')
            results[key] = (result_letter, x, y)
    return results


def parse_aware(value: str):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f'Дата без часового пояса: {value}')
    return timestamp.astimezone(timezone.utc)


def score_rows(rows, results, now, min_elapsed_hours=3):
    scored = []
    pending = defaultdict(int)
    invalid = []
    for row in rows:
        league, day, home, away, model, kick_raw, pred_raw, ph, pd, pa = row
        identity = (league, day, home, away)
        try:
            kickoff = parse_aware(kick_raw)
            predicted = parse_aware(pred_raw)
            probs = (ph, pd, pa)
            if predicted >= kickoff:
                raise ValueError('прогноз записан не до начала матча')
            if not all(isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in probs):
                raise ValueError('неправильные вероятности')
            if abs(sum(probs) - 1) > 1e-5:
                raise ValueError('вероятности не суммируются в 1')
            if league not in CODES:
                raise ValueError('неизвестная лига')
            if abs((datetime.fromisoformat(day).date() - kickoff.date()).days) > 1:
                raise ValueError('дата матча расходится с UTC началом более чем на день')
        except (ValueError, TypeError) as exc:
            invalid.append((identity, model, str(exc)))
            continue
        if now < kickoff + timedelta(hours=min_elapsed_hours):
            pending['ещё не завершён по временному порогу'] += 1
            continue
        if identity not in results:
            pending['нет итогового счёта в локальных CSV'] += 1
            continue
        actual, gh, ga = results[identity]
        index = {'H':0, 'D':1, 'A':2}[actual]
        logloss = -math.log(max(probs[index], 1e-15))
        brier = sum((p - (i == index))**2 for i, p in enumerate(probs))
        accuracy = int(max(range(3), key=lambda i: probs[i]) == index)
        scored.append(dict(key=identity, model=model, score=f'{gh}:{ga}', outcome=actual,
                           accuracy=accuracy, logloss=logloss, brier=brier))
    return scored, pending, invalid


def show_summary(scored):
    by_model = defaultdict(list)
    for item in scored:
        by_model[item['model']].append(item)
    if not scored:
        print('Пока нет завершённых матчей с результатами в сохранённых CSV. Это нормально.')
        return
    print('\nСРАВНЕНИЕ МОДЕЛЕЙ (только учтённые результаты)')
    print(f"{'Модель':<10}{'Матчей':>10}{'Accuracy':>12}{'LogLoss':>12}{'Brier':>12}")
    for model in sorted(by_model):
        values = by_model[model]
        n = len(values)
        print(f"{model:<10}{n:>10}{sum(x['accuracy'] for x in values)/n:>11.1%}"
              f"{sum(x['logloss'] for x in values)/n:>12.4f}"
              f"{sum(x['brier'] for x in values)/n:>12.4f}")
    print('\nМАТЧИ')
    fixtures = defaultdict(list)
    for item in scored:
        fixtures[item['key']].append(item)
    for (league, day, home, away), items in sorted(fixtures.items()):
        details = ', '.join(f"{r['model']}: {'✓' if r['accuracy'] else '✗'}" for r in sorted(items,key=lambda r:r['model']))
        print(f'{day} {league} {home} — {away} {items[0]["score"]} | {details}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only report for archived V7 forecasts')
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--min-elapsed-hours', type=int, default=3)
    args = parser.parse_args(argv)
    if args.min_elapsed_hours < 0:
        parser.error('--min-elapsed-hours must be nonnegative')
    if not args.db.is_file():
        parser.error(f'Нет базы прогнозов: {args.db}')
    # Open DB in enforced SQLite read-only mode; never mutate even accidentally.
    with sqlite3.connect(f'file:{args.db.resolve().as_posix()}?mode=ro', uri=True) as conn:
        rows = conn.execute('''SELECT league,match_date,home,away,model,kickoff_utc,
                                     forecast_utc,p_home,p_draw,p_away
                              FROM predictions ORDER BY kickoff_utc,league,home,model''').fetchall()
    results = load_results(args.source)
    now = datetime.now(timezone.utc)
    scored, pending, invalid = score_rows(rows,results,now,args.min_elapsed_hours)
    print('Проверка:',now.isoformat())
    print('Прогнозов в базе:',len(rows),'| уникальных матчей:',len({tuple(r[:4]) for r in rows}))
    print('Итоговых результатов в локальном источнике:',len(results))
    print('Учтено прогнозов:',len(scored))
    for why, n in sorted(pending.items()):
        print(f'ОЖИДАНИЕ: {why}: {n}')
    for identity,model,reason in invalid:
        print('ОШИБКА ПРОГНОЗА:',identity,model,reason)
    show_summary(scored)
    if invalid:
        raise SystemExit('Есть некорректные записи; сводку нельзя считать окончательной')

if __name__ == '__main__':
    main()
