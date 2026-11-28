#!/usr/bin/env python3
"""Bubus Ligue 1 Monitor V2.

Compare fresh data with latest recorded observation.

Datasets:
- Calendar
- States
- Provider Schedule

Initial baseline:
- Immutable Ligue 1 season archive.

Future baseline:
- Latest validated observation.

STRICTLY READ ONLY.

No snapshots.
No observations.
No archive writes.
No forecast recording.
"""

import hashlib
import json
import re

from pathlib import Path

import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_monitor_v1 as monitor
import football_europe_ligue1_season_v1 as season


BASE = (
    Path(__file__).resolve().parent
    / "state/football_live/calendars"
)

STATES = BASE / "ligue1_states"
PROVIDER = BASE / "ligue1_provider"
OBSERVATIONS = BASE / "ligue1_observations"

SCHEMA = "bubus-ligue1-observation-v1"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

NAME_RE = re.compile(
    r"^(\d{8}T\d{12}Z)-([0-9a-f]{12})\.json$"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def dataset_id(value):
    return hashlib.sha256(
        calendar.canonical(value)
    ).hexdigest()


def validate_datasets(content, states, provider):

    calendar.validate_datasets(
        content,
        states,
        provider,
    )

    for name, data in (
        ("Calendar", content),
        ("States", states),
        ("Provider", provider),
    ):
        require(
            len(data["matches"]) == 306,
            name + ": invalid match count",
        )


def load_snapshot(directory, identifier):

    require(
        HASH_RE.fullmatch(identifier) is not None,
        "Invalid snapshot identifier",
    )

    path = directory / (identifier + ".json")

    require(
        path.is_file(),
        "Snapshot missing: " + str(path),
    )

    raw = path.read_bytes()
    value = json.loads(raw)

    require(
        calendar.canonical(value) == raw,
        "Noncanonical snapshot: " + str(path),
    )

    require(
        dataset_id(value) == identifier,
        "Snapshot checksum mismatch: " + str(path),
    )

    return value


def load_initial_baseline():

    (
        archive,
        manifest,
        content,
        states,
        provider,
    ) = monitor.load_archive()

    validate_datasets(
        content,
        states,
        provider,
    )

    ids = (
        dataset_id(content),
        dataset_id(states),
        dataset_id(provider),
    )

    require(
        ids == (
            manifest["calendar_id"],
            manifest["initial_states_id"],
            manifest["initial_provider_id"],
        ),
        "Initial archive identifiers mismatch",
    )

    return content, states, provider, None


def compare_all(old, new):

    old_calendar, old_states, old_provider = old
    new_calendar, new_states, new_provider = new

    return (
        monitor.compare_entries(
            old_calendar,
            new_calendar,
        ),
        monitor.compare_entries(
            old_states,
            new_states,
        ),
        monitor.compare_entries(
            old_provider,
            new_provider,
        ),
    )


def load_previous_observation():

    content, states, provider, _ = (
        load_initial_baseline()
    )

    if not OBSERVATIONS.exists():
        return content, states, provider, None

    require(
        OBSERVATIONS.is_dir(),
        "Observations path is not a directory",
    )

    paths = sorted(
        OBSERVATIONS.glob("*.json")
    )

    if not paths:
        return content, states, provider, None

    previous = None
    previous_timestamp = None

    for path in paths:

        match = NAME_RE.fullmatch(path.name)

        require(
            match is not None,
            "Invalid observation filename: " + path.name,
        )

        timestamp, prefix = match.groups()

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
            observation.get("schema") == SCHEMA,
            "Observation schema mismatch",
        )

        old_ids = (
            dataset_id(content),
            dataset_id(states),
            dataset_id(provider),
        )

        require(
            observation.get("previous_calendar_id")
            == old_ids[0],
            "Calendar chain broken",
        )

        require(
            observation.get("previous_states_id")
            == old_ids[1],
            "States chain broken",
        )

        require(
            observation.get("previous_provider_id")
            == old_ids[2],
            "Provider chain broken",
        )

        new_ids = (
            observation.get("calendar_id"),
            observation.get("states_id"),
            observation.get("provider_id"),
        )

        require(
            all(
                isinstance(item, str)
                and HASH_RE.fullmatch(item)
                for item in new_ids
            ),
            "Invalid observation dataset IDs",
        )

        require(
            new_ids[0] == old_ids[0],
            "Unverified calendar modification",
        )

        require(
            prefix == new_ids[0][:12],
            "Observation filename ID mismatch",
        )

        require(
            observation.get("match_count") == 306,
            "Observation match count mismatch",
        )

        require(
            observation.get("calendar_changed") is False,
            "Calendar change flag invalid",
        )

        require(
            observation.get("states_changed")
            is (old_ids[1] != new_ids[1]),
            "States change flag invalid",
        )

        require(
            observation.get("provider_changed")
            is (old_ids[2] != new_ids[2]),
            "Provider change flag invalid",
        )

        new_states = (
            states
            if new_ids[1] == old_ids[1]
            else load_snapshot(STATES, new_ids[1])
        )

        new_provider = (
            provider
            if new_ids[2] == old_ids[2]
            else load_snapshot(PROVIDER, new_ids[2])
        )

        validate_datasets(
            content,
            new_states,
            new_provider,
        )

        events = compare_all(
            (content, states, provider),
            (content, new_states, new_provider),
        )

        require(
            observation.get("calendar_events")
            == events[0],
            "Calendar events mismatch",
        )

        require(
            observation.get("state_events")
            == events[1],
            "States events mismatch",
        )

        require(
            observation.get("provider_events")
            == events[2],
            "Provider events mismatch",
        )

        require(
            observation.get("calendar_events") == [],
            "Unexpected calendar events",
        )

        states = new_states
        provider = new_provider

        previous = observation
        previous_timestamp = timestamp

    return content, states, provider, previous


def run():

    print("=== BUBUS LIGUE 1 MONITOR V2 ===")
    print("MODE: STRICTLY READ ONLY")

    print("\n=== PREVIOUS OBSERVATION ===")

    (
        old_calendar,
        old_states,
        old_provider,
        observation,
    ) = load_previous_observation()

    if observation is None:
        print("Previous observation: NONE")
        print("Baseline: IMMUTABLE ARCHIVE")
    else:
        print("Previous observation: FOUND")
        print(
            "Previous States ID:",
            observation["states_id"],
        )
        print(
            "Previous Provider ID:",
            observation["provider_id"],
        )

    print("\n=== CURRENT SOURCES ===")

    archive, manifest, _, _, _ = (
        monitor.load_archive()
    )

    raw = monitor.fetch_current_sources()

    raw["lfp_official.pdf"] = (
        archive / "lfp_official.pdf"
    ).read_bytes()

    (
        new_calendar,
        new_states,
        new_provider,
    ) = season.normalize_sources(raw)

    validate_datasets(
        new_calendar,
        new_states,
        new_provider,
    )

    old_ids = (
        dataset_id(old_calendar),
        dataset_id(old_states),
        dataset_id(old_provider),
    )

    new_ids = (
        dataset_id(new_calendar),
        dataset_id(new_states),
        dataset_id(new_provider),
    )

    print("\n=== DATASET IDS ===")

    for name, old_id, new_id in zip(
        ("Calendar", "States", "Provider"),
        old_ids,
        new_ids,
    ):
        print(name + ":")
        print("  Previous:", old_id)
        print("  Current: ", new_id)

    require(
        old_ids[0] == new_ids[0],
        "CALENDAR_CHANGED: manual verification required",
    )

    events = compare_all(
        (old_calendar, old_states, old_provider),
        (new_calendar, new_states, new_provider),
    )

    monitor.print_events("CALENDAR EVENTS", events[0])
    monitor.print_events("STATE EVENTS", events[1])
    monitor.print_events("PROVIDER EVENTS", events[2])

    print("\n=== SUMMARY ===")

    print("Calendar changed:", False)
    print("States changed:", old_ids[1] != new_ids[1])
    print("Provider changed:", old_ids[2] != new_ids[2])

    print("\n=== FINAL STATUS ===")

    print("INITIAL ARCHIVE: VALID")
    print("OBSERVATION HISTORY: VALID")
    print("CURRENT SOURCES: VALID")
    print("CALENDAR: UNCHANGED")
    print("CHANGE DETECTION: PASSED")
    print("NO SNAPSHOTS CREATED")
    print("NO OBSERVATIONS CREATED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    run()
