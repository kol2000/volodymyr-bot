#!/usr/bin/env python3
"""Bubus Football Model Backtest V1.

Retrospective, date-cutoff evaluation of V1/V2/V3.

READ ONLY / OFFLINE.
No database writes.
No Telegram messages.
No live forecast recording.

Historical availability at prediction time
is NOT independently verified.
"""

import math
from collections import Counter, defaultdict
from datetime import date

import football_forecast_live_v7 as engine


LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

SEASON_START = date(2026, 7, 1)
SEASON_END = date(2027, 7, 1)

MODELS = ("V1", "V2", "V3")


def outcome(goals):
    home, away = goals

    if home > away:
        return 0
    if home == away:
        return 1
    return 2


def metrics(predictions):
    n = len(predictions)

    if not n:
        return None

    brier = 0.0
    logloss = 0.0
    correct = 0

    for probabilities, actual in predictions:

        target = [
            1.0 if i == actual else 0.0
            for i in range(3)
        ]

        brier += sum(
            (probabilities[i] - target[i]) ** 2
            for i in range(3)
        )

        logloss -= math.log(
            max(probabilities[actual], 1e-15)
        )

        predicted = max(
            range(3),
            key=lambda i: probabilities[i]
        )

        correct += predicted == actual

    return (
        brier / n,
        logloss / n,
        correct / n,
    )


def main():

    print("=== BUBUS MODEL BACKTEST V1 ===")
    print("MODE: RETROSPECTIVE / READ ONLY")

    matches, aliases, stats, skipped = (
        engine.load_matches(
            engine.HISTORY,
            engine.SOURCE,
            engine.MAPPING,
        )
    )

    print("\n=== DATA ===")
    print("All historical/current matches:", len(matches))
    print("Current CSV results:", dict(stats))

    targets = [
        m for m in matches
        if SEASON_START <= m.day < SEASON_END
        and m.league in LEAGUES
    ]

    print("Season 2026/27 targets:", len(targets))

    # Process the entire league/date group together.
    # This prevents results of matches on the
    # same calendar date entering each other's
    # predictions.

    groups = defaultdict(list)

    for match in targets:
        groups[
            (match.league, match.day)
        ].append(match)

    results = defaultdict(list)
    failures = Counter()
    processed = Counter()

    for league, day in sorted(
        groups,
        key=lambda x: (x[1], x[0])
    ):

        # The engine's load_matches() returns
        # chronological match order.

        past = [
            m for m in matches
            if m.day < day
        ]

        ratings = engine.rating_before(
            matches,
            day,
        )

        for match in groups[(league, day)]:

            processed[league] += 1

            fixture = {
                "league": league,
                "day": day,
                "home_id": match.home,
                "away_id": match.away,
            }

            try:
                probabilities, reason = (
                    engine.forecast_at(
                        fixture,
                        past,
                        ratings,
                    )
                )

            except Exception as exc:
                failures[
                    (league, "EXCEPTION")
                ] += 1

                print(
                    "ERROR:",
                    league,
                    day,
                    match.home,
                    match.away,
                    type(exc).__name__,
                    str(exc)[:200],
                )

                continue

            if probabilities is None:

                failures[
                    (league, reason or "UNKNOWN")
                ] += 1

                continue

            actual = outcome(match.goals)

            for model in MODELS:

                results[
                    (league, model)
                ].append(
                    (
                        probabilities[model],
                        actual,
                    )
                )

    print("\n=== RESULTS BY LEAGUE ===")

    for league in LEAGUES:

        print()
        print("=" * 65)
        print(league)
        print("=" * 65)

        print("Matches:", processed[league])

        for model in MODELS:

            rows = results[(league, model)]

            values = metrics(rows)

            if values is None:
                print(
                    model,
                    "| no predictions"
                )
                continue

            brier, logloss, accuracy = values

            print(
                f"{model}"
                f" | n={len(rows):3d}"
                f" | Brier={brier:.4f}"
                f" | LogLoss={logloss:.4f}"
                f" | Accuracy={accuracy:.1%}"
            )

        league_failures = [
            (reason, count)
            for (current, reason), count
            in failures.items()
            if current == league
        ]

        for reason, count in league_failures:
            print(
                "Skipped:",
                count,
                "|",
                reason,
            )

    print("\n=== OVERALL RESULTS ===")

    for model in MODELS:

        combined = []

        for league in LEAGUES:
            combined.extend(
                results[(league, model)]
            )

        values = metrics(combined)

        if values is None:
            print(model, "NO PREDICTIONS")
            continue

        brier, logloss, accuracy = values

        print(
            f"{model}"
            f" | n={len(combined)}"
            f" | Brier={brier:.4f}"
            f" | LogLoss={logloss:.4f}"
            f" | Accuracy={accuracy:.1%}"
        )

    print("\n=== COVERAGE ===")

    print(
        "Total target matches:",
        len(targets),
    )

    print(
        "Fully evaluated matches:",
        sum(
            len(results[(league, "V1")])
            for league in LEAGUES
        ),
    )

    print(
        "Skipped matches:",
        sum(failures.values()),
    )

    print("\n=== FINAL STATUS ===")
    print("RETROSPECTIVE DATE-CUTOFF TEST")
    print("HISTORICAL SOURCE AVAILABILITY: UNVERIFIED")
    print("SAME-DAY RESULTS: EXCLUDED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
