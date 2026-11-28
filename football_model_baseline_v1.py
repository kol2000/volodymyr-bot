#!/usr/bin/env python3
"""Bubus Model Baseline Comparison V1.

Compare V1/V2/V3 with a historical
league-outcome-frequency baseline.

READ ONLY / OFFLINE.
No database writes.
No forecast recording.
No Telegram publication.
"""

import math
from collections import Counter
from datetime import date

import football_forecast_live_v7 as engine


START = date(2026, 7, 1)
END = date(2027, 7, 1)

LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

MODELS = (
    "BASELINE",
    "V1",
    "V2",
    "V3",
)


def outcome(goals):
    h, a = goals
    return 0 if h > a else 1 if h == a else 2


def baseline(matches, league, day):

    previous = [
        m for m in matches
        if m.league == league
        and m.day < day
        and (day - m.day).days <= 540
    ]

    counts = [1, 1, 1]

    for match in previous:
        counts[outcome(match.goals)] += 1

    total = sum(counts)

    return tuple(
        count / total
        for count in counts
    )


def metrics(rows):

    if not rows:
        return None

    n = len(rows)

    brier = 0
    logloss = 0
    correct = 0

    for probabilities, actual in rows:

        brier += sum(
            (probabilities[i] -
             (1 if i == actual else 0)) ** 2
            for i in range(3)
        )

        logloss -= math.log(
            max(probabilities[actual], 1e-15)
        )

        correct += (
            max(range(3),
                key=lambda i: probabilities[i])
            == actual
        )

    return (
        brier / n,
        logloss / n,
        correct / n,
    )


def main():

    print("=== BUBUS BASELINE COMPARISON V1 ===")
    print("MODE: RETROSPECTIVE / READ ONLY")

    matches, aliases, stats, skipped = (
        engine.load_matches(
            engine.HISTORY,
            engine.SOURCE,
            engine.MAPPING,
        )
    )

    targets = [
        m for m in matches
        if START <= m.day < END
        and m.league in LEAGUES
    ]

    results = {
        league: {
            model: []
            for model in MODELS
        }
        for league in LEAGUES
    }

    skipped_matches = Counter()

    for match in targets:

        league = match.league
        day = match.day

        past = [
            m for m in matches
            if m.day < day
        ]

        ratings = engine.rating_before(
            matches,
            day,
        )

        fixture = {
            "league": league,
            "day": day,
            "home_id": match.home,
            "away_id": match.away,
        }

        predictions, reason = (
            engine.forecast_at(
                fixture,
                past,
                ratings,
            )
        )

        if predictions is None:
            skipped_matches[league] += 1
            continue

        predictions["BASELINE"] = (
            baseline(matches, league, day)
        )

        actual = outcome(match.goals)

        for model in MODELS:

            results[league][model].append(
                (predictions[model], actual)
            )

    print("\n=== RESULTS BY LEAGUE ===")

    for league in LEAGUES:

        print()
        print(league)

        for model in MODELS:

            rows = results[league][model]

            values = metrics(rows)

            if values is None:
                print(model, "NO RESULTS")
                continue

            brier, logloss, accuracy = values

            print(
                f"{model:8s}"
                f" n={len(rows):3d}"
                f" Brier={brier:.4f}"
                f" LogLoss={logloss:.4f}"
                f" Accuracy={accuracy:.1%}"
            )

        print(
            "Skipped:",
            skipped_matches[league],
        )

    print("\n=== OVERALL ===")

    for model in MODELS:

        combined = [
            row
            for league in LEAGUES
            for row in results[league][model]
        ]

        values = metrics(combined)

        if values is None:
            print(model, "NO RESULTS")
            continue

        brier, logloss, accuracy = values

        print(
            f"{model:8s}"
            f" n={len(combined)}"
            f" Brier={brier:.4f}"
            f" LogLoss={logloss:.4f}"
            f" Accuracy={accuracy:.1%}"
        )

    print("\n=== FINAL STATUS ===")
    print("BASELINE COMPARISON COMPLETED")
    print("SAME-DAY RESULTS EXCLUDED")
    print("HISTORICAL AVAILABILITY: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
