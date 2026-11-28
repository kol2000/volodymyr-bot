#!/usr/bin/env python3
"""Offline paired experiment: existing Poisson, V2, and V2 + pre-match Elo.

Elo probabilities are a conservative blend, not a trained model. No modifications
of the Telegram bot. All same-day matches are evaluated before ratings update.
"""
import argparse
from collections import defaultdict
from datetime import date
import math
from pathlib import Path

from football_backtest import LEAGUES, SEASONS, OUTCOME, evaluate, loss, read_fixtures, actual_outcome
from football_compare_v2 import candidate_prediction

HOME_ELO = 65.0
K = 20.0
BLEND = .25


def expected_home(home_rating, away_rating):
    return 1.0 / (1.0 + 10 ** ((away_rating - home_rating - HOME_ELO) / 400.0))


def elo_probabilities(p2, home_rating, away_rating, blend=BLEND):
    """Retain V2 draw probability and blend conditional decisive-outcome Elo.

    Elo's binary expected score includes draws, so treating it as P(home win)
    would be mathematically incorrect. This is a heuristic challenger only.
    """
    decisive = 1.0 - p2[1]
    elo_h = expected_home(home_rating, away_rating)
    # Convert Elo expected points to a tentative decisive-outcome split.
    # Rating update uses expected points, while prediction is explicitly heuristic.
    elo = (decisive * elo_h, p2[1], decisive * (1.0 - elo_h))
    out = tuple((1 - blend) * p2[i] + blend * elo[i] for i in range(3))
    assert abs(sum(out) - 1) < 1e-9 and min(out) >= 0
    return out


def update_rating(ratings, match):
    """Update after match, based on actual points (win=1, draw=.5, loss=0)."""
    league = match.league
    home_key, away_key = (league, match.home), (league, match.away)
    rh, ra = ratings[home_key], ratings[away_key]
    observed = 1.0 if match.goals[0] > match.goals[1] else (0.5 if match.goals[0] == match.goals[1] else 0.0)
    delta = K * (observed - expected_home(rh, ra))
    ratings[home_key] = rh + delta
    ratings[away_key] = ra - delta


def compare_three(fixtures, start, end):
    baseline, _ = evaluate(fixtures, start, end)
    index = {(r['league'], r['date'], r['home'], r['away']): r for r in baseline}
    by_day = defaultdict(list)
    for m in fixtures:
        by_day[m.day].append(m)
    ratings = defaultdict(lambda: 1500.0)
    history = []
    pairs = []
    skipped = defaultdict(int)
    for day in sorted(by_day):
        for m in by_day[day]:
            if not start <= day < end:
                continue
            key = (m.league, day.isoformat(), m.home, m.away)
            old = index.get(key)
            if old is None:
                continue
            p2 = candidate_prediction(m.home, m.away, history, m.league, day)
            if p2 is None:
                skipped[m.league] += 1
                continue
            p1 = (old['p_home'], old['p_draw'], old['p_away'])
            p3 = elo_probabilities(p2, ratings[m.league, m.home], ratings[m.league, m.away])
            pairs.append((m.league, actual_outcome(m), p1, p2, p3))
        # Same-day score leakage prevention: update AFTER all predictions.
        for m in by_day[day]:
            update_rating(ratings, m)
        history.extend(by_day[day])
    return pairs, skipped, len(baseline)


def report(rows, skipped, total):
    print('Парный эксперимент: Пуассон V1 / Dixon-Coles V2 / V2 + Elo V3')
    print(f'Прогнозов V1: {total}; сравнимых матчей: {len(rows)}')
    print(f'Elo: K={K:g}, преимущество дома={HOME_ELO:g}, доля смешивания={BLEND:g}. Параметры НЕ обучены.')
    print(f'{"Лига":14} {"Модель":15} {"Матчей":>7} {"Accuracy":>9} {"LogLoss":>9} {"Brier":>9}')
    print('-' * 72)
    for league in [*LEAGUES, 'ВСЕ']:
        group = [r for r in rows if r[0] == league or league == 'ВСЕ']
        if not group:
            continue
        for title, ix in [('Текущий V1', 2), ('Кандидат V2', 3), ('V2 + Elo V3', 4)]:
            n = len(group)
            correct = sum(OUTCOME[max(range(3), key=lambda j: r[ix][j])] == r[1] for r in group)
            scores = [loss(r[ix], r[1]) for r in group]
            print(f'{league:14} {title:15} {n:7} {correct/n:9.1%} {sum(s[0] for s in scores)/n:9.4f} {sum(s[1] for s in scores)/n:9.4f}')
    print('V2/V3 пропуски на матчах V1:', dict(skipped))
    print('ВНИМАНИЕ: 2024/25 и 2025/26 уже использованы при разработке моделей; это исследование, не независимый финальный тест.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', type=Path, default=Path('state/football_backtest/data'))
    p.add_argument('--from-date', type=date.fromisoformat, default=date(2025, 7, 1))
    p.add_argument('--to-date', type=date.fromisoformat, default=date(2026, 7, 1))
    args = p.parse_args()
    fixtures, incomplete = read_fixtures(args.data_dir, list(LEAGUES), list(SEASONS))
    print('Неполные исходные записи:', dict(incomplete))
    rows, skipped, total = compare_three(fixtures, args.from_date, args.to_date)
    report(rows, skipped, total)

if __name__ == '__main__':
    main()
