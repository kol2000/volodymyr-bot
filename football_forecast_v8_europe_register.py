#!/usr/bin/env python3
"""Bubus V8 European immutable research registration.

Supported:
    LaLiga
    SerieA

Default: read-only dry run.
--write: atomically create observations.

Kickoff times are calendar candidates, NOT verified.

Never modifies original batches, historical data,
Bundesliga, EPL or Telegram.
"""

import argparse
import hashlib
import json
import math
import os
import tempfile

from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

from football_forecast_data_live_v1 import load_current_matches


ROOT = Path(__file__).resolve().parent

LEAGUES = {
    "LaLiga": "football_v8_laliga_shadow",
    "SerieA": "football_v8_seriea_shadow",
}

MIN_LEAD = timedelta(minutes=30)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def utc(value):
    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    require(
        dt.tzinfo is not None,
        "Timestamp without timezone",
    )

    return dt.astimezone(timezone.utc)


def validate_forecast(forecast, league):

    require(
        forecast["competition"] == league,
        "Forecast competition mismatch",
    )

    require(
        forecast["production_eligible"] is False,
        "Unexpected production flag",
    )

    require(
        forecast["kickoff_verified"] is False,
        "Unexpected kickoff verification flag",
    )

    p = forecast["probabilities"]

    probabilities = [
        p["home"],
        p["draw"],
        p["away"],
    ]

    require(
        all(
            type(x) in (int, float)
            and math.isfinite(x)
            and 0 <= x <= 1
            for x in probabilities
        ),
        "Invalid probabilities",
    )

    require(
        abs(sum(probabilities) - 1) < 0.001,
        "Probability sum invalid",
    )


def publish_immutable(path, data):

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
        prefix=".pending-",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())

        os.chmod(temporary, 0o600)

        # Atomic exclusive publication.
        # Existing observations cannot be replaced.
        os.link(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print("=== BUBUS V8 EUROPE REGISTRATION ===")
    print(
        "MODE:",
        "WRITE" if args.write else "DRY RUN",
    )

    season = load_current_matches()

    snapshot_id = season["snapshot_id"]

    fixtures = {}

    for row in season["matches"]:

        key = (
            row["competition"],
            str(row["match_id"]),
        )

        require(
            key not in fixtures,
            f"Duplicate fixture: {key}",
        )

        fixtures[key] = row

    now = datetime.now(timezone.utc)

    planned = []
    stats = Counter()

    print()
    print("Snapshot:", snapshot_id)

    for league, directory in LEAGUES.items():

        folder = ROOT / "state" / directory

        batches = sorted(
            folder.glob("batch_*.json")
        )

        require(
            bool(batches),
            f"{league}: no research batch",
        )

        batch_path = batches[-1]
        batch_raw = batch_path.read_bytes()
        batch_sha = digest(batch_raw)

        batch = json.loads(batch_raw)

        require(
            batch["schema"]
            == "bubus_v8_europe_research_batch_v1",
            f"{league}: invalid batch schema",
        )

        require(
            batch["competition"] == league,
            "Batch competition mismatch",
        )

        require(
            batch["production_eligible"] is False,
            "Unexpected production authorization",
        )

        forecast_snapshot_id = batch["snapshot_id"]

        require(
            isinstance(forecast_snapshot_id, str)
            and len(forecast_snapshot_id) == 64,
            f"{league}: invalid forecast snapshot ID",
        )

        if forecast_snapshot_id != snapshot_id:
            print(
                "Calendar snapshot updated:",
                forecast_snapshot_id,
                "->",
                snapshot_id,
            )

        require(
            batch["forecast_count"]
            == len(batch["forecasts"]),
            f"{league}: forecast count mismatch",
        )

        created = utc(batch["created_utc"])

        print()
        print("=== " + league.upper() + " ===")
        print("Batch:", batch_path.name)
        print("Forecasts:", len(batch["forecasts"]))

        seen = set()

        for forecast in batch["forecasts"]:

            validate_forecast(forecast, league)

            match_id = str(forecast["match_id"])

            require(
                match_id not in seen,
                f"Duplicate forecast: {match_id}",
            )

            seen.add(match_id)

            key = (league, match_id)

            # V8_EXISTING_OBSERVATION_EARLY_CHECK
            # An existing immutable forecast is not
            # revalidated against a newer calendar.
            # Only its persistent identity is checked.
            name_hash = digest(
                (league + ":" + match_id).encode()
            )[:24]

            existing_path = (
                folder
                / "observations"
                / f"{league.lower()}_{name_hash}.json"
            )

            if existing_path.exists():
                saved = json.loads(
                    existing_path.read_bytes()
                )

                require(
                    saved.get("schema")
                    == "bubus_v8_europe_observation_v1",
                    f"{key}: existing schema mismatch",
                )

                require(
                    saved.get("competition") == league
                    and str(saved.get("match_id")) == match_id,
                    f"{key}: existing identity mismatch",
                )

                require(
                    saved.get("production_eligible") is False,
                    f"{key}: existing production flag",
                )

                for field in ("home", "away"):
                    require(
                        saved.get(field) == forecast[field],
                        f"{key}: existing {field} mismatch",
                    )

                require(
                    saved.get("registered_utc")
                    and saved.get("forecast_created_utc")
                    and saved.get("batch_sha256"),
                    f"{key}: incomplete existing observation",
                )

                stats[(league, "EXISTING")] += 1

                print(
                    "EXISTS (IMMUTABLE):",
                    forecast["home"],
                    "—",
                    forecast["away"],
                )

                continue

            row = fixtures.get(key)

            require(
                row is not None,
                f"Missing calendar fixture: {key}",
            )

            for field in ("home", "away", "date"):
                require(
                    row.get(field) == forecast[field],
                    f"{key}: mismatch in {field}",
                )

            raw_kickoff = row.get(
                "kickoff_candidate_utc"
            )

            require(
                raw_kickoff,
                f"{key}: kickoff unavailable",
            )

            kickoff = utc(raw_kickoff)

            require(
                kickoff.date().isoformat()
                == forecast["date"],
                f"{key}: kickoff date mismatch",
            )

            require(
                created + MIN_LEAD < kickoff,
                f"{key}: forecast created too late",
            )

            require(
                now + MIN_LEAD < kickoff,
                f"{key}: registration too late",
            )

            require(
                row.get("kickoff_verified") is False,
                f"{key}: unexpected kickoff flag",
            )

            require(
                row.get("state_status")
                not in (
                    "FINISHED",
                    "RESULT_REPORTED",
                    "RESULT_CORROBORATED",
                    "RESULT_PROVIDER_ONLY",
                ),
                f"{key}: fixture already finished",
            )

            observation = {
                "schema":
                    "bubus_v8_europe_observation_v1",

                "competition": league,
                "season": "2026/27",
                "match_id": match_id,

                "home": forecast["home"],
                "away": forecast["away"],

                "forecast_date": forecast["date"],

                "forecast_created_utc":
                    created.isoformat(),

                "kickoff_candidate_utc":
                    kickoff.isoformat(),

                "kickoff_verified": False,

                "forecast_snapshot_id":
                    forecast_snapshot_id,

                "registration_snapshot_id":
                    snapshot_id,

                "batch_file": batch_path.name,
                "batch_sha256": batch_sha,

                "probabilities":
                    forecast["probabilities"],

                "expected_goals":
                    forecast["expected_goals"],

                "most_likely_score":
                    forecast["most_likely_score"],

                "over": forecast["over"],
                "btts": forecast["btts"],

                "status":
                    "RESEARCH_REGISTERED_CALENDAR_CANDIDATE",

                "production_eligible": False,
            }

            name_hash = digest(
                (league + ":" + match_id).encode()
            )[:24]

            target = (
                folder
                / "observations"
                / f"{league.lower()}_{name_hash}.json"
            )

            if target.exists():

                saved = json.loads(
                    target.read_text(encoding="utf-8")
                )

                expected = dict(observation)

                actual = dict(saved)

                # Registration timestamp is intentionally
                # excluded from repeat-run comparison.
                actual.pop("registered_utc", None)

                require(
                    actual == expected,
                    f"{key}: existing observation conflict",
                )

                stats[(league, "EXISTING")] += 1

                print(
                    "EXISTS:",
                    forecast["home"],
                    "—",
                    forecast["away"],
                )

                continue

            observation["registered_utc"] = (
                now.isoformat()
            )

            planned.append((target, observation))

            stats[(league, "NEW")] += 1

            print(
                "NEW:",
                forecast["home"],
                "—",
                forecast["away"],
                "|",
                kickoff.isoformat(),
            )

    print()
    print("=== REGISTRATION SUMMARY ===")

    for league in LEAGUES:

        print()
        print(league)

        print(
            "New:",
            stats[(league, "NEW")],
        )

        print(
            "Existing:",
            stats[(league, "EXISTING")],
        )

    print()
    print("Total new:", len(planned))

    if not args.write:

        print()
        print("DRY RUN COMPLETE")
        print("No observations written")
        return

    written = 0

    for target, observation in planned:

        publish_immutable(
            target,
            observation,
        )

        written += 1

    print()
    print("=== REGISTRATION COMPLETE ===")
    print("Written:", written)
    print("Kickoff independently verified: NO")
    print("Production publication: DISABLED")
    print("Bundesliga: UNCHANGED")
    print("EPL: UNCHANGED")
    print("Telegram: UNCHANGED")


if __name__ == "__main__":
    main()
