#!/usr/bin/env python3
"""Bubus Ligue 1 Observations Audit V1.

Independently validate the complete observation chain.

Checks:
- immutable season archive
- canonical observation JSON
- observation filenames and timestamps
- dataset ID continuity
- snapshot SHA256
- event consistency
- calendar immutability

STRICTLY READ ONLY / OFFLINE.

No downloads.
No archive writes.
No observation writes.
No forecasts.
"""

import json
from collections import Counter
from datetime import datetime, timezone

import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_monitor_v1 as monitor
import football_europe_ligue1_monitor_v2 as previous


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def audit(
    observations_dir=None,
    states_dir=None,
    provider_dir=None,
    verbose=True,
):

    observations_dir = (
        observations_dir
        if observations_dir is not None
        else previous.OBSERVATIONS
    )

    states_dir = (
        states_dir
        if states_dir is not None
        else previous.STATES
    )

    provider_dir = (
        provider_dir
        if provider_dir is not None
        else previous.PROVIDER
    )

    if verbose:
        print("=== LIGUE 1 OBSERVATIONS AUDIT V1 ===")
        print("MODE: READ ONLY / OFFLINE")

    (
        archive,
        manifest,
        content,
        states,
        provider,
    ) = monitor.load_archive()

    previous.validate_datasets(
        content,
        states,
        provider,
    )

    initial_ids = (
        previous.dataset_id(content),
        previous.dataset_id(states),
        previous.dataset_id(provider),
    )

    require(
        initial_ids == (
            manifest["calendar_id"],
            manifest["initial_states_id"],
            manifest["initial_provider_id"],
        ),
        "Initial archive IDs mismatch",
    )

    if verbose:
        print("\n=== INITIAL ARCHIVE ===")
        print("Calendar:", initial_ids[0])
        print("States:", initial_ids[1])
        print("Provider:", initial_ids[2])
        print("Initial archive: VALID")

    if observations_dir.exists():

        require(
            observations_dir.is_dir(),
            "Observations path is not a directory",
        )

        paths = sorted(
            observations_dir.glob("*.json")
        )

    else:
        paths = []

    counts = Counter()

    previous_timestamp = None

    if verbose:
        print("\n=== OBSERVATIONS ===")
        print("Observation files:", len(paths))

    for index, path in enumerate(paths, 1):

        filename_match = (
            previous.NAME_RE.fullmatch(path.name)
        )

        require(
            filename_match is not None,
            "Invalid observation filename: " + path.name,
        )

        stamp, prefix = filename_match.groups()

        timestamp = datetime.strptime(
            stamp,
            "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc)

        require(
            previous_timestamp is None
            or timestamp > previous_timestamp,
            "Observation timestamps not increasing",
        )

        raw = path.read_bytes()

        observation = json.loads(raw)

        require(
            calendar.canonical(observation) == raw,
            "Noncanonical observation: " + path.name,
        )

        require(
            observation.get("schema") == previous.SCHEMA,
            "Observation schema mismatch",
        )

        fetched = datetime.fromisoformat(
            observation["fetched_utc"]
        )

        require(
            fetched.tzinfo is not None,
            "Observation timestamp lacks timezone",
        )

        require(
            fetched.astimezone(timezone.utc) == timestamp,
            "Filename/fetched timestamp mismatch",
        )

        old_ids = (
            previous.dataset_id(content),
            previous.dataset_id(states),
            previous.dataset_id(provider),
        )

        require(
            observation["previous_calendar_id"] == old_ids[0],
            "Previous Calendar ID mismatch",
        )

        require(
            observation["previous_states_id"] == old_ids[1],
            "Previous States ID mismatch",
        )

        require(
            observation["previous_provider_id"] == old_ids[2],
            "Previous Provider ID mismatch",
        )

        new_ids = (
            observation["calendar_id"],
            observation["states_id"],
            observation["provider_id"],
        )

        require(
            all(
                isinstance(identifier, str)
                and previous.HASH_RE.fullmatch(identifier)
                for identifier in new_ids
            ),
            "Invalid dataset identifier",
        )

        require(
            new_ids[0] == initial_ids[0],
            "Unverified Calendar ID",
        )

        require(
            prefix == new_ids[0][:12],
            "Filename Calendar ID mismatch",
        )

        require(
            observation.get("match_count") == 306,
            "Match count mismatch",
        )

        for field in (
            "fixtures_sha256",
            "results_sha256",
        ):

            value = observation.get(field)

            require(
                isinstance(value, str)
                and previous.HASH_RE.fullmatch(value),
                "Invalid source hash: " + field,
            )

        new_states = (
            states
            if new_ids[1] == old_ids[1]
            else previous.load_snapshot(
                states_dir,
                new_ids[1],
            )
        )

        new_provider = (
            provider
            if new_ids[2] == old_ids[2]
            else previous.load_snapshot(
                provider_dir,
                new_ids[2],
            )
        )

        previous.validate_datasets(
            content,
            new_states,
            new_provider,
        )

        events = previous.compare_all(
            (content, states, provider),
            (content, new_states, new_provider),
        )

        expected_flags = (
            False,
            old_ids[1] != new_ids[1],
            old_ids[2] != new_ids[2],
        )

        actual_flags = (
            observation.get("calendar_changed"),
            observation.get("states_changed"),
            observation.get("provider_changed"),
        )

        require(
            actual_flags == expected_flags,
            "Dataset change flags mismatch",
        )

        require(
            observation.get("calendar_events") == events[0],
            "Calendar events mismatch",
        )

        require(
            observation.get("state_events") == events[1],
            "State events mismatch",
        )

        require(
            observation.get("provider_events") == events[2],
            "Provider events mismatch",
        )

        require(
            events[0] == [],
            "Unexpected Calendar events",
        )

        counts["calendar"] += len(events[0])
        counts["states"] += len(events[1])
        counts["provider"] += len(events[2])

        states = new_states
        provider = new_provider

        previous_timestamp = timestamp

        if verbose:
            print(
                f"Observation {index}: VALID"
                f" | States={len(events[1])}"
                f" | Provider={len(events[2])}"
            )

    latest_ids = (
        previous.dataset_id(content),
        previous.dataset_id(states),
        previous.dataset_id(provider),
    )

    result = {
        "observations": len(paths),
        "events": (
            counts["calendar"],
            counts["states"],
            counts["provider"],
        ),
        "latest_ids": latest_ids,
    }

    if verbose:

        print("\n=== SUMMARY ===")
        print("Validated observations:", len(paths))
        print("Calendar events:", counts["calendar"])
        print("States events:", counts["states"])
        print("Provider events:", counts["provider"])

        print("\n=== LATEST IDS ===")
        print("Calendar:", latest_ids[0])
        print("States:", latest_ids[1])
        print("Provider:", latest_ids[2])

        print("\n=== FINAL STATUS ===")
        print("INITIAL ARCHIVE: VALID")
        print("OBSERVATION CHAIN: VALID")
        print("AUDIT: PASSED")
        print("NO NETWORK REQUESTS")
        print("NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")

    return result


if __name__ == "__main__":
    audit()
