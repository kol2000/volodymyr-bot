#!/usr/bin/env python3
"""Offline, leakage-conscious evaluation of national-team 1X2 predictions.
Reads ONLY the isolated V9 SQLite history. No network, no live writes.
All matches on a date are predicted against ratings from previous dates.
This is an experimental Elo baseline, NOT an independently validated model.
"""
import argparse
import collections
import datetime as dt
import math
import sqlite3
from pathlib import Path

DEFAULT_DB = Path('state/football_expansion/international_results.sqlite3')
K = 20.0
HOME_ADV = 65.0
INIT_ELO = 1500.0

def expected(home_rating, away_rating, advantage):
    return 1.0 / (1.0 + 10 ** ((away_rating - home_rating - advantage) / 400.0))

def probabilities(home_rating, away_rating, neutral):
    adv = 0 if neutral else HOME_ADV
    expected_home = expected(home_rating, away_rating, adv)
    # Draw chance is an explicit heuristic; this model must be backtested.
    draw = max(0.12, min(0.31, 0.28 * math.exp(-abs(home_rating + adv - away_rating) / 650.0)))
    return (expected_home * (1.0 - draw), draw, (1.0 - expected_home) * (1.0 - draw))

def actual(row):
    if row['home_goals'] > row['away_goals']:
        return 0
    if row['home_goals'] < row['away_goals']:
        return 2
    return 1

def metric(probs, outcome):
    logloss = -math.log(max(1e-12, probs[outcome]))
    brier = sum((prob - (idx == outcome)) ** 2 for idx, prob in enumerate(probs))
    return logloss, brier

def load(db_path):
    if not db_path.exists():
        raise FileNotFoundError(f'No V9 SQLite file: {db_path}')
    uri = f'file:{db_path.resolve()}?mode=ro'
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute('''
            SELECT match_date, home, away, home_goals, away_goals,
                   competition, neutral, id
            FROM matches ORDER BY match_date, id
        ''')]

def run(rows, start_year=2024, end_year=2026):
    ratings = collections.defaultdict(lambda: INIT_ELO)
    # Before 2024: naive fixed prior for baseline? Estimate historical home/draw/away frequencies
    # before scoring start, then freeze them to avoid future leakage.
    train = [r for r in rows if r['match_date'] < f'{start_year}-01-01']
    counts = collections.Counter(actual(r) for r in train)
    ntrain = len(train)
    baseline = tuple((counts[i] + 1) / (ntrain + 3) for i in range(3))
    results = []
    i = 0
    rows = sorted(rows, key=lambda r: (r['match_date'], r['id']))
    while i < len(rows):
        date = rows[i]['match_date']
        batch = []
        while i < len(rows) and rows[i]['match_date'] == date:
            batch.append(rows[i]); i += 1
        # All forecasts strictly use ratings at end of preceding day.
        for r in batch:
            yr = int(date[:4])
            if start_year <= yr <= end_year:
                probs = probabilities(ratings[r['home']], ratings[r['away']], r['neutral'])
                y = actual(r)
                results.append({
                    'year': yr, 'competition': r['competition'],
                    'actual': y, 'elo': probs, 'baseline': baseline,
                    'neutral': bool(r['neutral']), 'home': r['home'], 'away': r['away'],
                    'date': date,
                })
        # Ratings updated after forecasts for the whole day; multiple same-day games
        # update sequentially only AFTER every same-day game has been scored.
        for r in batch:
            h, a = ratings[r['home']], ratings[r['away']]
            eh = expected(h, a, 0 if r['neutral'] else HOME_ADV)
            outcome = actual(r)
            score = (1.0, 0.5, 0.0)[outcome]
            change = K * (score - eh)
            ratings[r['home']] = h + change
            ratings[r['away']] = a - change
    return results, baseline, len(train)

def summary(rows):
    if not rows: return None
    n = len(rows)
    out = {}
    for key in ('baseline', 'elo'):
        ll, br, acc = 0., 0., 0
        for r in rows:
            p = r[key]
            x, y = metric(p, r['actual'])
            ll += x; br += y
            acc += int(max(range(3), key=lambda k: p[k]) == r['actual'])
        out[key] = (acc / n, ll / n, br / n)
    return out

def category(competition):
    x = competition.casefold()
    if 'nations league' in x: return 'Лига наций'
    if 'friendly' in x: return 'Товарищеские'
    if 'fifa world cup qualification' in x: return 'Отбор ЧМ'
    if 'uefa euro qualification' in x or 'uefa european championship qualification' in x: return 'Отбор Евро'
    if 'fifa world cup' in x and 'qualification' not in x: return 'ЧМ'
    return 'Другие'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--start-year', type=int, default=2024)
    parser.add_argument('--end-year', type=int, default=2026)
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error('start-year > end-year')
    rows = load(args.db)
    results, prior, ntrain = run(rows, args.start_year, args.end_year)
    print('НАЦИОНАЛЬНЫЕ СБОРНЫЕ — офлайн-бэктест Elo V10')
    print('Источник:', args.db, '| записей:', len(rows))
    print('До периода теста:', ntrain, '| фиксированные 1/X/2 базовые частоты:', '/'.join(f'{x:.1%}' for x in prior))
    print('Предположения: Elo K=20; преимущество дома=65; ничьи=эвристика; командный рейтинг=1500.')
    print('Результаты одной даты НЕ используются для прогнозов на ту же дату.')
    print('НЕТ независимой валидации параметров и реальных предматчевых записей.')
    print('\n{:20} {:>7} | {:>23} | {:>23} | {:>8}'.format('Группа', 'Матчей', 'База Acc/LL/Brier', 'Elo Acc/LL/Brier', 'Δ LL'))
    print('-'*94)
    groups = [(str(y), [r for r in results if r['year'] == y]) for y in range(args.start_year, args.end_year + 1)]
    groups += [(name, [r for r in results if category(r['competition']) == name]) for name in ('Лига наций','Товарищеские','Отбор ЧМ','Отбор Евро','ЧМ','Другие')]
    groups.append(('ВСЕ', results))
    for name, group in groups:
        if not group: continue
        s = summary(group)
        baseline, elo = s['baseline'], s['elo']
        fmt = lambda x: f'{x[0]*100:5.1f}%/{x[1]:.4f}/{x[2]:.4f}'
        print(f'{name:20} {len(group):7} | {fmt(baseline):>23} | {fmt(elo):>23} | {baseline[1]-elo[1]:+8.4f}')
    print('\nΔ LL > 0: модель Elo лучше постоянных частот исходов. Это лишь базовое сравнение.')
    print('Турнирные категории определяются по тексту названия и могут требовать проверки.')
    print('Без сетевых запросов, без изменений V9/V7/Telegram или любой SQLite-базы.')

if __name__ == '__main__':
    main()
