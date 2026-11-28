#!/usr/bin/env python3
"""Offline, pre-match backtest of the EXISTING football_predictions.estimate().

Downloads optional openfootball/football.json CC0 league results into a separate
cache. Never imports or changes the running bot, its database or services.

For each historical match, uses *only earlier dates* from the same division,
within the most recent 180 days, max eight matches per team, min five.
This mimics the input selection of the current FNK-based Poisson predictor,
while testing it on an independent, structured results dataset. The backtest
is not a replay of the live FNK parser or its fallback Qwen outputs.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

LEAGUES = {
    'EPL': 'en.1',
    'LaLiga': 'es.1',
    'Bundesliga': 'de.1',
    'SerieA': 'it.1',
    'Ligue1': 'fr.1',
}
SEASONS = ('2023-24', '2024-25', '2025-26')
SOURCE = 'https://raw.githubusercontent.com/openfootball/football.json/master'
OUTCOME = ('H', 'D', 'A')
FIELDS = ('date', 'league', 'home', 'away', 'score', 'predicted', 'actual',
          'p_home', 'p_draw', 'p_away', 'b_home', 'b_draw', 'b_away',
          'correct', 'log_loss', 'brier', 'baseline_log_loss', 'baseline_brier')


@dataclass(frozen=True)
class Fixture:
    day: date
    league: str
    home: str
    away: str
    goals: tuple[int, int]


def download_missing(directory: Path, leagues: list[str], seasons: list[str], refresh=False):
    """Only explicit --download fetches new files. Cached data stays untouched."""
    directory.mkdir(parents=True, exist_ok=True)
    for league in leagues:
        for season in seasons:
            path = directory / f'{league}_{season}.json'
            if path.exists() and not refresh:
                continue
            url = f'{SOURCE}/{season}/{LEAGUES[league]}.json'
            print(f'Получаю: {league} {season}', flush=True)
            request = urllib.request.Request(url, headers={
                'User-Agent': 'bubus-football-backtest/1.0',
                'Accept': 'application/json',
            })
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    final = urlsplit(response.url)
                    if final.scheme != 'https' or final.netloc != 'raw.githubusercontent.com':
                        raise ValueError(f'Неожиданный адрес ответа: {final.netloc}')
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        raise ValueError(f'Слишком большой файл: {url}')
                payload = json.loads(body.decode('utf-8-sig'))
                if not isinstance(payload, dict) or not isinstance(payload.get('matches'), list):
                    raise ValueError(f'Некорректный формат: {url}')
                tmp = path.with_suffix('.json.part')
                tmp.write_bytes(body)
                tmp.replace(path)
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f'Не удалось получить {url}: {exc}') from exc


def read_fixtures(directory: Path, leagues: list[str], seasons: list[str]):
    fixtures = []
    seen = {}
    skipped = Counter()
    for league in leagues:
        for season in seasons:
            path = directory / f'{league}_{season}.json'
            if not path.is_file():
                raise FileNotFoundError(
                    f'Нет {path}. Запустите скрипт с --download или поместите данные туда вручную.')
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('matches'), list):
                raise ValueError(f'Неверный формат в {path}')
            for match in data['matches']:
                if not isinstance(match, dict):
                    skipped[league] += 1
                    continue
                try:
                    match_date = date.fromisoformat(match['date'])
                    home, away = match['team1'].strip(), match['team2'].strip()
                    raw_score = match.get('score')
                    score = raw_score.get('ft') if isinstance(raw_score, dict) else raw_score
                    if (not home or not away or home == away or not isinstance(score, list)
                            or len(score) != 2 or any(type(x) is not int or x < 0 or x > 15 for x in score)):
                        skipped[league] += 1
                        continue
                except (KeyError, ValueError, TypeError, AttributeError):
                    skipped[league] += 1
                    continue
                item = Fixture(match_date, league, home, away, (score[0], score[1]))
                key = (league, match_date, home, away)
                if key in seen:
                    if seen[key] != item.goals:
                        raise ValueError(f'Противоречащие результаты одного матча: {key}')
                    continue
                seen[key] = item.goals
                fixtures.append(item)
    return sorted(fixtures, key=lambda m: (m.day, m.league, m.home, m.away)), skipped


def actual_outcome(match):
    return 'H' if match.goals[0] > match.goals[1] else ('A' if match.goals[0] < match.goals[1] else 'D')


def loss(probs, actual):
    """Multiclass Brier = sum_k(p_k - y_k)^2; log loss uses a finite floor."""
    index = OUTCOME.index(actual)
    return (-math.log(max(probs[index], 1e-12)),
            sum((p - int(i == index)) ** 2 for i, p in enumerate(probs)))


def evaluate(fixtures, start, end, predictor=None, canonical=None):
    """Strictly chronological: same-day final scores enter history only next day.

    'predictor' can be substituted by test doubles in unit tests. In production
    it is imported from the user's actual unmodified football_predictions.py.
    """
    if predictor is None or canonical is None:
        from football_predictions import canonical as real_canonical, estimate
        predictor = predictor or estimate
        canonical = canonical or real_canonical
    history = defaultdict(lambda: defaultdict(list))
    league_counts = defaultdict(Counter)
    output = []
    reasons = Counter()
    fixtures = sorted(fixtures, key=lambda m: (m.day, m.league, m.home, m.away))
    last_date = None
    batch = []

    def commit_batch():
        for m in batch:
            row = dict(day=m.day.isoformat(), home=canonical(m.home),
                       away=canonical(m.away), score=m.goals)
            for team in (row['home'], row['away']):
                history[m.league][team].append(row)
            league_counts[m.league][actual_outcome(m)] += 1

    for m in fixtures:
        if last_date is not None and m.day != last_date:
            commit_batch()
            batch = []
        last_date = m.day
        if not start <= m.day < end:
            batch.append(m)
            continue
        def recent(team):
            prior = history[m.league][canonical(team)]
            return [r for r in reversed(prior)
                    if 0 < (m.day - date.fromisoformat(r['day'])).days <= 180][:8]
        h, a = recent(m.home), recent(m.away)
        if min(len(h), len(a)) < 5:
            reasons[(m.league, 'мало истории')] += 1
            batch.append(m)
            continue
        try:
            pred = predictor(m.home, m.away, h, a)
            p = tuple(float(x) / 100 for x in pred['probabilities'])
            if len(p) != 3 or any(not math.isfinite(x) or x < 0 or x > 1 for x in p) or abs(sum(p) - 1) > 1e-6:
                raise ValueError('Некорректные вероятности')
        except Exception as exc:
            # Don't silently hide malformed models; report details and halt.
            raise RuntimeError(f'Прогноз не удался: {m.day} {m.league} {m.home} — {m.away}: {exc}') from exc
        total = sum(league_counts[m.league].values())
        b = tuple((league_counts[m.league][outcome] + 1) / (total + 3) for outcome in OUTCOME)
        actual = actual_outcome(m)
        guessed = OUTCOME[max(range(3), key=lambda i: p[i])]
        ll, bs = loss(p, actual)
        bll, bbs = loss(b, actual)
        output.append(dict(date=m.day.isoformat(), league=m.league, home=m.home,
                           away=m.away, score=f'{m.goals[0]}:{m.goals[1]}',
                           predicted=guessed, actual=actual, p_home=p[0], p_draw=p[1],
                           p_away=p[2], b_home=b[0], b_draw=b[1], b_away=b[2],
                           correct=int(guessed == actual), log_loss=ll, brier=bs,
                           baseline_log_loss=bll, baseline_brier=bbs))
        batch.append(m)
    return output, reasons


def stats(rows, baseline=False):
    if not rows:
        return None
    n = len(rows)
    if baseline:
        accuracy = sum(OUTCOME[max(range(3), key=lambda i: r[('b_home', 'b_draw', 'b_away')[i]])] == r['actual'] for r in rows)/n
        return (n, accuracy, sum(r['baseline_log_loss'] for r in rows)/n,
                sum(r['baseline_brier'] for r in rows)/n)
    return (n, sum(r['correct'] for r in rows)/n,
            sum(r['log_loss'] for r in rows)/n, sum(r['brier'] for r in rows)/n)


def summarize(rows, reasons, fixtures, start, end):
    print(f'\nПРОВЕРКА: {start} — {end} (конец не включается)')
    print(f'Загружено завершённых матчей: {len(fixtures)}')
    print(f'Прогнозов сделано: {len(rows)}; пропущено из-за недостатка истории: {sum(reasons.values())}')
    if not rows:
        print('Недостаточно исторических данных: сравнивать точность пока нельзя.')
        return
    print(f'\n{"Чемпионат":14} {"Модель":12} {"Матчей":>8} {"Accuracy":>10} {"LogLoss":>10} {"Brier":>9}')
    print('-' * 71)
    groups = defaultdict(list)
    for row in rows:
        groups[row['league']].append(row)
    groups['ВСЕ'] = rows
    for league, group in groups.items():
        for label, baseline in [('Бубус', False), ('База', True)]:
            n, acc, ll, bs = stats(group, baseline)
            print(f'{league:14} {label:12} {n:8d} {acc:9.1%} {ll:10.4f} {bs:9.4f}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Исторический тест текущего Пуассона Бубуса')
    parser.add_argument('--download', action='store_true', help='скачать только отсутствующие файлы openfootball')
    parser.add_argument('--refresh', action='store_true', help='обновить существующие JSON (только с --download)')
    parser.add_argument('--data-dir', type=Path, default=Path('state/football_backtest/data'))
    parser.add_argument('--output', type=Path, default=Path('state/football_backtest/report_2025_26.csv'))
    parser.add_argument('--from-date', type=date.fromisoformat, default=date(2025, 7, 1))
    parser.add_argument('--to-date', type=date.fromisoformat, default=date(2026, 7, 1))
    parser.add_argument('--leagues', nargs='+', choices=tuple(LEAGUES), default=list(LEAGUES))
    parser.add_argument('--seasons', nargs='+', default=list(SEASONS))
    args = parser.parse_args(argv)
    if args.refresh and not args.download:
        parser.error('--refresh используется только с --download')
    if args.to_date <= args.from_date:
        parser.error('Конечная дата должна быть позже начальной')
    for season in args.seasons:
        if not (len(season) == 7 and season[4] == '-' and season[:4].isdigit()
                and season[5:].isdigit() and int(season[5:]) == (int(season[:4]) + 1) % 100):
            parser.error(f'Некорректный сезон: {season}')
    try:
        if args.download:
            download_missing(args.data_dir, args.leagues, args.seasons, args.refresh)
        fixtures, skipped = read_fixtures(args.data_dir, args.leagues, args.seasons)
        if skipped:
            print('Пропущены записи без подтверждённого итогового счёта:', dict(skipped))
        rows, reasons = evaluate(fixtures, args.from_date, args.to_date)
        summarize(rows, reasons, fixtures, args.from_date, args.to_date)
        if rows:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('w', encoding='utf-8-sig', newline='') as fp:
                writer = csv.DictWriter(fp, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            print(f'\nCSV-отчёт: {args.output}')
        return 0 if rows else 2
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f'ОШИБКА: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
