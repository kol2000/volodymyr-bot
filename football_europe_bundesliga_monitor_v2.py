#!/usr/bin/env python3
"""Bubus Bundesliga Monitor V2.

Compare OpenLigaDB with latest recorded observation.

Three independent datasets:
- Calendar
- States
- Provider schedule

Current version: STRICTLY READ ONLY.

No archive writes.
No observation writes.
No forecast recording.
"""

import hashlib
import json
import re

from collections import Counter
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_season_v1 as season
import football_europe_bundesliga_monitor_v1 as monitor


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars"

ARCHIVES = BASE / "bundesliga_season"
STATES = BASE / "bundesliga_states"
PROVIDER = BASE / "bundesliga_provider"
OBSERVATIONS = BASE / "bundesliga_observations"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_hash(value):
    return (
        isinstance(value, str)
        and HASH_RE.fullmatch(value) is not None
    )


def dataset_id(value):
    return hashlib.sha256(
        calendar.canonical(value)
    ).hexdigest()


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def validate_records(items, expected_ids, name):

    require(
        isinstance(items, list),
        name + ": expected list",
    )

    require(
        len(items) == 306,
        name + ": expected 306 records",
    )

    ids = []

    for item in items:

        require(
            isinstance(item, dict),
            name + ": invalid record",
        )

        identifier = item.get("id")

        require(
            isinstance(identifier, str),
            name + ": invalid match ID",
        )

        ids.append(identifier)

    require(
        len(set(ids)) == 306,
        name + ": duplicate IDs",
    )

    require(
        ids == sorted(ids),
        name + ": records are not sorted",
    )

    require(
        set(ids) == expected_ids,
        name + ": IDs differ from calendar",
    )


def validate_datasets(content, states, provider):

    require(
        isinstance(content, dict),
        "Calendar must be an object",
    )

    require(
        content.get("schema")
        == "bubus-bundesliga-calendar-v3",
        "Invalid calendar schema",
    )

    require(
        content.get("competition") == "DE1",
        "Invalid competition",
    )

    require(
        content.get("season") == "2026-27",
        "Invalid season",
    )

    matches = content["matches"]

    require(
        isinstance(matches, list)
        and len(matches) == 306,
        "Expected 306 calendar matches",
    )

    match_ids = [
        item["id"]
        for item in matches
    ]

    require(
        len(set(match_ids)) == 306,
        "Duplicate calendar IDs",
    )

    require(
        match_ids == sorted(match_ids),
        "Calendar is not sorted",
    )

    expected_ids = set(match_ids)

    validate_records(
        states,
        expected_ids,
        "States",
    )

    validate_records(
        provider,
        expected_ids,
        "Provider",
    )


def load_snapshot(directory, identifier):

    require(
        valid_hash(identifier),
        "Invalid snapshot ID",
    )

    path = directory / (
        identifier + ".json"
    )

    raw = path.read_bytes()

    require(
        hashlib.sha256(raw).hexdigest()
        == identifier,
        "Snapshot checksum mismatch: "
        + directory.name,
    )

    value = json.loads(raw)

    require(
        calendar.canonical(value) == raw,
        "Noncanonical snapshot: "
        + directory.name,
    )

    return value


def load_calendar(identifier):

    require(
        valid_hash(identifier),
        "Invalid Calendar ID",
    )

    archive = ARCHIVES / identifier

    season.validate_archive(archive)

    content = read_json(
        archive / "calendar.json"
    )

    require(
        dataset_id(content) == identifier,
        "Calendar ID mismatch",
    )

    return content


def load_initial_baseline():

    require(
        ARCHIVES.is_dir(),
        "Archive directory missing",
    )

    archives = [
        path
        for path in ARCHIVES.iterdir()
        if (
            path.is_dir()
            and HASH_RE.fullmatch(path.name)
        )
    ]

    require(
        len(archives) == 1,
        "Expected exactly one initial archive",
    )

    archive = archives[0]

    season.validate_archive(archive)

    source = read_json(
        archive / "source.json"
    )

    content, states, provider = (
        calendar.normalize(source)
    )

    validate_datasets(
        content,
        states,
        provider,
    )

    return (
        content,
        states,
        provider,
        None,
    )


def load_previous_observation():

    if not OBSERVATIONS.is_dir():
        return load_initial_baseline()

    files = sorted(
        OBSERVATIONS.glob("*.json")
    )

    if not files:
        return load_initial_baseline()

    latest = files[-1]

    observation = read_json(latest)

    require(
        observation.get("schema")
        == "bubus-bundesliga-observation-v1",
        "Invalid observation schema",
    )

    calendar_id = observation["calendar_id"]
    states_id = observation["states_id"]
    provider_id = observation["provider_id"]

    content = load_calendar(
        calendar_id
    )

    states = load_snapshot(
        STATES,
        states_id,
    )

    provider = load_snapshot(
        PROVIDER,
        provider_id,
    )

    validate_datasets(
        content,
        states,
        provider,
    )

    return (
        content,
        states,
        provider,
        observation,
    )


def compare_all(old, new):

    old_content, old_states, old_provider = old
    new_content, new_states, new_provider = new

    calendar_events = monitor.compare_datasets(
        old_content["matches"],
        new_content["matches"],
        "CALENDAR",
    )

    state_events = monitor.compare_datasets(
        old_states,
        new_states,
        "STATE",
    )

    provider_events = monitor.compare_datasets(
        old_provider,
        new_provider,
        "PROVIDER",
    )

    return (
        calendar_events,
        state_events,
        provider_events,
    )


def main():

    print("=== BUBUS BUNDESLIGA MONITOR V2 ===")
    print("MODE: READ ONLY")

    print("\n=== PREVIOUS STATE ===")

    (
        old_content,
        old_states,
        old_provider,
        observation,
    ) = load_previous_observation()

    old = (
        old_content,
        old_states,
        old_provider,
    )

    old_ids = tuple(
        dataset_id(item)
        for item in old
    )

    print(
        "Baseline:",
        "LATEST OBSERVATION"
        if observation
        else "INITIAL ARCHIVE",
    )

    print("Calendar ID:", old_ids[0])
    print("States ID:", old_ids[1])
    print("Provider ID:", old_ids[2])

    print("\n=== CURRENT OPENLIGADB ===")

    source_raw, source = season.download()

    content, states, provider = (
        calendar.normalize(source)
    )

    validate_datasets(
        content,
        states,
        provider,
    )

    new = (
        content,
        states,
        provider,
    )

    new_ids = tuple(
        dataset_id(item)
        for item in new
    )

    print("Calendar matches:", len(content["matches"]))
    print("State records:", len(states))
    print("Provider records:", len(provider))

    print("Calendar ID:", new_ids[0])
    print("States ID:", new_ids[1])
    print("Provider ID:", new_ids[2])

    print(
        "Source SHA256:",
        season.sha(source_raw),
    )

    print("\n=== COMPARISON ===")

    events = compare_all(
        old,
        new,
    )

    names = (
        "CALENDAR",
        "STATES",
        "PROVIDER",
    )

    for index, name in enumerate(names):

        changed = (
            old_ids[index] != new_ids[index]
        )

        event_count = len(events[index])

        require(
            changed == bool(event_count),
            name + ": ID/events mismatch",
        )

        print(
            name + ":",
            "CHANGED" if changed else "UNCHANGED",
            "| events:",
            event_count,
        )

        for event in events[index][:5]:

            print(
                json.dumps(
                    event,
                    ensure_ascii=False,
                )
            )

    print("\n=== CURRENT STATES ===")

    counts = Counter(
        state["status"]
        for state in states
    )

    print(dict(sorted(counts.items())))

    print("\n=== FINAL STATUS ===")

    print("THREE DATASETS: VALID")
    print("COMPARISON: VALID")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
