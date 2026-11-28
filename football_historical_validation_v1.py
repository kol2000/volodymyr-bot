#!/usr/bin/env python3
"""Bubus Historical Validation V1.

Retrospective test on season 2025/26.

Models:
    BASELINE
    V1
    V2
    V3

STRICTLY READ ONLY / OFFLINE.

No database writes.
No live forecast recording.
No Telegram publication.
"""

import math
from collections import Counter, defaultdict
from datetime import date

import football_forecast_live_v7 as engine
import football_model_baseline_v1 as baseline_module


START = date(2025, 7, 1)
END = date(2026, 7, 1)

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


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def outcome(goals):
    h, a = goals
    return 0 if h > a else 1 if h == a else 2


def metrics(rows):

    if not rows:
        return None

    n = len(rows)
    brier = 0.0
    logloss = 0.0
    correct = 0

    for probabilities, actual in rows:

        require(
            len(probabilities) == 3
            and all(
                math.isfinite(p) and 0 <= p <= 1
                for p in probabilities
            )
            and abs(sum(probabilities) - 1) < 1e-6,
            "Invalid model probabilities",
        )

        brier += sum(
            (
                probabilities[i]
                - (1.0 if i == actual else 0.0)
            ) ** 2
            for i in range(3)
        )

        logloss -= math.log(
            max(probabilities[actual], 1e-15)
        )

        correct += (
            max(
                range(3),
                key=lambda i: probabilities[i]
            ) == actual
        )

    return (
        brier / n,
        logloss / n,
        correct / n,
    )


def main():

    print("=== BUBUS HISTORICAL VALIDATION V1 ===")
    print("MODE: READ ONLY / OFFLINE")
    print("TEST SEASON: 2025/26")

    matches, aliases, stats, skipped = (
        engine.load_matches(
            engine.HISTORY,
            engine.SOURCE,
            engine.MAPPING,
        )
    )

    require(
        all(
            matches[i].day <= matches[i + 1].day
            for i in range(len(matches) - 1)
        ),
        "Match history is not chronological",
    )

    targets = [
        m for m in matches
        if START <= m.day < END
        and m.league in LEAGUES
    ]

    print("\n=== SOURCE DATA ===")
    print("Total matches loaded:", len(matches))
    print("Historical targets:", len(targets))

    for league in LEAGUES:
        count = sum(
            m.league == league
            for m in targets
        )
        print(league, "targets:", count)

    require(
        bool(targets),
        "No historical target matches",
    )

    results = defaultdict(list)
    failures = Counter()

    groups = defaultdict(list)

    for match in targets:
        groups[(match.league, match.day)].append(match)

    # Precompute date cutoffs.
    # No same-day or future match can enter training.

    dates = sorted({
        day for league, day in groups
    })

    print("\nUnique match dates:", len(dates))

    cache = {}

    for day in dates:

        past = [
            m for m in matches
            if m.day < day
        ]

        ratings = engine.rating_before(
            matches,
            day,
        )

        cache[day] = (past, ratings)

    print("\n=== EVALUATING ===")

    for league, day in sorted(
        groups,
        key=lambda x: (x[1], x[0])
    ):

        past, ratings = cache[day]

        require(
            all(m.day < day for m in past),
            "Temporal cutoff violation",
        )

        base = baseline_module.baseline(
            matches,
            league,
            day,
        )

        for match in groups[(league, day)]:

            fixture = {
                "league": league,
                "day": day,
                "home_id": match.home,
                "away_id": match.away,
            }

            probabilities, reason = (
                engine.forecast_at(
                    fixture,
                    past,
                    ratings,
                )
            )

            if probabilities is None:

                failures[(league, reason)] += 1
                continue

            actual = outcome(match.goals)

            predictions = {
                "BASELINE": base,
                **probabilities,
            }

            for model in MODELS:
                results[(league, model)].append(
                    (predictions[model], actual)
                )

    print("\n=== RESULTS BY LEAGUE ===")

    for league in LEAGUES:

        print()
        print("=" * 68)
        print(league)
        print("=" * 68)

        target_count = sum(
            m.league == league
            for m in targets
        )

        evaluated = len(
            results[(league, "V3")]
        )

        print("Targets:", target_count)
        print("Evaluated:", evaluated)
        print("Skipped:", target_count - evaluated)

        for model in MODELS:

            values = metrics(
                results[(league, model)]
            )

            if values is None:
                print(model, "NO PREDICTIONS")
                continue

            brier, loss, accuracy = values

            print(
                f"{model:8s}"
                f" n={len(results[(league, model)]):4d}"
                f" Brier={brier:.4f}"
                f" LogLoss={loss:.4f}"
                f" Accuracy={accuracy:.1%}"
            )

        for (current, reason), count in failures.items():

            if current == league:
                print(
                    "Skip reason:",
                    count,
                    reason,
                )

    print("\n=== OVERALL ===")

    combined = {}

    for model in MODELS:

        rows = [
            row
            for league in LEAGUES
            for row in results[(league, model)]
        ]

        combined[model] = metrics(rows)

        if combined[model] is None:
            print(model, "NO PREDICTIONS")
            continue

        brier, loss, accuracy = combined[model]

        print(
            f"{model:8s}"
            f" n={len(rows):4d}"
            f" Brier={brier:.4f}"
            f" LogLoss={loss:.4f}"
            f" Accuracy={accuracy:.1%}"
        )

    print("\n=== COMPARISON ===")

    if (
        combined.get("BASELINE")
        and combined.get("V2")
        and combined.get("V3")
    ):

        base = combined["BASELINE"][0]
        v2 = combined["V2"][0]
        v3 = combined["V3"][0]

        print(
            "Baseline - V3 Brier:",
            round(base - v3, 6)
        )

        print(
            "V2 - V3 Brier:",
            round(v2 - v3, 6)
        )

        if v3 < v2:
            print("V3 beats V2 on historical Brier")
        else:
            print("V3 does not beat V2 on historical Brier")

    print("\n=== FINAL STATUS ===")

    print("HISTORICAL SEASON: 2025/26")
    print("DATE-CUTOFF VALIDATION COMPLETED")
    print("SAME-DAY RESULTS EXCLUDED")
    print("SOURCE AVAILABILITY AT CUTOFF: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
