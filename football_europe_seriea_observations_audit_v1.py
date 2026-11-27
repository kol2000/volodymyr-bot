#!/usr/bin/env python3
"""Bubus Serie A Observations Audit V1.

Validate calendar archives, state snapshots
and observation event chain.

STRICTLY READ ONLY.
No API requests.
No forecast recording.
"""

import json
import re
import sys

from datetime import datetime, timezone
from pathlib import Path

import football_europe_seriea_calendar_v1 as calendar
import football_europe_seriea_season_v1 as season
import football_europe_seriea_monitor_v1 as compare
import football_europe_seriea_monitor_v2 as monitor


BASE = Path(__file__).resolve().parent / (
    "state/football_live/calendars"
)

ARCHIVES = BASE / "seriea_season"
STATES = BASE / "seriea_states"
OBSERVATIONS = BASE / "seriea_observations"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

calendar_cache = {}
state_cache = {}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_hash(value):
    return (
        isinstance(value, str)
        and HASH_RE.fullmatch(value) is not None
    )


def load_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def load_calendar(content_id):

    require(
        valid_hash(content_id),
        "Invalid calendar ID",
    )

    if content_id not in calendar_cache:

        path = ARCHIVES / content_id

        season.validate_archive(path)

        content = load_json(
            path / "calendar.json"
        )

        require(
            calendar.content_id(content) == content_id,
            "Calendar Content ID mismatch",
        )

        calendar_cache[content_id] = content

    return calendar_cache[content_id]


def load_states(state_id, content):

    require(
        valid_hash(state_id),
        "Invalid state ID",
    )

    if state_id not in state_cache:

        path = STATES / (state_id + ".json")

        raw = path.read_bytes()

        require(
            season.sha(raw) == state_id,
            "State SHA256 mismatch",
        )

        states = json.loads(raw)

        require(
            calendar.canonical(states) == raw,
            "Noncanonical state snapshot",
        )

        state_cache[state_id] = states

    states = state_cache[state_id]

    monitor.validate_states(
        states,
        content,
    )

    return states


def initial_baseline(first_previous_id):

    content = load_calendar(
        first_previous_id
    )

    source = load_json(
        ARCHIVES
        / first_previous_id
        / "source.json"
    )

    reconstructed, states = (
        calendar.normalize(source)
    )

    require(
        calendar.canonical(reconstructed)
        == calendar.canonical(content),
        "Initial archive reconstruction mismatch",
    )

    monitor.validate_states(
        states,
        content,
    )

    return content, states


def main():

    print(
        "=== BUBUS SERIE A OBSERVATIONS AUDIT V1 ==="
    )

    print("MODE: READ ONLY")

    require(
        OBSERVATIONS.is_dir(),
        "Observations directory missing",
    )

    files = sorted(
        OBSERVATIONS.glob("*.json")
    )

    require(
        len(files) > 0,
        "No observations found",
    )

    old_content = None
    old_states = None

    previous_stamp = None
    previous_calendar_id = None
    previous_state_id = None

    total_schedule_events = 0
    total_state_events = 0

    for index, path in enumerate(files, 1):

        data = load_json(path)

        require(
            data.get("schema")
            == "bubus-seriea-observation-v2",
            "Invalid observation schema",
        )

        stamp = datetime.fromisoformat(
            data["fetched_utc"]
        )

        require(
            stamp.tzinfo is not None,
            "Timestamp has no timezone",
        )

        stamp = stamp.astimezone(
            timezone.utc
        )

        require(
            stamp <= datetime.now(timezone.utc),
            "Observation timestamp is in future",
        )

        current_calendar_id = data["content_id"]
        current_state_id = data["state_id"]

        old_calendar_id = data["previous_content_id"]
        old_state_id = data["previous_state_id"]

        for identifier in (
            current_calendar_id,
            current_state_id,
            old_calendar_id,
            old_state_id,
        ):
            require(
                valid_hash(identifier),
                "Invalid SHA256 identifier",
            )

        expected_name = (
            stamp.strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + current_calendar_id[:12]
            + ".json"
        )

        require(
            path.name == expected_name,
            "Observation filename mismatch",
        )

        current_content = load_calendar(
            current_calendar_id
        )

        current_states = load_states(
            current_state_id,
            current_content,
        )

        if index == 1:

            old_content, old_states = initial_baseline(
                old_calendar_id
            )

            require(
                monitor.state_id(old_states)
                == old_state_id,
                "Initial state ID mismatch",
            )

        else:

            require(
                stamp > previous_stamp,
                "Non-monotonic timestamps",
            )

            require(
                old_calendar_id
                == previous_calendar_id,
                "Calendar observation chain broken",
            )

            require(
                old_state_id
                == previous_state_id,
                "State observation chain broken",
            )

        expected_schedule = compare.compare_calendar(
            old_content["matches"],
            current_content["matches"],
        )

        expected_states = compare.compare_states(
            old_states,
            current_states,
        )

        require(
            calendar.canonical(expected_schedule)
            == calendar.canonical(
                data["schedule_events"]
            ),
            "Recorded schedule events mismatch",
        )

        require(
            calendar.canonical(expected_states)
            == calendar.canonical(
                data["state_events"]
            ),
            "Recorded state events mismatch",
        )

        calendar_changed = (
            current_calendar_id != old_calendar_id
        )

        states_changed = (
            current_state_id != old_state_id
        )

        require(
            type(data["calendar_changed"]) is bool
            and data["calendar_changed"]
            == calendar_changed,
            "Calendar changed flag mismatch",
        )

        require(
            type(data["states_changed"]) is bool
            and data["states_changed"]
            == states_changed,
            "States changed flag mismatch",
        )

        require(
            calendar_changed == bool(expected_schedule),
            "Calendar ID/events inconsistency",
        )

        require(
            states_changed == bool(expected_states),
            "State ID/events inconsistency",
        )

        require(
            type(data["match_count"]) is int
            and data["match_count"] == 380,
            "Invalid match count",
        )

        require(
            valid_hash(data.get("source_sha256")),
            "Invalid source SHA256",
        )

        total_schedule_events += len(expected_schedule)
        total_state_events += len(expected_states)

        print(
            f"OK: {index:03d}",
            stamp.isoformat(),
            "| schedule:",
            len(expected_schedule),
            "| state:",
            len(expected_states),
        )

        old_content = current_content
        old_states = current_states

        previous_stamp = stamp
        previous_calendar_id = current_calendar_id
        previous_state_id = current_state_id

    print("\n=== SUMMARY ===")

    print("Observations:", len(files))

    print(
        "Calendar archives validated:",
        len(calendar_cache),
    )

    print(
        "State snapshots validated:",
        len(state_cache),
    )

    print(
        "Schedule events:",
        total_schedule_events,
    )

    print(
        "State events:",
        total_state_events,
    )

    print("Calendar chain: OK")
    print("State chain: OK")
    print("Event reconstruction: OK")

    print("\nSTATUS: AUDIT PASSED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:
        print(
            "\nSTATUS: AUDIT FAILED",
            type(exc).__name__,
            str(exc),
        )
        sys.exit(2)
