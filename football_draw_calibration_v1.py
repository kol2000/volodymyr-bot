#!/usr/bin/env python3
"""Bubus Draw Calibration Audit V1.

Evaluate draw probability calibration
for seasons 2025/26 and 2026/27.

READ ONLY / OFFLINE.

No parameter tuning.
No database writes.
No forecast recording.
No Telegram messages.
"""

from collections import Counter, defaultdict
from datetime import date

import football_forecast_live_v7 as engine


SEASONS = {
    "2025/26": (
        date(2025, 7, 1),
        date(2026, 7, 1),
    ),
    "2026/27": (
        date(2026, 7, 1),
        date(2027, 7, 1),
    ),
}

LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def draw_metrics(rows):

    n = len(rows)

    if not n:
        return None

    predicted = sum(p for p, y in rows) / n
    actual = sum(y for p, y in rows) / n

    brier = sum(
        (p - y) ** 2
        for p, y in rows
    ) / n

    return n, predicted, actual, brier


def print_metrics(rows):

    values = draw_metrics(rows)

    if values is None:
        print("No observations")
        return

    n, predicted, actual, brier = values

    print(
        f"n={n:4d}"
        f" | predicted={predicted:6.2%}"
        f" | actual={actual:6.2%}"
        f" | difference={predicted-actual:+6.2%}"
        f" | binary_Brier={brier:.4f}"
    )


def main():

    print("=== BUBUS DRAW CALIBRATION V1 ===")
    print("MODE: READ ONLY / OFFLINE")

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
        "History is not chronological",
    )

    all_rows = []
    failures = Counter()

    date_cache = {}

    for match in matches:

        season_name = None

        for name, (start, end) in SEASONS.items():

            if start <= match.day < end:
                season_name = name
                break

        if season_name is None:
            continue

        if match.league not in LEAGUES:
            continue

        day = match.day

        if day not in date_cache:

            past = [
                m for m in matches
                if m.day < day
            ]

            ratings = engine.rating_before(
                matches,
                day,
            )

            date_cache[day] = (past, ratings)

        past, ratings = date_cache[day]

        fixture = {
            "league": match.league,
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
            failures[(season_name, reason)] += 1
            continue

        p2 = probabilities["V2"][1]
        p3 = probabilities["V3"][1]

        require(
            abs(p2 - p3) < 1e-12,
            "V2/V3 draw probability mismatch",
        )

        actual_draw = int(
            match.goals[0] == match.goals[1]
        )

        all_rows.append((
            season_name,
            match.league,
            p3,
            actual_draw,
        ))

    print("\n=== OVERALL CALIBRATION ===")

    for season in SEASONS:

        rows = [
            (p, y)
            for s, league, p, y in all_rows
            if s == season
        ]

        print()
        print("SEASON:", season)

        print_metrics(rows)

        print("\n=== PROBABILITY BUCKETS ===")

        buckets = defaultdict(list)

        for p, y in rows:

            bucket = min(int(p * 10), 9)

            buckets[bucket].append((p, y))

        print(
            "Range      Count    Predicted"
            "    Actual    Difference"
        )

        for bucket in range(10):

            subset = buckets[bucket]

            if not subset:
                continue

            n, predicted, actual, brier = (
                draw_metrics(subset)
            )

            print(
                f"{bucket*10:02d}-{(bucket+1)*10:02d}%"
                f"     {n:4d}"
                f"       {predicted:6.2%}"
                f"     {actual:6.2%}"
                f"     {predicted-actual:+6.2%}"
            )

            if n < 30:
                print(
                    "  WARNING: small bucket"
                )

        print("\n=== BY LEAGUE ===")

        for league in LEAGUES:

            subset = [
                (p, y)
                for s, lg, p, y in all_rows
                if s == season and lg == league
            ]

            print(f"{league:12s}", end=" ")
            print_metrics(subset)

    print("\n=== COVERAGE ===")

    for season in SEASONS:

        targets = sum(
            start <= m.day < end
            and m.league in LEAGUES
            for m in matches
            for name, (start, end) in SEASONS.items()
            if name == season
        )

        evaluated = sum(
            s == season
            for s, lg, p, y in all_rows
        )

        print(
            season,
            "| targets:", targets,
            "| evaluated:", evaluated,
            "| skipped:", targets - evaluated,
        )

    print("\n=== FINAL STATUS ===")

    print("DRAW CALIBRATION: EVALUATED")
    print("V2/V3 DRAW EQUALITY: VERIFIED")
    print("NO PARAMETERS CHANGED")
    print("HISTORICAL SOURCE AVAILABILITY: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
