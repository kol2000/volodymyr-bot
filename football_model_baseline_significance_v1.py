#!/usr/bin/env python3
"""Bubus Baseline Significance V1.

Paired date-clustered bootstrap:
Baseline vs Forecast V3.

STRICTLY READ ONLY / OFFLINE.
"""

import random
from collections import Counter
from datetime import date

import football_forecast_live_v7 as engine
import football_model_significance_v1 as significance
import football_model_baseline_v1 as baseline_module


LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

START = date(2026, 7, 1)
END = date(2027, 7, 1)

SEED = 20261123


def main():

    print("=== BUBUS BASELINE SIGNIFICANCE V1 ===")
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
    failures = Counter()

    # Cache calculations by calendar date.
    # Only previous dates are allowed.

    cached = {}

    for match in targets:

        day = match.day
        league = match.league

        if day not in cached:

            past = [
                m for m in matches
                if m.day < day
            ]

            ratings = engine.rating_before(
                matches,
                day,
            )

            cached[day] = (past, ratings)

        past, ratings = cached[day]

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

        base = baseline_module.baseline(
            matches,
            league,
            day,
        )

        actual = significance.outcome(
            match.goals
        )

        rows.append((
            league,
            day,
            actual,
            base,
            probabilities["V3"],
        ))

    print("\n=== COVERAGE ===")

    print("Targets:", len(targets))
    print("Evaluated:", len(rows))
    print("Skipped:", sum(failures.values()))

    if len(rows) != 423:
        raise RuntimeError(
            "Unexpected evaluated count; "
            "check source data changes"
        )

    print("\n=== V3 VS BASELINE ===")

    for league in (*LEAGUES, "ALL"):

        subset = (
            rows if league == "ALL"
            else [r for r in rows if r[0] == league]
        )

        if not subset:
            continue

        print()
        print(
            league,
            "| matches:",
            len(subset),
        )

        for name, metric in (
            ("BRIER", significance.brier),
            ("LOGLOSS", significance.logloss),
        ):

            result = significance.bootstrap(
                subset,
                metric,
                random.Random(SEED),
            )

            observed, low, high, positive, clusters = (
                result
            )

            print(
                f"{name:8s}"
                f" delta={observed:+.6f}"
                f" | 95% CI=[{low:+.6f}, {high:+.6f}]"
                f" | positive={positive:.1%}"
                f" | clusters={clusters}"
            )

    print("\n=== INTERPRETATION ===")

    print(
        "Positive delta = V3 improves "
        "over baseline."
    )

    print(
        "CI entirely above zero = "
        "positive effect under this "
        "bootstrap procedure."
    )

    print(
        "Historical source availability "
        "at prediction time is unverified."
    )

    print("\n=== FINAL STATUS ===")
    print("PAIRED COMPARISON COMPLETED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
