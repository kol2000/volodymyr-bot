#!/usr/bin/env python3
"""Experimental EPL V8 forecast preview.

Read-only research calculation.
Candidate results are not authoritative.
No cache, database or Telegram writes.
"""

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import football_forecast_research_v8_1 as research
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals
import football_forecast_epl_overlay_v1 as overlay

from football_forecast_data_live_v1 import (
    load_current_matches,
)

MOSCOW = ZoneInfo("Europe/Moscow")


def main():

    print("=== BUBUS V8 EPL PREVIEW ===")
    print("MODE: EXPERIMENTAL / READ ONLY")

    today = datetime.now(MOSCOW).date()

    season = load_current_matches()

    context = research.build_context(
        as_of=today,
        season_data=(
            season["matches"],
            season["counts"],
        ),
    )

    original_count = len(context["combined"])

    context = overlay.apply_overlay(
        context,
        season,
        today,
    )

    combined = context["combined"]
    bridge = context["bridge"]

    print()
    print("=== HISTORICAL DATA ===")

    print("Snapshot:", season["snapshot_id"])
    print("Original matches:", original_count)
    print("Combined matches:", len(combined))
    print(
        "EPL candidate results:",
        context["epl_overlay_added"],
    )

    print("Candidate results authoritative: NO")

    upcoming = [
        (day, row)
        for day, league, row in context["upcoming"]
        if league == "EPL"
    ]

    print()
    print("=== UPCOMING EPL ===")
    print("Matches:", len(upcoming))

    coverage = Counter()
    cache = {}

    for day, row in upcoming:

        print()
        print("=" * 55)

        print(
            day,
            row["home"],
            "—",
            row["away"],
        )

        if day not in cache:

            past = [
                match
                for match in combined
                if match.day < day
            ]

            ratings = engine.rating_before(
                combined,
                day,
            )

            cache[day] = (past, ratings)

        past, ratings = cache[day]

        if not all(m.day < day for m in past):
            raise RuntimeError(
                "Historical cutoff violation"
            )

        try:

            home = bridge["EPL"][
                row["home"]
            ]["historical_id"]

            away = bridge["EPL"][
                row["away"]
            ]["historical_id"]

            fixture = {
                "league": "EPL",
                "day": day,
                "home_id": home,
                "away_id": away,
            }

            prediction, reason = engine.forecast_at(
                fixture,
                past,
                ratings,
            )

            if prediction is None:
                coverage["OUTCOME_UNAVAILABLE"] += 1
                print("Outcome unavailable:", reason)
                continue

            goals = totals.analyze(
                home,
                away,
                past,
                "EPL",
                day,
            )

            if goals is None:
                coverage["TOTALS_UNAVAILABLE"] += 1
                print("Totals unavailable")
                continue

            probabilities = prediction["V3"]

            if len(probabilities) != 3:
                raise RuntimeError(
                    "Invalid probabilities"
                )

            print()
            print("📊 ИСХОД МАТЧА")

            print(
                "П1: {:.1f}%".format(
                    probabilities[0] * 100
                )
            )

            print(
                "Х:  {:.1f}%".format(
                    probabilities[1] * 100
                )
            )

            print(
                "П2: {:.1f}%".format(
                    probabilities[2] * 100
                )
            )

            score = goals["most_likely_score"]

            print()
            print("🥅 ВЕРОЯТНЫЙ СЧЁТ")
            print(f"{score[0]}:{score[1]}")

            print()
            print("📈 ОЖИДАЕМЫЕ ГОЛЫ")

            print(
                row["home"],
                f'{goals["expected_home"]:.2f}',
            )

            print(
                row["away"],
                f'{goals["expected_away"]:.2f}',
            )

            print()
            print("📊 ТОТАЛЫ")

            for threshold, probability in sorted(
                goals["over"].items()
            ):
                print(
                    f"ТБ {threshold}: "
                    f"{probability * 100:.1f}%"
                )

            print()
            print(
                "Обе забьют:",
                f'{goals["btts"] * 100:.1f}%',
            )

            coverage["FULL_FORECAST"] += 1

        except Exception as exc:

            coverage["ERROR"] += 1

            print(
                "ERROR:",
                type(exc).__name__,
                str(exc),
            )

    print()
    print("=" * 55)
    print("=== FINAL SUMMARY ===")

    print("Upcoming:", len(upcoming))

    for name, count in sorted(coverage.items()):
        print(name, count)

    print()
    print("Research only")
    print("Results provenance: NOT VERIFIED")
    print("Kickoff times: NOT VERIFIED")
    print("Historical CSV modified: NO")
    print("V8 cache modified: NO")
    print("Telegram messages: NO")


if __name__ == "__main__":
    main()
