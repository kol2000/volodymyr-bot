#!/usr/bin/env python3
"""Bubus Bundesliga Observations Audit V1.

Audit complete observation sequence.

Checks:
- initial immutable archive
- snapshot checksums
- previous/current ID continuity
- independently reconstructed events
- change flags
- chronological order
- observation filenames
- calendar safety

STRICTLY READ ONLY.
NO NETWORK REQUESTS.
NO FORECAST RECORDING.
"""

import json
import re

from datetime import datetime, timezone
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_monitor_v2 as previous


HASH_RE = re.compile(r"^[0-9a-f]{64}$")

FILENAME_RE = re.compile(
    r"^(\d{8}T\d{12}Z)-([0-9a-f]{12})\.json$"
)

SCHEMA = "bubus-bundesliga-observation-v1"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_hash(value):

    return (
        isinstance(value, str)
        and HASH_RE.fullmatch(value) is not None
    )


def read_observation(path):

    raw = path.read_bytes()

    value = json.loads(raw)

    require(
        isinstance(value, dict),
        "Observation is not an object: "
        + path.name,
    )

    require(
        calendar.canonical(value) == raw,
        "Noncanonical observation: "
        + path.name,
    )

    require(
        value.get("schema") == SCHEMA,
        "Invalid observation schema: "
        + path.name,
    )

    return value


def load_snapshot(directory, identifier):

    require(
        valid_hash(identifier),
        "Invalid snapshot ID",
    )

    return previous.load_snapshot(
        directory,
        identifier,
    )


def audit(
    observations_dir=None,
    states_dir=None,
    provider_dir=None,
    verbose=True,
):

    if observations_dir is None:
        observations_dir = previous.OBSERVATIONS

    if states_dir is None:
        states_dir = previous.STATES

    if provider_dir is None:
        provider_dir = previous.PROVIDER

    print("=== BUNDESLIGA OBSERVATIONS AUDIT V1 ===")
    print("MODE: READ ONLY / OFFLINE")

    print("\n=== INITIAL ARCHIVE ===")

    (
        initial_calendar,
        initial_states,
        initial_provider,
        initial_observation,
    ) = previous.load_initial_baseline()

    require(
        initial_observation is None,
        "Unexpected initial observation",
    )

    initial = (
        initial_calendar,
        initial_states,
        initial_provider,
    )

    initial_ids = tuple(
        previous.dataset_id(value)
        for value in initial
    )

    print("Calendar:", initial_ids[0])
    print("States:", initial_ids[1])
    print("Provider:", initial_ids[2])

    require(
        initial_ids[0]
        == (
            "e20b6cca4ccd73b50aa8789258a07284"
            "f164931830f29f67124814e75238768a"
        ),
        "Unexpected initial calendar ID",
    )

    print("Initial archive: VALID")

    print("\n=== OBSERVATIONS ===")

    if observations_dir.exists():

        require(
            observations_dir.is_dir(),
            "Observations path is not a directory",
        )

        files = sorted(
            observations_dir.glob("*.json")
        )

    else:
        files = []

    print("Observation files:", len(files))

    old = initial
    old_ids = initial_ids

    last_timestamp = None

    total_events = [0, 0, 0]

    for index, path in enumerate(files, 1):

        require(
            path.is_file(),
            "Invalid observation entry",
        )

        match = FILENAME_RE.fullmatch(
            path.name
        )

        require(
            match is not None,
            "Invalid observation filename: "
            + path.name,
        )

        observation = read_observation(path)

        stamp = observation.get("fetched_utc")

        require(
            isinstance(stamp, str),
            "Invalid fetched_utc",
        )

        fetched = datetime.fromisoformat(
            stamp.replace("Z", "+00:00")
        )

        require(
            fetched.tzinfo is not None,
            "Observation time lacks timezone",
        )

        fetched = fetched.astimezone(
            timezone.utc
        )

        expected_filename_stamp = (
            fetched.strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
        )

        require(
            match.group(1)
            == expected_filename_stamp,
            "Filename/time mismatch: "
            + path.name,
        )

        if last_timestamp is not None:

            require(
                fetched > last_timestamp,
                "Observation chronology broken",
            )

        last_timestamp = fetched

        require(
            observation.get("match_count") == 306,
            "Invalid match count",
        )

        require(
            valid_hash(
                observation.get("source_sha256")
            ),
            "Invalid source SHA256",
        )

        new_ids = (
            observation.get("calendar_id"),
            observation.get("states_id"),
            observation.get("provider_id"),
        )

        previous_ids = (
            observation.get("previous_calendar_id"),
            observation.get("previous_states_id"),
            observation.get("previous_provider_id"),
        )

        require(
            all(valid_hash(x) for x in new_ids),
            "Invalid current dataset ID",
        )

        require(
            all(valid_hash(x) for x in previous_ids),
            "Invalid previous dataset ID",
        )

        require(
            previous_ids == old_ids,
            "Observation chain broken: "
            + path.name,
        )

        require(
            match.group(2)
            == new_ids[0][:12],
            "Filename/calendar ID mismatch",
        )

        require(
            new_ids[0] == initial_ids[0],
            "Unverified calendar change",
        )

        new_calendar = previous.load_calendar(
            new_ids[0]
        )

        new_states = load_snapshot(
            states_dir,
            new_ids[1],
        )

        new_provider = load_snapshot(
            provider_dir,
            new_ids[2],
        )

        new = (
            new_calendar,
            new_states,
            new_provider,
        )

        previous.validate_datasets(
            *new
        )

        actual_ids = tuple(
            previous.dataset_id(value)
            for value in new
        )

        require(
            actual_ids == new_ids,
            "Snapshot/content ID mismatch",
        )

        calculated = previous.compare_all(
            old,
            new,
        )

        saved_events = (
            observation.get("calendar_events"),
            observation.get("state_events"),
            observation.get("provider_events"),
        )

        require(
            all(
                isinstance(events, list)
                for events in saved_events
            ),
            "Invalid event lists",
        )

        for position in range(3):

            require(
                calculated[position]
                == saved_events[position],
                "Recorded events mismatch: "
                + path.name
            )

            require(
                (old_ids[position] != new_ids[position])
                == bool(calculated[position]),
                "ID/events mismatch",
            )

            total_events[position] += len(
                calculated[position]
            )

        require(
            observation.get("calendar_changed")
            is False,
            "Unexpected calendar_changed",
        )

        require(
            observation.get("states_changed")
            is (old_ids[1] != new_ids[1]),
            "Incorrect states_changed",
        )

        require(
            observation.get("provider_changed")
            is (old_ids[2] != new_ids[2]),
            "Incorrect provider_changed",
        )

        if verbose:

            print(
                f"#{index} VALID:",
                path.name,
            )

            print(
                "   Events:",
                len(calculated[0]),
                len(calculated[1]),
                len(calculated[2]),
            )

        old = new
        old_ids = new_ids

    print("\n=== SUMMARY ===")

    print("Validated observations:", len(files))

    print(
        "Calendar events:",
        total_events[0],
    )

    print(
        "States events:",
        total_events[1],
    )

    print(
        "Provider events:",
        total_events[2],
    )

    print("\n=== LATEST IDS ===")

    print("Calendar:", old_ids[0])
    print("States:", old_ids[1])
    print("Provider:", old_ids[2])

    print("\n=== FINAL STATUS ===")

    if not files:

        print("INITIAL ARCHIVE: VALID")
        print("OBSERVATION CHAIN: EMPTY")
        print("READY FOR FIRST OBSERVATION")

    else:

        print("OBSERVATION CHAIN: VALID")

    print("NO NETWORK REQUESTS")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")

    return {
        "observations": len(files),
        "events": tuple(total_events),
        "latest_ids": old_ids,
    }


if __name__ == "__main__":
    audit()
