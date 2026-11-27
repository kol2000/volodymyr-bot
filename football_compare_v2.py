#!/usr/bin/env python3
"""Offline comparison of the untouched Bubus baseline with a league-adjusted model.

Uses previously downloaded openfootball JSON and the existing football_backtest.py.
Never edits bot code, SQLite, or service. Each prediction uses strictly earlier days.
The candidate is exploratory, with fixed parameters, not an optimized model.
"""
import argparse
from collections import defaultdict
from datetime import date
import math
from pathlib import Path

from football_backtest import (LEAGUES, SEASONS, OUTCOME, actual_outcome,
                               evaluate, loss, read_fixtures, stats)


def candidate_prediction(home, away, prior, league, when, rho=-0.08):
    """Season/league-normalized weighted Poisson with low-score DC correction.

    Input is ONLY past same-league games. Team venue-specific rates shrink toward
    league-wide means. Decay half-life ~120 days. No odds, xG or lineup claims.
    """
    half_life = 120.0
    past = [m for m in prior if m.league == league and m.day < when and (when - m.day).days <= 540]
    if len(past) < 90:
        return None
    def weight(m):
        return 2 ** (-(when - m.day).days / half_life)
    weights = [weight(m) for m in past]
    total = sum(weights)
    if total <= 0:
        return None
    league_home = sum(w * m.goals[0] for m, w in zip(past, weights)) / total
    league_away = sum(w * m.goals[1] for m, w in zip(past, weights)) / total
    league_home = max(0.2, league_home)
    league_away = max(0.2, league_away)

    def rate(team, venue):
        # Team stats at matching venue, with cross-venue information as a weak prior.
        subset = [(m, w) for m, w in zip(past, weights)
                  if (m.home if venue == 'home' else m.away) == team]
        broad = [(m, w) for m, w in zip(past, weights) if team in (m.home, m.away)]
        if len(broad) < 6 or len(subset) < 3:
            return None
        home = venue == 'home'
        def gf_ga(m):
            return m.goals if (m.home == team) else m.goals[::-1]
        broad_weight = sum(w for _, w in broad)
        broad_for = sum(gf_ga(m)[0] * w for m, w in broad) / broad_weight
        broad_against = sum(gf_ga(m)[1] * w for m, w in broad) / broad_weight
        # Relative-strength prior, blended with league venue goal rates.
        default_for = league_home if home else league_away
        default_against = league_away if home else league_home
        prior_for = 0.5 * broad_for + 0.5 * default_for
        prior_against = 0.5 * broad_against + 0.5 * default_against
        denom = sum(w for _, w in subset) + 5.0
        return ((sum(gf_ga(m)[0] * w for m, w in subset) + 5 * prior_for) / denom,
                (sum(gf_ga(m)[1] * w for m, w in subset) + 5 * prior_against) / denom)

    h = rate(home, 'home')
    a = rate(away, 'away')
    if h is None or a is None:
        return None
    # Multiplicative attack/defense ratios against league venue benchmarks.
    lh = max(.15, min(4.5, h[0] * a[1] / league_home))
    la = max(.15, min(4.5, a[0] * h[1] / league_away))
    ph = [math.exp(-lh) * lh**i / math.factorial(i) for i in range(13)]
    pa = [math.exp(-la) * la**i / math.factorial(i) for i in range(13)]
    out = [0., 0., 0.]
    best = (0., (0, 0))
    for i in range(13):
        for j in range(13):
            p = ph[i] * pa[j]
            if i == 0 and j == 0:
                p *= 1 - lh * la * rho
            elif i == 0 and j == 1:
                p *= 1 + lh * rho
            elif i == 1 and j == 0:
                p *= 1 + la * rho
            elif i == 1 and j == 1:
                p *= 1 - rho
            if p < 0:
                raise ValueError('Некорректная коррекция низких счетов')
            out[0 if i > j else 1 if i == j else 2] += p
            if p > best[0]:
                best = (p, (i, j))
    mass = sum(out)
    return tuple(v / mass for v in out)


def compare(fixtures, start, end, minimum=100):
    baseline, skipped = evaluate(fixtures, start, end)
    index = {(r['league'], r['date'], r['home'], r['away']): r for r in baseline}
    per_day = defaultdict(list)
    for m in fixtures:
        per_day[m.day].append(m)
    seen = []
    paired = []
    abstentions = defaultdict(int)
    for day in sorted(per_day):
        for m in per_day[day]:
            if not start <= day < end:
                continue
            k = (m.league, day.isoformat(), m.home, m.away)
            if k not in index:
                continue
            p = candidate_prediction(m.home, m.away, seen, m.league, day)
            if p is None:
                abstentions[m.league] += 1
                continue
            old = index[k]
            actual = actual_outcome(m)
            ll, bs = loss(p, actual)
            oldp = (old['p_home'], old['p_draw'], old['p_away'])
            oldll, oldbs = loss(oldp, actual)
            paired.append((m.league, p, oldp, actual, ll, bs, oldll, oldbs))
        # Commit only after processing every match that day.
        seen.extend(per_day[day])
    return paired, abstentions, len(baseline)


def report(paired, abstentions, n_baseline):
    print('Проверка альтернативной модели на ТОЧНО ТЕХ ЖЕ матчах, что и текущей')
    print(f'Всего прогнозов старой модели: {n_baseline}; парное сравнение: {len(paired)}')
    print('Параметры альтернативы фиксированы заранее; это эксперимент, не готовая замена.')
    print(f'{"Лига":14} {"Модель":15} {"Матчей":>7} {"Accuracy":>9} {"LogLoss":>9} {"Brier":>9}')
    print('-'*72)
    for league in [*LEAGUES, 'ВСЕ']:
        group = [r for r in paired if r[0] == league or league == 'ВСЕ']
        if not group: continue
        for tag, ix_p, ix_ll, ix_bs in [('Текущий', 2, 6, 7), ('Кандидат', 1, 4, 5)]:
            n = len(group)
            acc = sum(OUTCOME[max(range(3), key=lambda i: r[ix_p][i])] == r[3] for r in group)/n
            print(f'{league:14} {tag:15} {n:7} {acc:9.1%} {sum(r[ix_ll] for r in group)/n:9.4f} {sum(r[ix_bs] for r in group)/n:9.4f}')
    print('Нет прогноза кандидата по лигам:', dict(abstentions))
    print('Меньший LogLoss/Brier лучше. Не внедряйте кандидата без дополнительной проверки.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path('state/football_backtest/data'))
    parser.add_argument('--from-date', type=date.fromisoformat, default=date(2025, 7, 1))
    parser.add_argument('--to-date', type=date.fromisoformat, default=date(2026, 7, 1))
    args = parser.parse_args()
    fixtures, skipped = read_fixtures(args.data_dir, list(LEAGUES), list(SEASONS))
    print('Неполные исходные записи:', dict(skipped))
    paired, abstentions, n = compare(fixtures, args.from_date, args.to_date)
    report(paired, abstentions, n)

if __name__ == '__main__':
    main()
