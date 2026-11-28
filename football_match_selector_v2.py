#!/usr/bin/env python3
"""Bubus Match Selector V2.

Research-only selection for one football round.

Uses:
- Forecast Research V8.1
- Forecast V3 probabilities
- Totals V2
- Formatter V1

No network requests, DB writes or Telegram messages.
"""

from collections import Counter
from datetime import date
from math import log
import math

import football_forecast_research_v8_1 as v8
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals

from football_forecast_formatter_v1 import render_forecast


START = date(2026, 11, 27)
END = date(2026, 11, 30)

MAX_MATCHES = 8
MAX_PER_LEAGUE = 2

CATEGORIES = (
    "⚖️ РАВНАЯ БОРЬБА",
    "🔥 МНОГО ГОЛОВ",
    "🛡️ МАЛО ГОЛОВ",
    "👑 ЯВНЫЙ ФАВОРИТ",
)


def entropy(probs):
    return -sum(
        p * log(p)
        for p in probs
        if p > 0
    ) / log(3)


def identity(match):
    return (
        match["league"],
        match["day"],
        match["home"],
        match["away"],
    )


def category_score(match, category):

    if category == CATEGORIES[0]:
        return match["entropy"]

    if category == CATEGORIES[1]:
        return match["expected_total"]

    if category == CATEGORIES[2]:
        return -match["expected_total"]

    if category == CATEGORIES[3]:
        return match["favourite"]

    raise ValueError("Unknown category")


def ordered_for_category(matches, category):

    return sorted(
        matches,
        key=lambda m: (
            -category_score(m, category),
            m["day"],
            m["league"],
            m["home"],
            m["away"],
        )
    )


def main():

    print("=== BUBUS MATCH SELECTOR V2 ===")
    print("MODE: OFFLINE / RESEARCH ONLY")
    print("PERIOD:", START, "—", END)

    assert START > v8.AS_OF
    assert START <= END

    context = v8.build_context()

    combined = context["combined"]
    bridge = context["bridge"]

    upcoming = [
        (day, league, row)
        for day, league, row in context["upcoming"]
        if START <= day <= END
    ]

    print("\n=== SOURCE COVERAGE ===")
    print("Matches in period:", len(upcoming))

    matches = []
    cache = {}

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

        assert all(m.day < day for m in past)

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

        probs = tuple(predictions["V3"])

        if (
            len(probs) != 3
            or not all(
                math.isfinite(p) and 0 <= p <= 1
                for p in probs
            )
            or abs(sum(probs) - 1) > 0.001
        ):
            raise RuntimeError(
                f"Invalid probabilities: {row['match_id']}"
            )

        expected_total = (
            goals["expected_home"]
            + goals["expected_away"]
        )

        if not math.isfinite(expected_total):
            raise RuntimeError("Invalid expected total")

        matches.append({
            "day": day,
            "league": league,
            "home": row["home"],
            "away": row["away"],
            "match_id": row["match_id"],
            "probs": probs,
            "goals": goals,
            "entropy": entropy(probs),
            "expected_total": expected_total,
            "favourite": max(probs),
        })

    print("Complete forecasts:", len(matches))

    if not matches:
        raise RuntimeError("No complete forecasts")

    selected = []
    used = set()
    league_counts = Counter()

    def add(match, reason):

        key = identity(match)

        if key in used:
            return False

        if len(selected) >= MAX_MATCHES:
            return False

        if league_counts[match["league"]] >= MAX_PER_LEAGUE:
            return False

        selected.append((reason, match))
        used.add(key)
        league_counts[match["league"]] += 1

        return True

    # -----------------------------------
    # 1. Four editorial categories
    # -----------------------------------

    for category in CATEGORIES:

        ordered = ordered_for_category(
            matches,
            category,
        )

        for match in ordered:

            if add(match, category):
                break

    # -----------------------------------
    # 2. League diversity
    # -----------------------------------

    represented = {
        match["league"]
        for _, match in selected
    }

    for league in v8.LEAGUES:

        if league in represented:
            continue

        candidates = [
            m for m in matches
            if m["league"] == league
        ]

        # Within an unrepresented league, choose
        # the strongest available category signal.
        #
        # Values are only editorial heuristics,
        # not probabilities or model confidence.

        def editorial_score(m):

            high_goals = min(
                1.0,
                max(
                    0.0,
                    (m["expected_total"] - 2.5) / 2.0
                )
            )

            low_goals = min(
                1.0,
                max(
                    0.0,
                    (2.5 - m["expected_total"]) / 1.5
                )
            )

            strong_favourite = min(
                1.0,
                max(
                    0.0,
                    (m["favourite"] - 0.5) / 0.35
                )
            )

            return max(
                m["entropy"],
                high_goals,
                low_goals,
                strong_favourite,
            )

        candidates.sort(
            key=lambda m: (
                -editorial_score(m),
                m["day"],
                m["home"],
                m["away"],
            )
        )

        for match in candidates:

            if add(match, "🌍 РАЗНООБРАЗИЕ ЧЕМПИОНАТОВ"):
                represented.add(league)
                break

    # -----------------------------------
    # 3. Fill remaining slots
    # -----------------------------------

    category_index = 0

    while len(selected) < MAX_MATCHES:

        added = False

        for offset in range(len(CATEGORIES)):

            category = CATEGORIES[
                (category_index + offset)
                % len(CATEGORIES)
            ]

            ordered = ordered_for_category(
                matches,
                category,
            )

            for match in ordered:

                if add(match, category):
                    added = True
                    break

            if added:
                category_index = (
                    category_index + offset + 1
                ) % len(CATEGORIES)
                break

        if not added:
            break

    # -----------------------------------
    # 4. Validate selection
    # -----------------------------------

    assert len(selected) <= MAX_MATCHES
    assert len(used) == len(selected)
    assert all(
        n <= MAX_PER_LEAGUE
        for n in league_counts.values()
    )

    available_leagues = {
        m["league"] for m in matches
    }

    selected_leagues = {
        m["league"] for _, m in selected
    }

    if (
        len(available_leagues) <= MAX_MATCHES
        and len(available_leagues) <= len(selected)
    ):
        assert (
            available_leagues <= selected_leagues
        ), "League diversity not achieved"

    # -----------------------------------
    # 5. Display selected matches
    # -----------------------------------

    print("\n=== SELECTED MATCHES ===")

    for index, (reason, match) in enumerate(
        selected, 1
    ):

        p1, px, p2 = match["probs"]

        print()
        print(f"{index}. {reason}")

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

    # -----------------------------------
    # 6. Telegram message previews
    # -----------------------------------

    print("\n=== TELEGRAM PREVIEWS ===")

    for index, (reason, match) in enumerate(
        selected[:3], 1
    ):

        message = render_forecast(
            league=match["league"],
            match_date=match["day"],
            home=match["home"],
            away=match["away"],
            probabilities=match["probs"],
            goals=match["goals"],
            verified=False,
        )

        if "Время начала матча не подтверждено" not in message:
            raise RuntimeError(
                "Missing unverified kickoff warning"
            )

        print()
        print("=" * 65)
        print("PREVIEW:", index)
        print("SELECTION REASON:", reason)
        print("=" * 65)
        print(message)

    # -----------------------------------
    # 7. Summary
    # -----------------------------------

    print("\n=== LEAGUE DISTRIBUTION ===")

    for league in v8.LEAGUES:

        print(
            f"{league:12s}",
            league_counts[league],
        )

    print("\n=== FINAL STATUS ===")

    print("Candidates:", len(upcoming))
    print("Complete forecasts:", len(matches))
    print("Selected:", len(selected))
    print("Leagues represented:", len(selected_leagues))
    print("AS_OF:", v8.AS_OF)

    print("KICKOFF TIMES: UNVERIFIED")
    print("SOURCE AVAILABILITY: UNVERIFIED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
