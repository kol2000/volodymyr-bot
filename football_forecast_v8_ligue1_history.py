#!/usr/bin/env python3
"""Historical Ligue1 provider lookup for immutable V8 research."""

import json
from datetime import datetime, timezone
from pathlib import Path

import football_europe_ligue1_monitor_v2 as monitor


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_utc(value):
    result = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    require(
        result.tzinfo is not None,
        "Timestamp missing timezone",
    )

    return result.astimezone(timezone.utc)


def historical_provider(as_of):
    """Return archived provider observed before as_of."""

    as_of = parse_utc(as_of)

    # Verify the complete historical chain first.
    monitor.load_previous_observation()

    _, _, baseline, _ = (
        monitor.load_initial_baseline()
    )

    baseline_id = monitor.dataset_id(baseline)

    observation_dir = (
        ROOT
        / "state/football_live/calendars"
        / "ligue1_observations"
    )

    paths = sorted(
        observation_dir.glob("*.json")
    )

    require(
        paths,
        "No historical provider observations",
    )

    selected = None

    for path in paths:

        observation = json.loads(
            path.read_bytes()
        )

        fetched = parse_utc(
            observation["fetched_utc"]
        )

        if fetched <= as_of:
            selected = (
                path,
                observation,
                fetched,
            )
        else:
            break

    require(
        selected is not None,
        "No provider observation before forecast",
    )

    path, observation, fetched = selected

    provider_id = observation["provider_id"]

    if provider_id == baseline_id:
        provider = baseline
        source = "INITIAL_ARCHIVE"
    else:
        provider = monitor.load_snapshot(
            monitor.PROVIDER,
            provider_id,
        )
        source = "HISTORICAL_SNAPSHOT"

    require(
        monitor.dataset_id(provider) == provider_id,
        "Provider checksum mismatch",
    )

    rows = provider["matches"]

    require(
        len(rows) == 306,
        "Unexpected provider match count",
    )

    indexed = {}

    for row in rows:
        match_id = str(row["match_id"])

        require(
            match_id not in indexed,
            "Duplicate historical match ID",
        )

        indexed[match_id] = row

    return {
        "provider": provider,
        "provider_id": provider_id,
        "provider_source": source,
        "observation_file": path.name,
        "observation_fetched_utc": fetched.isoformat(),
        "matches": indexed,
    }


def main():
    folder = (
        ROOT
        / "state/football_v8_ligue1_shadow"
    )

    batches = sorted(
        folder.glob("batch_*.json")
    )

    print(
        "=== BUBUS V8 LIGUE1 HISTORICAL PROVIDER ==="
    )

    print("MODE: READ ONLY")

    require(
        batches,
        "No research batches",
    )

    for batch_path in batches:

        batch = json.loads(
            batch_path.read_bytes()
        )

        require(
            batch["competition"] == "Ligue1",
            "Unexpected competition",
        )

        history = historical_provider(
            batch["created_utc"]
        )

        print()
        print("Batch:", batch_path.name)

        print(
            "Created:",
            batch["created_utc"],
        )

        print(
            "Historical observation:",
            history["observation_file"],
        )

        print(
            "Provider observed:",
            history["observation_fetched_utc"],
        )

        print(
            "Provider ID:",
            history["provider_id"],
        )

        print(
            "Provider source:",
            history["provider_source"],
        )

        matched = 0
        mismatched = 0

        for forecast in batch["forecasts"]:

            match_id = str(
                forecast["match_id"]
            )

            row = history["matches"].get(
                match_id
            )

            require(
                row is not None,
                f"Missing historical fixture: {match_id}",
            )

            if row["date"] == forecast["date"]:
                matched += 1
            else:
                mismatched += 1

                print(
                    "DATE MISMATCH:",
                    match_id,
                    forecast["date"],
                    row["date"],
                )

        print("Forecasts:", len(batch["forecasts"]))
        print("Dates matched:", matched)
        print("Date mismatches:", mismatched)

        require(
            mismatched == 0,
            "Historical calendar mismatch",
        )

    print()
    print("HISTORICAL PROVIDER AUDIT: PASSED")
    print("Forecasts modified: NO")
    print("Telegram modified: NO")


if __name__ == "__main__":
    main()
