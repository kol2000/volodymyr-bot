#!/usr/bin/env python3
"""Bubus Forecast V8 Bridge V1.

Research-only Soccer365 -> V8 forecast integration.

No production integration.
No database writes.
No Telegram messages.
"""

from collections import Counter
from datetime import date, timedelta
import math

import soccer365_football as soccer
import football_soccer365_v8_mapping_v1 as mapping_module
import football_forecast_research_v8_1 as v8
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals

from football_forecast_formatter_v1 import render_forecast


START = date(2026, 11, 27)
END = date(2026, 11, 30)


def validate_probabilities(probs):

    if len(probs) != 3:
        raise RuntimeError("Expected three probabilities")

    if not all(
        math.isfinite(p) and 0 <= p <= 1
        for p in probs
    ):
        raise RuntimeError("Invalid probabilities")

    if abs(sum(probs) - 1) > 0.001:
        raise RuntimeError("Probabilities do not sum to one")


def main():

    print("=== BUBUS FORECAST V8 BRIDGE V1 ===")
    print("MODE: RESEARCH ONLY")
    print("PRODUCTION INTEGRATION: DISABLED")

    mapping = mapping_module.load_mapping()

    context = v8.build_context()

    combined = context["combined"]
    upcoming = context["upcoming"]
    bridge = context["bridge"]

    print("\n=== CONTEXT ===")
    print("Historical matches:", len(combined))
    print("Upcoming candidates:", len(upcoming))

    # Index fixtures from the research calendar.

    fixtures = {}

    for day, league, row in upcoming:

        if not START <= day <= END:
            continue

        key = (
            day,
            league,
            row["home"],
            row["away"],
        )

        if key in fixtures:
            raise RuntimeError(
                f"Duplicate V8 fixture: {key}"
            )

        fixtures[key] = row

    print("Fixtures in selected period:", len(fixtures))

    # Read only Soccer365 daily listing pages.

    soccer_matches = []

    for offset in range((END - START).days + 1):

        day = START + timedelta(days=offset)

        url = (
            "https://soccer365.ru/online/&date="
            + day.isoformat()
        )

        rows = soccer.listing(
            soccer.fetch(url)
        )

        for row in rows:

            if row["competition"] not in mapping_module.LEAGUES:
                continue

            soccer_matches.append((day, row))

    print("Soccer365 matches:", len(soccer_matches))

    # Calculate each unique date's historical context once.

    cache = {}

    results = []
    skipped = Counter()

    seen_soccer = set()
    seen_fixtures = set()

    for day, item in soccer_matches:

        soccer_id = item["id"]

        if soccer_id in seen_soccer:
            raise RuntimeError(
                f"Duplicate Soccer365 ID: {soccer_id}"
            )

        seen_soccer.add(soccer_id)

        league = mapping_module.LEAGUES[
            item["competition"]
        ]

        home = mapping[league].get(
            soccer.identity(item["home"])
        )

        away = mapping[league].get(
            soccer.identity(item["away"])
        )

        if home is None or away is None:

            skipped["UNMAPPED_TEAM"] += 1
            continue

        key = (day, league, home, away)

        row = fixtures.get(key)

        if row is None:

            skipped["FIXTURE_NOT_FOUND"] += 1
            continue

        if key in seen_fixtures:
            raise RuntimeError(
                f"Duplicate fixture mapping: {key}"
            )

        seen_fixtures.add(key)

        if day not in cache:

            past = [
                m for m in combined
                if m.day < day
            ]

            if any(m.day >= day for m in past):
                raise RuntimeError("Temporal leakage")

            ratings = engine.rating_before(
                combined,
                day,
            )

            cache[day] = (past, ratings)

        past, ratings = cache[day]

        home_id = bridge[league][home][
            "historical_id"
        ]

        away_id = bridge[league][away][
            "historical_id"
        ]

        fixture = {
            "league": league,
            "day": day,
            "home_id": home_id,
            "away_id": away_id,
        }

        predictions, reason = engine.forecast_at(
            fixture,
            past,
            ratings,
        )

        goals = totals.analyze(
            home_id,
            away_id,
            past,
            league,
            day,
        )

        if predictions is None:

            skipped["OUTCOME_UNAVAILABLE"] += 1
            continue

        if goals is None:

            skipped["TOTALS_UNAVAILABLE"] += 1
            continue

        probs = tuple(predictions["V3"])

        validate_probabilities(probs)

        message = render_forecast(
            league=league,
            match_date=day,
            home=item["home"],
            away=item["away"],
            probabilities=probs,
            goals=goals,
            verified=False,
        )

        if (
            "Время начала матча не подтверждено"
            not in message
        ):
            raise RuntimeError(
                "Missing research warning"
            )

        results.append({
            "soccer_id": soccer_id,
            "day": day,
            "league": league,
            "home": item["home"],
            "away": item["away"],
            "probs": probs,
            "goals": goals,
            "message": message,
        })

    print("\n=== FORECAST COVERAGE ===")

    print("Soccer365 matches:", len(soccer_matches))
    print("Matched fixtures:", len(seen_fixtures))
    print("Complete forecasts:", len(results))
    print("Skipped:", dict(skipped))

    counts = Counter(
        row["league"]
        for row in results
    )

    print("\n=== LEAGUES ===")

    for league in v8.LEAGUES:
        print(f"{league:12s}: {counts[league]}")

    if (
        len(soccer_matches) != len(fixtures)
        or len(seen_fixtures) != len(fixtures)
    ):
        raise RuntimeError(
            "Incomplete fixture reconciliation"
        )

    if len(results) != len(fixtures):
        raise RuntimeError(
            "Incomplete forecast coverage"
        )

    # Only display three full messages.
    # All forecasts above were calculated and checked.

    print("\n=== TELEGRAM FORMAT PREVIEWS ===")

    for index, row in enumerate(results[:3], 1):

        print()
        print("=" * 68)
        print("PREVIEW:", index)
        print("SOCCER365 MATCH ID:", row["soccer_id"])
        print("=" * 68)
        print(row["message"])

    print("\n=== FINAL STATUS ===")
    print("SOCCER365 -> V8 FORECAST BRIDGE: PASSED")
    print("Complete forecasts:", len(results))
    print("KICKOFF TIMES: UNVERIFIED")
    print("HISTORICAL AVAILABILITY: UNVERIFIED")
    print("RESEARCH OUTPUT ONLY")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
