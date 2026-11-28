#!/usr/bin/env python3
"""Immutable date-only registration of 18 Ligue1 V8 forecasts."""

import argparse
import hashlib
import json
import os
import re
import tempfile

from pathlib import Path
from datetime import datetime, timezone, date

import football_europe_ligue1_monitor_v2 as monitor


ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "state/football_v8_ligue1_shadow"
DEST = FOLDER / "observations"

EXPECTED_PROVIDER_ID = (
    "43a609bef6ac3f095bae6484c8821b0a476eef2ff00dae88d011206c5f749012"
)

EXPECTED_BATCH_SHA256 = (
    "bfcda28c1a970986177d21f76774f53d4ad5db9fb45532598243ec9b4e9efd87"
)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def utc(value):
    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    require(dt.tzinfo is not None, "Timezone missing")
    return dt.astimezone(timezone.utc)


def write_immutable(path, record):
    raw = canonical(record)

    path.parent.mkdir(parents=True, exist_ok=True)

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
        os.link(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    print("=== BUBUS V8 LIGUE1 DATE-ONLY REGISTRATION ===")
    print("MODE:", "WRITE" if args.write else "DRY RUN")

    batches = sorted(FOLDER.glob("batch_*.json"))
    require(len(batches) == 1, "Expected exactly one research batch")

    batch_path = batches[0]
    raw = batch_path.read_bytes()
    batch = json.loads(raw)

    batch_sha = sha(raw)

    require(
        batch_sha == EXPECTED_BATCH_SHA256,
        "Original forecast batch changed",
    )

    require(batch["competition"] == "Ligue1", "Wrong league")
    require(batch["production_eligible"] is False, "Production flag")
    require(len(batch["forecasts"]) == 18, "Wrong forecast count")

    created = utc(batch["created_utc"])

    # Validate complete provenance chain.
    content, states, provider, observation = (
        monitor.load_previous_observation()
    )

    require(observation is not None, "Missing observation")

    provider_id = monitor.dataset_id(provider)

    require(
        provider_id == EXPECTED_PROVIDER_ID,
        "Provider changed; historical replay required",
    )

    # Prove current provider is still identical to initial baseline.
    _, _, baseline_provider, _ = monitor.load_initial_baseline()

    require(
        monitor.dataset_id(baseline_provider) == provider_id,
        "Provider changed from baseline",
    )

    indexed = {}

    for row in provider["matches"]:
        key = str(row["match_id"])
        require(key not in indexed, "Duplicate provider match")
        indexed[key] = row

    require(len(indexed) == 306, "Provider coverage mismatch")

    now = datetime.now(timezone.utc)

    planned = []
    existing = 0
    seen = set()

    for forecast in batch["forecasts"]:

        match_id = str(forecast["match_id"])

        require(match_id not in seen, "Duplicate forecast")
        seen.add(match_id)

        row = indexed.get(match_id)
        require(row is not None, f"Missing match: {match_id}")

        match_date = forecast["date"]

        require(
            match_date == row.get("date"),
            f"Date mismatch: {match_id}",
        )

        require(
            date.fromisoformat(match_date) > now.date(),
            f"Match date not in future: {match_id}",
        )

        require(
            created <= now,
            "Forecast timestamp is in the future",
        )

        time_value = row.get("time")

        require(
            isinstance(time_value, str)
            and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value),
            f"Invalid local time: {match_id}",
        )

        require(
            row.get("time_source") == "open_matchday",
            f"Unexpected time source: {match_id}",
        )

        require(
            row.get("time_status") == "UNVERIFIED",
            f"Unexpected time status: {match_id}",
        )

        record = {
            "schema": "bubus_v8_ligue1_date_observation_v1",
            "competition": "Ligue1",
            "match_id": match_id,

            "forecast": forecast,
            "forecast_date": match_date,

            "forecast_created_utc": created.isoformat(),
            "forecast_batch_file": batch_path.name,
            "forecast_batch_sha256": batch_sha,
            "forecast_snapshot_id": batch["snapshot_id"],

            "provider_id": provider_id,
            "provider_date": row["date"],
            "provider_time_raw": time_value,
            "provider_time_source": row["time_source"],
            "provider_time_status": "UNVERIFIED",

            "kickoff_candidate_utc": None,
            "kickoff_verified": False,

            "status": "RESEARCH_REGISTERED_DATE_ONLY",
            "production_eligible": False,
        }

        name = sha(f"Ligue1:{match_id}".encode())[:24]
        target = DEST / f"ligue1_{name}.json"

        if target.exists():
            saved = json.loads(target.read_bytes())

            original_record = dict(saved)
            original_record.pop("registered_utc", None)

            require(
                original_record == record,
                f"Existing observation conflict: {match_id}",
            )

            existing += 1
            print("EXISTS:", forecast["home"], "—", forecast["away"])
            continue

        record["registered_utc"] = now.isoformat()

        planned.append((target, record))

        print(
            "NEW:",
            match_date,
            time_value,
            forecast["home"],
            "—",
            forecast["away"],
        )

    print()
    print("=== SUMMARY ===")
    print("Forecasts:", len(batch["forecasts"]))
    print("Existing:", existing)
    print("New:", len(planned))
    print("Provider ID:", provider_id)
    print("Kickoff UTC: UNKNOWN")
    print("Telegram: UNCHANGED")

    if not args.write:
        print("DRY RUN COMPLETE")
        return

    for target, record in planned:
        write_immutable(target, record)

    print("WRITTEN:", len(planned))
    print("PRODUCTION: DISABLED")


if __name__ == "__main__":
    main()
