#!/usr/bin/env python3
"""Read-only ablation: isolate Dixon-Coles and Elo contributions on matched games.

Exploratory comparisons only: historical years have already influenced model design.
All features/ratings are frozen before the first kickoff of each calendar day.
"""
import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

from football_backtest import LEAGUES, SEASONS, OUTCOME, actual_outcome, evaluate, loss, read_fixtures
from football_compare_v2 import candidate_prediction
from football_compare_v3 import elo_probabilities, update_rating

VARIANTS = (
    ('V1', 'Исходный Пуассон'),
    ('V2_noDC', 'V2 без Dixon-Coles'),
    ('V2_DC', 'V2 с Dixon-Coles'),
    ('V3_Elo10', 'V2 + Elo 10%'),
    ('V3_Elo25', 'V2 + Elo 25%'),
)


def compare(fixtures, start, end):
    baseline, _ = evaluate(fixtures, start, end)
    index = {(r['league'], r['date'], r['home'], r['away']): r for r in baseline}
    days = defaultdict(list)
    for match in fixtures:
        days[match.day].append(match)
    ratings = defaultdict(lambda: 1500.0)
    history = []
    rows = []
    skipped = defaultdict(int)
    for day in sorted(days):
        # Compute entire day before incorporating any results from it.
        for m in days[day]:
            if not start <= day < end:
                continue
            original = index.get((m.league, day.isoformat(), m.home, m.away))
            if original is None:
                continue
            no_dc = candidate_prediction(m.home, m.away, history, m.league, day, rho=0.0)
            dc = candidate_prediction(m.home, m.away, history, m.league, day, rho=-0.08)
            if no_dc is None or dc is None:
                skipped[m.league] += 1
                continue
            p1 = (original['p_home'], original['p_draw'], original['p_away'])
            rh, ra = ratings[(m.league, m.home)], ratings[(m.league, m.away)]
            probs = {
                'V1': p1,
                'V2_noDC': no_dc,
                'V2_DC': dc,
                'V3_Elo10': elo_probabilities(dc, rh, ra, blend=.10),
                'V3_Elo25': elo_probabilities(dc, rh, ra, blend=.25),
            }
            rows.append((m.league, m.day, actual_outcome(m), probs))
        for m in days[day]:
            update_rating(ratings, m)
        history.extend(days[day])
    return rows, dict(skipped), len(baseline)


def metrics(rows, key):
    n = len(rows)
    if not n:
        return None
    accuracy = sum(OUTCOME[max(range(3), key=lambda i: probs[key][i])] == actual
                   for _, _, actual, probs in rows) / n
    scores = [loss(probs[key], actual) for _, _, actual, probs in rows]
    return accuracy, sum(s[0] for s in scores) / n, sum(s[1] for s in scores) / n


def report(rows, skipped, baseline_total):
    print(f'Прогнозов V1: {baseline_total}; общая сравнимая выборка: {len(rows)}; пропуски: {skipped}')
    print('Параметры заданы до этого запуска; годы уже участвовали в разработке. НЕ независимый тест.')
    print(f'{"Лига":13} {"Вариант":24} {"Матчей":>7} {"Accuracy":>9} {"LogLoss":>9} {"Brier":>9} {"ΔLL к V2":>10}')
    print('-'*91)
    for league in [*LEAGUES, 'ВСЕ']:
        subset = [r for r in rows if league == 'ВСЕ' or r[0] == league]
        if not subset: continue
        baseline = metrics(subset, 'V2_DC')[1]
        for key, label in VARIANTS:
            acc, ll, bs = metrics(subset, key)
            print(f'{league:13} {label:24} {len(subset):7} {acc:9.1%} {ll:9.4f} {bs:9.4f} {baseline-ll:+10.4f}')
        print()
    print('ΔLL к V2 > 0 означает, что вариант лучше V2; меньший LogLoss/Brier лучше.')
    print('Вариант без DC изолирует вклад коррекции при неизменной остальной формуле.')
    print('Elo 10% и 25% проверяют чувствительность к весу, а не выбирают лучший вес для продакшена.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path('state/football_backtest/data'))
    parser.add_argument('--from-date', type=date.fromisoformat, default=date(2025, 7, 1))
    parser.add_argument('--to-date', type=date.fromisoformat, default=date(2026, 7, 1))
    args = parser.parse_args()
    fixtures, incomplete = read_fixtures(args.data_dir, list(LEAGUES), list(SEASONS))
    print('Неполные исходные записи:', dict(incomplete))
    rows, skipped, total = compare(fixtures, args.from_date, args.to_date)
    report(rows, skipped, total)

if __name__ == '__main__':
    main()
