#!/usr/bin/env python3
"""Ligue1 V8 rolling research registrar: read-only validation stage."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone, timedelta, time
from pathlib import Path

from football_forecast_v8_ligue1_history import (
    historical_provider,
    parse_utc,
)

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "state/football_v8_ligue1_shadow"
DEST = FOLDER / "observations"

MIN_LEAD = timedelta(hours=24)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_json(path):
    return json.loads(path.read_bytes())


def verify_existing(path, match_id):
    obs = load_json(path)

    require(
        obs.get("schema")
        == "bubus_v8_ligue1_date_observation_v1",
        f"Schema mismatch: {match_id}",
    )

    require(
        obs.get("competition") == "Ligue1"
        and str(obs.get("match_id")) == match_id,
        f"Identity mismatch: {match_id}",
    )

    require(
        obs.get("production_eligible") is False,
        f"Production flag: {match_id}",
    )

    forecast = obs["forecast"]

    require(
        str(forecast["match_id"]) == match_id
        and forecast["competition"] == "Ligue1"
        and forecast["production_eligible"] is False,
        f"Nested forecast mismatch: {match_id}",
    )

    batch_name = obs["forecast_batch_file"]

    require(
        Path(batch_name).name == batch_name,
        f"Unsafe batch name: {match_id}",
    )

    original_batch = FOLDER / batch_name

    require(
        original_batch.is_file(),
        f"Original batch missing: {match_id}",
    )

    batch_raw = original_batch.read_bytes()

    require(
        sha(batch_raw) == obs["forecast_batch_sha256"],
        f"Original batch changed: {match_id}",
    )

    batch = json.loads(batch_raw)

    require(
        batch.get("competition") == "Ligue1"
        and batch.get("production_eligible") is False,
        f"Invalid original batch: {match_id}",
    )

    original_forecasts = [
        item
        for item in batch["forecasts"]
        if str(item["match_id"]) == match_id
    ]

    require(
        len(original_forecasts) == 1
        and original_forecasts[0] == forecast,
        f"Forecast provenance mismatch: {match_id}",
    )

    require(
        obs["forecast_snapshot_id"] == batch["snapshot_id"],
        f"Snapshot mismatch: {match_id}",
    )

    created = parse_utc(obs["forecast_created_utc"])
    registered = parse_utc(obs["registered_utc"])

    require(
        created == parse_utc(batch["created_utc"])
        and created <= registered,
        f"Timeline mismatch: {match_id}",
    )

    original_date = datetime.fromisoformat(
        obs["forecast_date"]
    ).date()

    day_start = datetime.combine(
        original_date,
        time.min,
        tzinfo=timezone.utc,
    )

    require(
        registered < day_start,
        f"Registration after match day: {match_id}",
    )

    require(
        obs["forecast_date"] == forecast["date"],
        f"Original forecast date mismatch: {match_id}",
    )

    require(
        obs["kickoff_candidate_utc"] is None
        and obs["kickoff_verified"] is False,
        f"Unexpected verified kickoff: {match_id}",
    )

    return obs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    planned = []
    print("=== BUBUS V8 LIGUE1 REGISTRAR V2 ===")
    print("MODE:", "WRITE IMMUTABLE OBSERVATIONS" if args.write else "READ ONLY")

    batches = sorted(FOLDER.glob("batch_*.json"))

    require(batches, "No research batches")

    now = datetime.now(timezone.utc)

    stats = Counter()
    encountered = set()

    existing_files = sorted(
        DEST.glob("ligue1_*.json")
    )

    print()
    print("Research batches:", len(batches))
    print("Existing observations:", len(existing_files))

    require(
        len(existing_files) >= 18,
        "Original 18 observations missing",
    )

    # Independently verify every previously registered record.
    existing_ids = set()

    for path in existing_files:
        obs = load_json(path)
        match_id = str(obs["match_id"])

        require(
            match_id not in existing_ids,
            f"Duplicate observation: {match_id}",
        )

        existing_ids.add(match_id)
        verify_existing(path, match_id)

    print("Existing provenance audit: PASSED")

    for batch_path in batches:
        raw = batch_path.read_bytes()
        batch = json.loads(raw)

        require(
            batch.get("schema")
            == "bubus_v8_europe_research_batch_v1",
            f"Invalid batch schema: {batch_path.name}",
        )

        require(
            batch.get("competition") == "Ligue1"
            and batch.get("production_eligible") is False,
            f"Invalid batch: {batch_path.name}",
        )

        created = parse_utc(batch["created_utc"])

        require(
            created <= now,
            "Research batch created in future",
        )

        history = historical_provider(
            batch["created_utc"]
        )

        print()
        print("BATCH:", batch_path.name)
        print("SHA256:", sha(raw))
        print("Provider:", history["provider_id"])
        print(
            "Historical observation:",
            history["observation_file"],
        )

        batch_seen = set()

        for forecast in batch["forecasts"]:
            match_id = str(forecast["match_id"])

            require(
                match_id not in batch_seen,
                f"Duplicate forecast in batch: {match_id}",
            )

            batch_seen.add(match_id)

            require(
                forecast["competition"] == "Ligue1"
                and forecast["production_eligible"] is False,
                f"Invalid forecast: {match_id}",
            )

            name = sha(
                f"Ligue1:{match_id}".encode()
            )[:24]

            target = DEST / f"ligue1_{name}.json"

            if target.exists():
                obs = load_json(target)

                require(
                    str(obs["match_id"]) == match_id,
                    f"Filename identity collision: {match_id}",
                )

                for field in ("home", "away"):
                    require(
                        obs["forecast"][field] == forecast[field],
                        f"Team mismatch: {match_id}",
                    )

                stats["EXISTING"] += 1
                encountered.add(match_id)
                continue

            # A match can occur in multiple historical batches.
            # It must only be selected once for registration.
            if match_id in encountered:
                stats["REPEATED"] += 1
                continue

            encountered.add(match_id)

            row = history["matches"].get(match_id)

            require(
                row is not None,
                f"Historical match missing: {match_id}",
            )

            require(
                row["date"] == forecast["date"],
                f"Historical date mismatch: {match_id}",
            )

            match_date = datetime.fromisoformat(
                forecast["date"]
            ).date()

            day_start = datetime.combine(
                match_date,
                time.min,
                tzinfo=timezone.utc,
            )

            if (
                created + MIN_LEAD >= day_start
                or now + MIN_LEAD >= day_start
            ):
                stats["TOO_LATE"] += 1
                continue

            require(
                row.get("time_source") == "open_matchday"
                and row.get("time_status") == "UNVERIFIED",
                f"Invalid time provenance: {match_id}",
            )

            record = dict(schema='bubus_v8_ligue1_date_observation_v1',
                competition='Ligue1', match_id=match_id, forecast=forecast,
                forecast_date=forecast['date'], forecast_created_utc=created.isoformat(),
                forecast_batch_file=batch_path.name, forecast_batch_sha256=sha(raw),
                forecast_snapshot_id=batch['snapshot_id'], provider_id=history['provider_id'],
                provider_date=row['date'], provider_time_raw=row.get('time'),
                provider_time_source=row.get('time_source'), provider_time_status=row.get('time_status'),
                kickoff_candidate_utc=None, kickoff_verified=False,
                status='RESEARCH_REGISTERED_DATE_ONLY', production_eligible=False,
                registered_utc=now.isoformat())
            planned.append((target, record))
            stats["NEW_ELIGIBLE"] += 1

            print(
                "NEW ELIGIBLE:",
                forecast["date"],
                forecast["home"],
                "—",
                forecast["away"],
            )

    print()
    print("=== FINAL SUMMARY ===")

    for key in (
        "EXISTING",
        "REPEATED",
        "NEW_ELIGIBLE",
        "TOO_LATE",
    ):
        print(f"{key}: {stats[key]}")

    require(
        len(existing_ids) >= 18,
        "Original observations missing",
    )

    print()
    print("V2 READ-ONLY AUDIT: PASSED")
    written = 0
    if args.write:
        from forecast_service import write_once
        for target, record in planned:
            if write_once(target, record):
                written += 1
            verify_existing(target, record['match_id'])
    print("Observations written:", written)
    print("Telegram modified: NO")


if __name__ == "__main__":
    main()
