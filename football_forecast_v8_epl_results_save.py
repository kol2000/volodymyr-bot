#!/usr/bin/env python3
"""Immutable EPL V8 result candidate recorder.

Default: dry run.
--write: create new provider-only result records.

Never changes forecasts, observations or historical CSV.
Does not authorize production publication.
"""

import argparse
import hashlib
import json
import os

from datetime import datetime, timezone, timedelta
from pathlib import Path

import football_forecast_v8_epl_results as collector


DATA = Path(__file__).resolve().parent / (
    "state/football_v8_epl_shadow"
)

RESULTS = DATA / "results"

MIN_AFTER_KICKOFF = timedelta(hours=2)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_time(value):
    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
    require(dt.tzinfo is not None, "Timezone missing")
    return dt.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    print("=== BUBUS V8 EPL RESULT RECORDING ===")
    print("MODE:", "WRITE" if args.write else "DRY RUN")

    observations = collector.load_observations()

    require(
        len(observations) == 30,
        "Unexpected observation count",
    )

    provider, provider_hash = collector.fetch_results()
    now = datetime.now(timezone.utc)

    new_records = []
    existing_count = 0
    pending_count = 0

    for observation in observations:

        match_id = str(observation["match_id"])
        provider_id = str(
            observation["provider_match_id"]
        )

        audit_path = DATA / observation["audit_file"]

        require(
            audit_path.is_file(),
            f"Audit missing: {match_id}",
        )

        audit_raw = audit_path.read_bytes()

        require(
            digest(audit_raw) == observation["audit_sha256"],
            f"Audit modified: {match_id}",
        )

        audit = json.loads(audit_raw)

        original = [
            m for m in audit["matches"]
            if str(m["match_id"]) == match_id
        ]

        require(
            len(original) == 1,
            f"Audit identity mismatch: {match_id}",
        )

        original = original[0]

        require(
            str(original["provider_match_id"]) == provider_id,
            f"Provider identity conflict: {match_id}",
        )

        match = provider.get(provider_id)

        require(
            match is not None,
            f"Provider match missing: {provider_id}",
        )

        require(
            match["team1"]["teamId"]
            == original["provider_home_team_id"],
            f"Home team identity conflict: {match_id}",
        )

        require(
            match["team2"]["teamId"]
            == original["provider_away_team_id"],
            f"Away team identity conflict: {match_id}",
        )

        kickoff = parse_time(
            observation["kickoff_utc"]
        )

        current_raw = match.get("matchDateTimeUTC")

        require(
            current_raw,
            f"Provider kickoff missing: {match_id}",
        )

        current_kickoff = parse_time(current_raw)

        if current_kickoff != kickoff:
            print(
                "REVIEW: kickoff changed:",
                observation["home"],
                "—",
                observation["away"],
                kickoff.isoformat(),
                "=>",
                current_kickoff.isoformat(),
            )
            pending_count += 1
            continue

        # Avoid accepting results immediately after kickoff.
        if now < kickoff + MIN_AFTER_KICKOFF:
            pending_count += 1
            continue

        score = collector.final_score(match)

        if score is None:
            pending_count += 1
            continue

        record = {
            "schema": "bubus_v8_epl_result_candidate_v1",
            "competition": "EPL",
            "match_id": match_id,
            "provider": "OpenLigaDB",
            "provider_match_id": provider_id,
            "home": observation["home"],
            "away": observation["away"],
            "kickoff_utc": kickoff.isoformat(),
            "home_score": score[0],
            "away_score": score[1],
            "provider_response_sha256": provider_hash,
            "checked_utc": now.isoformat(),
            "status": "PROVIDER_RESULT_CANDIDATE",
            "independently_corroborated": False,
            "production_eligible": False,
        }

        target = RESULTS / f"epl_{match_id}.json"

        if target.exists():

            saved = json.loads(
                target.read_text(encoding="utf-8")
            )

            require(
                saved.get("schema")
                == "bubus_v8_epl_result_candidate_v1",
                f"Existing result schema conflict: {match_id}",
            )

            for field in (
                "competition",
                "match_id",
                "provider_match_id",
                "home",
                "away",
                "kickoff_utc",
                "home_score",
                "away_score",
                "status",
            ):
                require(
                    saved.get(field) == record[field],
                    f"Existing result conflict: "
                    f"{match_id} / {field}",
                )

            existing_count += 1
            continue

        print(
            "NEW RESULT:",
            observation["home"],
            "—",
            observation["away"],
            f"{score[0]}:{score[1]}",
        )

        new_records.append((target, record))

    print()
    print("=== SUMMARY ===")
    print("Observations:", len(observations))
    print("New results:", len(new_records))
    print("Existing results:", existing_count)
    print("Pending:", pending_count)

    if not args.write:
        print("DRY RUN: no files modified")
        return

    RESULTS.mkdir(parents=True, exist_ok=True)

    written = 0

    for target, record in new_records:

        raw = json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")

        # Exclusive creation: existing results are never replaced.
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())

        written += 1

    print()
    print("=== RECORDING COMPLETE ===")
    print("Saved new results:", written)
    print("Independent corroboration: NOT PERFORMED")
    print("Production authorization: NOT GRANTED")
    print("Bundesliga cache: UNCHANGED")


if __name__ == "__main__":
    main()
