#!/usr/bin/env python3
"""Bubus V8 European Research Batch.

Supported:
    LaLiga
    SerieA
    Ligue1

Research only.
No Telegram publication.
No modification of Bundesliga or EPL caches.
"""

import hashlib
import json
import math
import os
import tempfile

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import football_forecast_research_v8_1 as research
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals

from football_forecast_data_live_v1 import (
    load_current_matches,
)
from football_forecast_readiness_v1 import evaluate


ROOT = Path(__file__).resolve().parent

MOSCOW = ZoneInfo("Europe/Moscow")

LEAGUES = {
    "LaLiga": "football_v8_laliga_shadow",
    "SerieA": "football_v8_seriea_shadow",
    "Ligue1": "football_v8_ligue1_shadow",
}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_probability(value):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def build_batches():

    now = datetime.now(timezone.utc)
    today = now.astimezone(MOSCOW).date()

    print("=== BUBUS V8 EUROPE BATCH ===")
    print("MODE: RESEARCH ONLY")
    print("AS OF:", today)

    readiness = evaluate(as_of=today)

    print()
    print("=== READINESS ===")

    for league in LEAGUES:

        item = readiness["leagues"][league]

        print(
            league,
            item["status"],
            "accepted:",
            item["accepted"],
        )

        require(
            item["status"] == "READY_CANDIDATE",
            f"{league}: readiness blocked",
        )

        require(
            not item["unresolved"]
            and not item["quarantined"]
            and not item["undated"],
            f"{league}: unresolved data",
        )

    season = load_current_matches()

    context = research.build_context(
        as_of=today,
        season_data=(
            season["matches"],
            season["counts"],
        ),
    )

    require(
        readiness["snapshot_id"] == season["snapshot_id"],
        "Readiness snapshot mismatch",
    )

    combined = context["combined"]
    bridge = context["bridge"]

    require(combined, "Historical context empty")

    print()
    print("=== HISTORICAL DATA ===")
    print("Snapshot:", season["snapshot_id"])
    print("Historical matches:", len(combined))

    batches = {}

    cache = {}

    for league in LEAGUES:

        upcoming = [
            (day, row)
            for day, competition, row
            in context["upcoming"]
            if competition == league
        ]

        require(
            upcoming,
            f"{league}: no upcoming matches",
        )

        forecasts = []

        seen = set()

        print()
        print("=== " + league.upper() + " ===")

        for day, row in upcoming:

            match_id = str(row["match_id"])

            require(
                match_id not in seen,
                f"{league}: duplicate match ID",
            )

            seen.add(match_id)

            if day not in cache:

                past = [
                    match
                    for match in combined
                    if match.day < day
                ]

                require(
                    all(m.day < day for m in past),
                    "Temporal leakage detected",
                )

                ratings = engine.rating_before(
                    combined,
                    day,
                )

                cache[day] = (past, ratings)

            past, ratings = cache[day]

            home = bridge[league][
                row["home"]
            ]["historical_id"]

            away = bridge[league][
                row["away"]
            ]["historical_id"]

            fixture = {
                "league": league,
                "day": day,
                "home_id": home,
                "away_id": away,
            }

            prediction, reason = engine.forecast_at(
                fixture,
                past,
                ratings,
            )

            require(
                prediction is not None,
                f"{league}/{match_id}: "
                f"outcome unavailable: {reason}",
            )

            goals = totals.analyze(
                home,
                away,
                past,
                league,
                day,
            )

            require(
                goals is not None,
                f"{league}/{match_id}: totals unavailable",
            )

            probabilities = [
                float(x)
                for x in prediction["V3"]
            ]

            require(
                len(probabilities) == 3
                and all(
                    validate_probability(x)
                    for x in probabilities
                )
                and abs(sum(probabilities) - 1) < 0.001,
                f"{league}/{match_id}: invalid probabilities",
            )

            expected_home = float(
                goals["expected_home"]
            )

            expected_away = float(
                goals["expected_away"]
            )

            require(
                all(
                    math.isfinite(x) and 0 <= x <= 30
                    for x in (expected_home, expected_away)
                ),
                f"{league}/{match_id}: invalid expected goals",
            )

            score = list(
                goals["most_likely_score"]
            )

            require(
                len(score) == 2
                and all(
                    type(x) is int and 0 <= x <= 30
                    for x in score
                ),
                f"{league}/{match_id}: invalid likely score",
            )

            over = {
                str(k): float(v)
                for k, v in goals["over"].items()
            }

            require(
                all(
                    validate_probability(x)
                    for x in over.values()
                ),
                f"{league}/{match_id}: invalid totals",
            )

            btts = float(goals["btts"])

            require(
                validate_probability(btts),
                f"{league}/{match_id}: invalid BTTS",
            )

            forecasts.append({
                "competition": league,
                "match_id": match_id,
                "date": day.isoformat(),
                "home": row["home"],
                "away": row["away"],

                "probabilities": {
                    "home": probabilities[0],
                    "draw": probabilities[1],
                    "away": probabilities[2],
                },

                "expected_goals": {
                    "home": expected_home,
                    "away": expected_away,
                },

                "most_likely_score": score,
                "over": over,
                "btts": btts,

                "kickoff_verified": False,
                "production_eligible": False,
            })

        forecasts.sort(
            key=lambda x: (
                x["date"],
                x["match_id"],
            )
        )

        print("Forecasts:", len(forecasts))

        batches[league] = {
            "schema": "bubus_v8_europe_research_batch_v1",
            "created_utc": now.isoformat(),

            "competition": league,
            "season": "2026/27",

            "snapshot_id": season["snapshot_id"],
            "snapshot_created_utc":
                season["snapshot_created_utc"].isoformat(),

            "research_as_of": today.isoformat(),

            "readiness_status": "READY_CANDIDATE",

            "historical_results_independently_verified":
                False,

            "kickoff_verified": False,
            "production_eligible": False,

            "forecast_count": len(forecasts),
            "forecasts": forecasts,
        }

        json.dumps(
            batches[league],
            allow_nan=False,
        )

    return batches


def save_immutable(path, data):

    raw = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".research-",
        dir=path.parent,
    )

    try:

        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())

        os.chmod(temporary, 0o600)

        # Exclusive atomic publication.
        # Existing research batches are never replaced.
        os.link(temporary, path)

    finally:

        if os.path.exists(temporary):
            os.unlink(temporary)


def main():

    batches = build_batches()

    print()
    print("=== SAVING RESEARCH BATCHES ===")

    for league, batch in batches.items():

        output = (
            ROOT /
            "state" /
            LEAGUES[league]
        )

        stamp = datetime.fromisoformat(
            batch["created_utc"]
        ).strftime("%Y%m%dT%H%M%S%fZ")

        target = output / (
            f"batch_{stamp}.json"
        )

        require(
            batch["production_eligible"] is False,
            "Publication authorization detected",
        )

        save_immutable(target, batch)

        print(
            league,
            "saved:",
            target,
        )

    print()
    print("=== FINAL STATUS ===")
    print("Research batches: 3")
    print("Production publication: DISABLED")
    print("Bundesliga cache: UNCHANGED")
    print("EPL cache: UNCHANGED")
    print("Telegram messages: NONE")


if __name__ == "__main__":
    main()
