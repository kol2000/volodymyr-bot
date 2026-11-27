#!/usr/bin/env python3
"""Experimental EPL V8 batch generator.

Research only.
Does not publish forecasts.
Does not alter Bundesliga cache.
Candidate results remain non-authoritative.
"""

import hashlib
import json
import math
import os
import tempfile

from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import football_forecast_research_v8_1 as research
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals
import football_forecast_epl_overlay_v1 as overlay

from football_forecast_data_live_v1 import load_current_matches


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "state/football_v8_epl_shadow"
MOSCOW = ZoneInfo("Europe/Moscow")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def calculate():

    now = datetime.now(timezone.utc)
    today = now.astimezone(MOSCOW).date()

    season = load_current_matches()

    context = research.build_context(
        as_of=today,
        season_data=(
            season["matches"],
            season["counts"],
        ),
    )

    context = overlay.apply_overlay(
        context,
        season,
        today,
    )

    combined = context["combined"]
    bridge = context["bridge"]

    upcoming = [
        (day, row)
        for day, league, row in context["upcoming"]
        if league == "EPL"
    ]

    require(upcoming, "No upcoming EPL fixtures")

    # Identify the exact candidate data used.
    candidates_raw = overlay.CANDIDATES.read_bytes()

    candidates_hash = hashlib.sha256(
        candidates_raw
    ).hexdigest()

    forecasts = []
    computed = {}

    for day, row in upcoming:

        if day not in computed:

            past = [
                match for match in combined
                if match.day < day
            ]

            require(
                all(match.day < day for match in past),
                "Temporal cutoff violation",
            )

            ratings = engine.rating_before(
                combined,
                day,
            )

            computed[day] = (past, ratings)

        past, ratings = computed[day]

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

        require(
            prediction is not None,
            f"Outcome unavailable: {reason}",
        )

        goals = totals.analyze(
            home,
            away,
            past,
            "EPL",
            day,
        )

        require(
            goals is not None,
            "Totals unavailable",
        )

        probabilities = [
            float(x)
            for x in prediction["V3"]
        ]

        require(
            len(probabilities) == 3
            and all(
                math.isfinite(x) and 0 <= x <= 1
                for x in probabilities
            )
            and abs(sum(probabilities) - 1) < 0.001,
            "Invalid probabilities",
        )

        match_id = str(row["match_id"])

        score = goals["most_likely_score"]

        require(
            len(score) == 2
            and all(type(x) is int and 0 <= x <= 30
                    for x in score),
            "Invalid likely score",
        )

        forecasts.append({
            "competition": "EPL",
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
                "home": float(goals["expected_home"]),
                "away": float(goals["expected_away"]),
            },
            "most_likely_score": list(score),
            "over": {
                str(k): float(v)
                for k, v in goals["over"].items()
            },
            "btts": float(goals["btts"]),
            "kickoff_verified": False,
            "production_eligible": False,
        })

    identities = [
        x["match_id"] for x in forecasts
    ]

    require(
        len(identities) == len(set(identities)),
        "Duplicate match identity",
    )

    forecasts.sort(
        key=lambda x: (
            x["date"],
            x["match_id"],
        )
    )

    payload = {
        "schema": "bubus_v8_epl_research_batch_v1",
        "created_utc": now.isoformat(),
        "snapshot_id": season["snapshot_id"],
        "candidate_sha256": candidates_hash,
        "candidate_results_authoritative": False,
        "kickoff_verified": False,
        "production_eligible": False,
        "forecast_count": len(forecasts),
        "forecasts": forecasts,
    }

    # Verify the entire payload before saving.
    json.dumps(payload, allow_nan=False)

    return payload


def atomic_save(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")

    fd, temporary = tempfile.mkstemp(
        prefix=".epl-v8-",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())

        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():

    print("=== BUBUS V8 EPL RESEARCH BATCH ===")

    data = calculate()

    print("Snapshot:", data["snapshot_id"])
    print("Forecasts:", data["forecast_count"])

    require(
        data["production_eligible"] is False,
        "Publication must remain disabled",
    )

    # Store an immutable record of this calculation.
    stamp = datetime.fromisoformat(
        data["created_utc"]
    ).strftime("%Y%m%dT%H%M%S%fZ")

    target = OUTPUT / f"batch_{stamp}.json"

    require(
        not target.exists(),
        "Batch already exists",
    )

    atomic_save(target, data)

    print("Saved:", target)
    print("Research only: YES")
    print("Telegram publication: DISABLED")
    print("Bundesliga cache: UNCHANGED")


if __name__ == "__main__":
    main()
