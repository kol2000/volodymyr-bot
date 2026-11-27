#!/usr/bin/env python3
"""Bubus Serie A Monitor V2.

Compare live official API against latest recorded state.

Current version: READ ONLY.

No archive writes.
No observation writes.
No forecast recording.
"""

import hashlib
import json
import re

from pathlib import Path
from collections import Counter

import football_europe_seriea_calendar_v1 as calendar
import football_europe_seriea_season_v1 as season
import football_europe_seriea_monitor_v1 as monitor


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars"

ARCHIVES = BASE / "seriea_season"
STATES = BASE / "seriea_states"
OBSERVATIONS = BASE / "seriea_observations"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_hash(value):
    return (
        isinstance(value, str)
        and HASH_RE.fullmatch(value) is not None
    )


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def state_id(states):
    return hashlib.sha256(
        calendar.canonical(states)
    ).hexdigest()


def index_states(states):
    result = {}

    for state in states:
        match_id = state["id"]

        require(
            match_id not in result,
            "Duplicate state ID",
        )

        result[match_id] = state

    return result


def validate_states(states, content):

    require(
        isinstance(states, list),
        "States must be a list",
    )

    require(
        len(states) == 380,
        "Expected 380 states",
    )

    state_index = index_states(states)

    match_ids = {
        match["id"]
        for match in content["matches"]
    }

    require(
        set(state_index) == match_ids,
        "State IDs do not match calendar",
    )

    require(
        states == sorted(
            states,
            key=lambda item: item["id"],
        ),
        "States are not sorted",
    )


def load_calendar(content_id):

    require(
        valid_hash(content_id),
        "Invalid calendar Content ID",
    )

    path = ARCHIVES / content_id

    season.validate_archive(path)

    content = read_json(
        path / "calendar.json"
    )

    require(
        calendar.content_id(content) == content_id,
        "Calendar Content ID mismatch",
    )

    return content


def load_original_baseline():

    require(
        ARCHIVES.is_dir(),
        "Calendar archive directory missing",
    )

    archives = [
        path
        for path in ARCHIVES.iterdir()
        if (
            path.is_dir()
            and not path.name.startswith(".")
        )
    ]

    require(
        len(archives) == 1,
        "Expected one initial calendar archive",
    )

    archive = archives[0]

    season.validate_archive(archive)

    content = read_json(
        archive / "calendar.json"
    )

    source = read_json(
        archive / "source.json"
    )

    reconstructed, states = (
        calendar.normalize(source)
    )

    require(
        calendar.canonical(reconstructed)
        == calendar.canonical(content),
        "Baseline reconstruction mismatch",
    )

    validate_states(states, content)

    return content, states, None


def load_previous_observation():

    if not OBSERVATIONS.is_dir():
        return load_original_baseline()

    files = sorted(
        OBSERVATIONS.glob("*.json")
    )

    if not files:
        return load_original_baseline()

    latest = files[-1]

    observation = read_json(latest)

    require(
        observation.get("schema")
        == "bubus-seriea-observation-v2",
        "Invalid observation schema",
    )

    content_id = observation["content_id"]
    previous_state_id = observation["state_id"]

    require(
        valid_hash(previous_state_id),
        "Invalid state Content ID",
    )

    content = load_calendar(content_id)

    state_path = (
        STATES / (previous_state_id + ".json")
    )

    raw = state_path.read_bytes()

    require(
        hashlib.sha256(raw).hexdigest()
        == previous_state_id,
        "State checksum mismatch",
    )

    states = json.loads(raw)

    require(
        calendar.canonical(states) == raw,
        "State snapshot is not canonical",
    )

    validate_states(states, content)

    return content, states, observation


def state_summary(states):

    return {
        "statuses": dict(
            Counter(
                state["status"]
                for state in states
            )
        ),
        "phases": dict(
            Counter(
                state["phase"]
                for state in states
            )
        ),
    }


def main():

    print("=== BUBUS SERIE A MONITOR V2 ===")
    print("MODE: READ ONLY")

    print("\n=== PREVIOUS STATE ===")

    old_content, old_states, previous = (
        load_previous_observation()
    )

    old_id = calendar.content_id(
        old_content
    )

    old_state_id = state_id(
        old_states
    )

    print(
        "Baseline:",
        "LATEST OBSERVATION"
        if previous
        else "INITIAL ARCHIVE"
    )

    print("Calendar ID:", old_id)
    print("State ID:", old_state_id)

    print("\n=== LIVE OFFICIAL API ===")

    source_raw, source = season.download()

    new_content, new_states = (
        calendar.normalize(source)
    )

    validate_states(
        new_states,
        new_content,
    )

    new_id = calendar.content_id(
        new_content
    )

    new_state_id = state_id(
        new_states
    )

    print("Matches:", len(new_content["matches"]))
    print("States:", len(new_states))

    print("Calendar ID:", new_id)
    print("State ID:", new_state_id)

    print(
        "Raw API SHA256:",
        season.sha(source_raw),
    )

    print("\n=== CALENDAR CHANGES ===")

    schedule_events = monitor.compare_calendar(
        old_content["matches"],
        new_content["matches"],
    )

    print(
        "Schedule events:",
        len(schedule_events),
    )

    for event in schedule_events[:10]:

        print(
            json.dumps(
                event,
                ensure_ascii=False,
            )
        )

    print("\n=== MATCH STATE CHANGES ===")

    state_events = monitor.compare_states(
        old_states,
        new_states,
    )

    print(
        "State events:",
        len(state_events),
    )

    for event in state_events[:10]:

        print(
            json.dumps(
                event,
                ensure_ascii=False,
            )
        )

    print("\n=== STATE SUMMARY ===")

    summary = state_summary(new_states)

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n=== CONSISTENCY ===")

    calendar_changed = old_id != new_id
    states_changed = old_state_id != new_state_id

    require(
        calendar_changed == bool(schedule_events),
        "Calendar Content ID/events mismatch",
    )

    require(
        states_changed == bool(state_events),
        "State Content ID/events mismatch",
    )

    print(
        "Calendar changed:",
        calendar_changed,
    )

    print(
        "States changed:",
        states_changed,
    )

    print("\n=== FINAL STATUS ===")

    print("STRUCTURE: VALID")
    print("COMPARISON: VALID")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
