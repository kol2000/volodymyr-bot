#!/usr/bin/env python3
"""Bubus Model Significance V1.

Paired, date-clustered bootstrap comparison of V2 and V3.

READ ONLY / OFFLINE.
No database writes.
No Telegram publication.
No forecast recording.
"""

import math
import random
from collections import defaultdict
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

ITERATIONS = 3000
SEED = 20261122


def outcome(goals):
    h, a = goals
    return 0 if h > a else 1 if h == a else 2


def brier(probabilities, actual):
    return sum(
        (p - (1.0 if i == actual else 0.0)) ** 2
        for i, p in enumerate(probabilities)
    )


def logloss(probabilities, actual):
    return -math.log(max(probabilities[actual], 1e-15))


def bootstrap(rows, metric, rng):
    """
    rows:
      (league, date, actual, V2 probabilities, V3 probabilities)

    Cluster by (league, date).
    Positive delta = V3 improvement.
    """

    groups = defaultdict(list)

    for league, day, actual, p2, p3 in rows:
        groups[(league, day)].append(
            metric(p2, actual) - metric(p3, actual)
        )

    clusters = list(groups.values())

    if not clusters:
        return None

    observed = (
        sum(sum(group) for group in clusters)
        / sum(len(group) for group in clusters)
    )

    samples = []

    for _ in range(ITERATIONS):

        selected = [
            clusters[rng.randrange(len(clusters))]
            for _ in range(len(clusters))
        ]

        delta = (
            sum(sum(group) for group in selected)
            / sum(len(group) for group in selected)
        )

        samples.append(delta)

    samples.sort()

    lower = samples[int(0.025 * ITERATIONS)]
    upper = samples[int(0.975 * ITERATIONS)]

    probability_positive = (
        sum(x > 0 for x in samples)
        / ITERATIONS
    )

    return (
        observed,
        lower,
        upper,
        probability_positive,
        len(clusters),
    )


def main():

    print("=== BUBUS MODEL SIGNIFICANCE V1 ===")
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

    rows = []
    failures = 0

    for match in targets:

        fixture = {
            "league": match.league,
            "day": match.day,
            "home_id": match.home,
            "away_id": match.away,
        }

        past = [
            m for m in matches
            if m.day < match.day
        ]

        ratings = engine.rating_before(
            matches,
            match.day,
        )

        predictions, reason = engine.forecast_at(
            fixture,
            past,
            ratings,
        )

        if predictions is None:
            failures += 1
            continue

        rows.append((
            match.league,
            match.day,
            outcome(match.goals),
            predictions["V2"],
            predictions["V3"],
        ))

    print("\nEvaluated:", len(rows))
    print("Skipped:", failures)

    if not rows:
        raise RuntimeError("No predictions")

    print("\n=== PAIRED BOOTSTRAP: V3 VS V2 ===")

    for league in (*LEAGUES, "ALL"):

        subset = (
            rows
            if league == "ALL"
            else [r for r in rows if r[0] == league]
        )

        if not subset:
            continue

        print()
        print(league, "| matches:", len(subset))

        for name, metric in (
            ("BRIER", brier),
            ("LOGLOSS", logloss),
        ):

            rng = random.Random(SEED)

            result = bootstrap(
                subset,
                metric,
                rng,
            )

            observed, low, high, positive, clusters = result

            print(
                f"{name:8s}"
                f" delta={observed:+.6f}"
                f" | 95% CI=[{low:+.6f}, {high:+.6f}]"
                f" | positive={positive:.1%}"
                f" | clusters={clusters}"
            )

    print("\n=== INTERPRETATION ===")

    print("Positive delta = V3 has lower error.")
    print("CI crossing zero = advantage not established.")
    print(
        "Positive fraction is a bootstrap stability"
        " measure, not a Bayesian posterior probability."
    )

    print("\n=== FINAL STATUS ===")

    print("PAIRED MODEL COMPARISON COMPLETED")
    print("DATE-CLUSTERED BOOTSTRAP")
    print("HISTORICAL DATA AVAILABILITY: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
