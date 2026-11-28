#!/usr/bin/env python3
"""Bubus Full Probability Calibration V1.

Evaluate V3 calibration for:
HOME / DRAW / AWAY.

Seasons:
2024/25, 2025/26, 2026/27.

STRICTLY READ ONLY / OFFLINE.
"""

from collections import Counter, defaultdict
from datetime import date

import football_forecast_live_v7 as engine


SEASONS = {
    "2024/25": (date(2024, 7, 1), date(2025, 7, 1)),
    "2025/26": (date(2025, 7, 1), date(2026, 7, 1)),
    "2026/27": (date(2026, 7, 1), date(2027, 7, 1)),
}

LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

OUTCOMES = ("HOME", "DRAW", "AWAY")


def outcome(goals):
    h, a = goals
    return 0 if h > a else 1 if h == a else 2


def summarize(rows, idx):
    if not rows:
        return None

    n = len(rows)

    predicted = sum(p[idx] for p, y in rows) / n
    actual = sum(y == idx for p, y in rows) / n

    brier = sum(
        (p[idx] - (y == idx)) ** 2
        for p, y in rows
    ) / n

    return n, predicted, actual, predicted-actual, brier


def main():

    print("=== BUBUS FULL PROBABILITY CALIBRATION ===")
    print("MODE: READ ONLY / OFFLINE")

    matches, _, _, _ = engine.load_matches(
        engine.HISTORY,
        engine.SOURCE,
        engine.MAPPING,
    )

    assert all(
        matches[i].day <= matches[i+1].day
        for i in range(len(matches)-1)
    ), "History not sorted"

    rows = defaultdict(list)
    failures = Counter()
    cache = {}

    for match in matches:

        season = next(
            (
                name for name, (start, end) in SEASONS.items()
                if start <= match.day < end
            ),
            None,
        )

        if season is None or match.league not in LEAGUES:
            continue

        day = match.day

        if day not in cache:
            past = [m for m in matches if m.day < day]
            ratings = engine.rating_before(matches, day)
            cache[day] = (past, ratings)

        past, ratings = cache[day]

        fixture = {
            "league": match.league,
            "day": day,
            "home_id": match.home,
            "away_id": match.away,
        }

        probabilities, reason = engine.forecast_at(
            fixture,
            past,
            ratings,
        )

        if probabilities is None:
            failures[season] += 1
            continue

        p = probabilities["V3"]
        y = outcome(match.goals)

        rows[season].append((p, y))

    for season in SEASONS:

        data = rows[season]

        print()
        print("=" * 65)
        print("SEASON:", season)
        print("MATCHES:", len(data))
        print("SKIPPED:", failures[season])
        print("=" * 65)

        print("\n=== OUTCOME CALIBRATION ===")

        for idx, label in enumerate(OUTCOMES):

            value = summarize(data, idx)

            if value is None:
                continue

            n, predicted, actual, diff, brier = value

            print(
                f"{label:5s}"
                f" predicted={predicted:6.2%}"
                f" actual={actual:6.2%}"
                f" diff={diff:+6.2%}"
                f" binary_Brier={brier:.4f}"
            )

        print("\n=== CONFIDENCE OF TOP PREDICTION ===")

        confidence_groups = defaultdict(list)

        for p, y in data:

            predicted_outcome = max(
                range(3),
                key=lambda i: p[i],
            )

            confidence = p[predicted_outcome]

            bucket = min(int(confidence * 10), 9)

            confidence_groups[bucket].append(
                (
                    confidence,
                    int(predicted_outcome == y),
                )
            )

        print(
            "Range    N     Mean confidence"
            "    Accuracy    Difference"
        )

        for bucket in sorted(confidence_groups):

            subset = confidence_groups[bucket]
            n = len(subset)

            avg_conf = sum(x for x, y in subset) / n
            accuracy = sum(y for x, y in subset) / n

            print(
                f"{bucket*10:02d}-{(bucket+1)*10:02d}%"
                f"  {n:4d}"
                f"       {avg_conf:6.2%}"
                f"        {accuracy:6.2%}"
                f"      {avg_conf-accuracy:+6.2%}"
            )

            if n < 30:
                print("  WARNING: small bucket")

    print("\n=== TOTAL COVERAGE ===")

    print(
        "Evaluated:",
        sum(len(x) for x in rows.values()),
    )

    print(
        "Skipped:",
        sum(failures.values()),
    )

    print("\n=== FINAL STATUS ===")

    print("FULL PROBABILITY CALIBRATION COMPLETED")
    print("SAME-DAY RESULTS EXCLUDED")
    print("NO PARAMETERS CHANGED")
    print("HISTORICAL AVAILABILITY: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
