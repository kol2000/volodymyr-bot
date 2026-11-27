#!/usr/bin/env python3

"""Immutable V8 research results for LaLiga and SerieA.

Default: read-only.
--write: save immutable result events.

Does not modify forecasts or Telegram.
"""

import argparse
import hashlib
import json
import os
import tempfile

from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from football_forecast_data_live_v1 import load_current_matches


ROOT = Path(__file__).resolve().parent

LEAGUES = {
    "LaLiga": {
        "directory": "football_v8_laliga_shadow",
        "accepted": {"RESULT_CORROBORATED"},
        "result_grade": "CORROBORATED_BY_ADAPTER",
    },
    "SerieA": {
        "directory": "football_v8_seriea_shadow",
        "accepted": {"FINISHED"},
        "result_grade": "PROVIDER_ONLY",
    },
}

MIN_AFTER_KICKOFF = timedelta(hours=2)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def utc(value):
    result = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    require(
        result.tzinfo is not None,
        "Timestamp missing timezone",
    )

    return result.astimezone(timezone.utc)


def canonical(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def write_immutable(path, data):
    raw = canonical(data)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".pending-",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temporary, 0o600)

        # Link creation fails if destination exists.
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

    print("=== BUBUS V8 EUROPE RESULTS ===")
    print("MODE:", "WRITE" if args.write else "DRY RUN")

    season = load_current_matches()

    snapshot_id = season["snapshot_id"]
    now = datetime.now(timezone.utc)

    fixtures = {}

    for row in season["matches"]:
        key = (
            row["competition"],
            str(row["match_id"]),
        )

        require(
            key not in fixtures,
            f"Duplicate calendar match: {key}",
        )

        fixtures[key] = row

    stats = Counter()
    planned = []

    for league, config in LEAGUES.items():
        folder = ROOT / "state" / config["directory"]

        observations = sorted(
            (folder / "observations").glob("*.json")
        )

        print()
        print("=== " + league.upper() + " ===")
        print("Observations:", len(observations))

        seen = set()

        for path in observations:
            obs = json.loads(path.read_bytes())

            require(
                obs["competition"] == league,
                f"Observation league mismatch: {path}",
            )

            require(
                obs["production_eligible"] is False,
                f"Unexpected production flag: {path}",
            )

            require(
                obs["kickoff_verified"] is False,
                f"Unexpected kickoff flag: {path}",
            )

            match_id = str(obs["match_id"])
            key = (league, match_id)

            require(
                key not in seen,
                f"Duplicate observation: {key}",
            )

            seen.add(key)

            row = fixtures.get(key)

            require(
                row is not None,
                f"Missing calendar match: {key}",
            )

            for field in ("home", "away"):
                require(
                    row[field] == obs[field],
                    f"{key}: {field} mismatch",
                )

            require(
                row.get("date") == obs["forecast_date"],
                f"{key}: date mismatch",
            )

            kickoff = utc(obs["kickoff_candidate_utc"])
            created = utc(obs["forecast_created_utc"])
            registered = utc(obs["registered_utc"])

            require(
                created <= registered < kickoff,
                f"{key}: invalid forecast timeline",
            )

            status = row.get("state_status")

            if status not in config["accepted"]:
                stats[(league, "PENDING")] += 1
                continue

            if now < kickoff + MIN_AFTER_KICKOFF:
                stats[(league, "TOO_EARLY")] += 1
                continue

            home_score = row.get("home_score")
            away_score = row.get("away_score")

            require(
                type(home_score) is int
                and type(away_score) is int
                and 0 <= home_score <= 30
                and 0 <= away_score <= 30,
                f"{key}: invalid final score",
            )

            result_source = row.get("result_source")

            require(
                isinstance(result_source, str)
                and bool(result_source),
                f"{key}: missing result source",
            )

            # Identity is based on match and result content,
            # not collection time or current snapshot.
            result_identity = {
                "competition": league,
                "match_id": match_id,
                "home_score": home_score,
                "away_score": away_score,
                "state_status": status,
                "result_source": result_source,
            }

            match_hash = digest(
                f"{league}:{match_id}".encode()
            )[:20]

            result_hash = digest(
                canonical(result_identity)
            )[:20]

            target = (
                folder
                / "result_events"
                / f"{match_hash}_{result_hash}.json"
            )

            if target.exists():
                saved = json.loads(target.read_bytes())

                require(
                    saved.get("result_identity")
                    == result_identity,
                    f"{key}: immutable event conflict",
                )

                stats[(league, "EXISTING")] += 1
                continue

            event = {
                "schema": "bubus_v8_europe_result_event_v1",
                "result_identity": result_identity,
                "home": obs["home"],
                "away": obs["away"],
                "forecast_observation_file": path.name,
                "forecast_batch_sha256": obs["batch_sha256"],
                "forecast_created_utc":
                    obs["forecast_created_utc"],
                "forecast_registered_utc":
                    obs["registered_utc"],
                "kickoff_candidate_utc":
                    obs["kickoff_candidate_utc"],
                "kickoff_verified": False,
                "result_grade": config["result_grade"],
                "result_date": row.get("result_date"),
                "provider_source_sha256":
                    row.get("provider_source_sha256"),
                "collection_snapshot_id": snapshot_id,
                "collected_utc": now.isoformat(),
                "production_eligible": False,
            }

            planned.append((target, event))

            stats[(league, "NEW")] += 1

            print(
                "NEW RESULT:",
                obs["home"],
                "—",
                obs["away"],
                f"{home_score}:{away_score}",
                "|",
                config["result_grade"],
            )

    # Ligue1 uses date-only observations.
    # No French kickoff timezone is assumed.
    from football_forecast_v8_ligue1_results import collect_ligue1

    french_planned, french_stats = collect_ligue1(
        fixtures,
        snapshot_id,
        now,
    )

    planned.extend(french_planned)

    print()
    print("=== SUMMARY ===")

    for league in LEAGUES:
        print(
            league,
            "| pending:",
            stats[(league, "PENDING")],
            "| too early:",
            stats[(league, "TOO_EARLY")],
            "| existing:",
            stats[(league, "EXISTING")],
            "| new:",
            stats[(league, "NEW")],
        )

    print("Total new:", len(planned))

    if not args.write:
        print("DRY RUN COMPLETE")
        print("Files modified: NO")
        return

    for target, event in planned:
        write_immutable(target, event)

    print()
    print("RESULT EVENTS WRITTEN:", len(planned))
    print("Forecasts modified: NO")
    print("Telegram modified: NO")


if __name__ == "__main__":
    main()
