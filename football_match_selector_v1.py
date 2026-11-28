#!/usr/bin/env python3
"""Bubus Match Selector V1.

Research-only editorial selection.
No network, SQLite writes or Telegram publication.
"""

from collections import Counter
from math import log

import football_forecast_research_v8_1 as v8
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals


MAX_MATCHES = 8
MAX_PER_LEAGUE = 2


def uncertainty(probs):
    """Normalized entropy of P1/X/P2."""
    return -sum(
        p * log(p) for p in probs if p > 0
    ) / log(3)


def main():

    print("=== BUBUS MATCH SELECTOR V1 ===")
    print("MODE: OFFLINE / RESEARCH ONLY")

    context = v8.build_context()

    combined = context["combined"]
    upcoming = context["upcoming"]
    bridge = context["bridge"]

    cache = {}
    matches = []

    for day, league, row in upcoming:

        if day not in cache:

            past = [
                m for m in combined
                if m.day < day
            ]

            ratings = engine.rating_before(
                combined,
                day,
            )

            cache[day] = (past, ratings)

        past, ratings = cache[day]

        home = bridge[league][row["home"]]["historical_id"]
        away = bridge[league][row["away"]]["historical_id"]

        fixture = {
            "league": league,
            "day": day,
            "home_id": home,
            "away_id": away,
        }

        predictions, reason = engine.forecast_at(
            fixture,
            past,
            ratings,
        )

        goals = totals.analyze(
            home,
            away,
            past,
            league,
            day,
        )

        if predictions is None or goals is None:
            continue

        p1, px, p2 = predictions["V3"]
        probs = (p1, px, p2)

        matches.append({
            "day": day,
            "league": league,
            "home": row["home"],
            "away": row["away"],
            "probs": probs,
            "goals": goals,
            "expected_total": (
                goals["expected_home"]
                + goals["expected_away"]
            ),
            "balance": (
                max(probs) - min(probs)
            ),
            "uncertainty": uncertainty(probs),
            "favourite": max(probs),
        })

    print("\n=== CANDIDATES ===")
    print("Available:", len(matches))

    categories = [
        (
            "⚖️ РАВНАЯ БОРЬБА",
            lambda m: m["balance"],
            False,
        ),
        (
            "🔥 ВЫСОКАЯ РЕЗУЛЬТАТИВНОСТЬ",
            lambda m: m["expected_total"],
            True,
        ),
        (
            "🛡️ НИЗКАЯ РЕЗУЛЬТАТИВНОСТЬ",
            lambda m: m["expected_total"],
            False,
        ),
        (
            "👑 ЯВНЫЙ ФАВОРИТ",
            lambda m: m["favourite"],
            True,
        ),
        (
            "🎭 НЕОПРЕДЕЛЁННЫЙ ИСХОД",
            lambda m: m["uncertainty"],
            True,
        ),
    ]

    selected = []
    used = set()
    leagues = Counter()

    # First pass: best available match per category.
    # Second pass: fill remaining slots by cycling categories.
    for round_number in range(MAX_MATCHES):

        if len(selected) >= MAX_MATCHES:
            break

        category_name, metric, reverse = (
            categories[round_number % len(categories)]
        )

        ordered = sorted(
            matches,
            key=metric,
            reverse=reverse,
        )

        for match in ordered:

            identity = (
                match["league"],
                match["day"],
                match["home"],
                match["away"],
            )

            if identity in used:
                continue

            if leagues[match["league"]] >= MAX_PER_LEAGUE:
                continue

            selected.append((category_name, match))
            used.add(identity)
            leagues[match["league"]] += 1
            break

    print("\n=== SELECTED MATCHES ===")

    for index, (category, match) in enumerate(
        selected, 1
    ):

        p1, px, p2 = match["probs"]

        print()
        print(f"{index}. {category}")

        print(
            match["day"],
            "|",
            match["league"],
            "|",
            match["home"],
            "—",
            match["away"],
        )

        print(
            f"P1 {p1:.1%} | "
            f"X {px:.1%} | "
            f"P2 {p2:.1%}"
        )

        print(
            "Expected goals:",
            f"{match['expected_total']:.2f}"
        )

        print(
            "Over 2.5:",
            f"{match['goals']['over'][2.5]:.1%}"
        )

        print(
            "Uncertainty:",
            f"{match['uncertainty']:.3f}"
        )

    print("\n=== LEAGUE DISTRIBUTION ===")

    for league in v8.LEAGUES:
        print(
            league,
            leagues[league],
        )

    print("\n=== FINAL STATUS ===")
    print("Selected:", len(selected))
    print("SELECTION: RESEARCH ONLY")
    print("KICKOFF TIMES: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
