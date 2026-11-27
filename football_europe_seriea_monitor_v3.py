#!/usr/bin/env python3
"""Bubus Serie A Monitor V3.

Official API observation journal.

Default: READ ONLY.
--write: persist calendar, state snapshot and observation.

Forecast recording is never performed.
"""

import argparse
import fcntl
import json
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path

import football_europe_seriea_calendar_v1 as calendar
import football_europe_seriea_season_v1 as season
import football_europe_seriea_monitor_v1 as compare
import football_europe_seriea_monitor_v2 as previous


BASE = Path(__file__).resolve().parent / (
    "state/football_live/calendars"
)

ARCHIVES = BASE / "seriea_season"
STATES = BASE / "seriea_states"
OBSERVATIONS = BASE / "seriea_observations"

LOCK = BASE / "seriea_monitor.lock"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_file(path, raw):
    """Atomically publish a file without replacing an existing one."""

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary = tempfile.mkstemp(
        prefix=".staging-",
        dir=path.parent,
    )

    temporary = Path(temporary)

    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())

        require(
            temporary.read_bytes() == raw,
            "Staged file verification failed",
        )

        # Atomic, no-overwrite publication.
        # Both paths are on the same filesystem.
        os.link(temporary, path)

        sync_directory(path.parent)

    finally:
        temporary.unlink(missing_ok=True)


def save_states(states):
    raw = calendar.canonical(states)

    identifier = previous.state_id(states)

    require(
        season.sha(raw) == identifier,
        "State checksum mismatch",
    )

    path = STATES / (identifier + ".json")

    if path.exists():
        require(
            path.read_bytes() == raw,
            "Existing state snapshot mismatch",
        )

        return False

    publish_file(path, raw)

    return True


def save_calendar(content, source_raw, fetched):
    identifier = calendar.content_id(content)

    path = ARCHIVES / identifier

    if path.exists():
        season.validate_archive(path)
        return False

    content_raw = calendar.canonical(content)

    manifest = {
        "schema": "bubus-seriea-season-archive-v1",
        "competition": "IT1",
        "season": "2026-27",
        "content_id": identifier,
        "calendar_sha256": season.sha(content_raw),
        "source_sha256": season.sha(source_raw),
        "source_url": season.source_url(),
        "fetched_utc": fetched,
        "match_count": 380,
    }

    season.publish_archive(
        path,
        {
            "calendar.json": content_raw,
            "source.json": source_raw,
            "manifest.json": calendar.canonical(manifest),
        },
    )

    season.validate_archive(path)

    return True


def run(write):

    print("=== BUBUS SERIE A MONITOR V3 ===")
    print("MODE:", "WRITE" if write else "READ ONLY")

    print("\n=== PREVIOUS OBSERVATION ===")

    old_content, old_states, last = (
        previous.load_previous_observation()
    )

    old_calendar_id = calendar.content_id(
        old_content
    )

    old_state_id = previous.state_id(
        old_states
    )

    print(
        "Baseline:",
        "LATEST OBSERVATION" if last
        else "INITIAL ARCHIVE",
    )

    print("Calendar ID:", old_calendar_id)
    print("State ID:", old_state_id)

    print("\n=== LIVE OFFICIAL API ===")

    source_raw, source = season.download()

    fetched = datetime.now(
        timezone.utc
    )

    content, states = calendar.normalize(
        source
    )

    previous.validate_states(
        states,
        content,
    )

    current_calendar_id = calendar.content_id(
        content
    )

    current_state_id = previous.state_id(
        states
    )

    print("Matches:", len(content["matches"]))
    print("States:", len(states))
    print("Calendar ID:", current_calendar_id)
    print("State ID:", current_state_id)

    print("\n=== CHANGES ===")

    schedule_events = compare.compare_calendar(
        old_content["matches"],
        content["matches"],
    )

    state_events = compare.compare_states(
        old_states,
        states,
    )

    calendar_changed = (
        current_calendar_id != old_calendar_id
    )

    states_changed = (
        current_state_id != old_state_id
    )

    require(
        calendar_changed == bool(schedule_events),
        "Calendar ID/events mismatch",
    )

    require(
        states_changed == bool(state_events),
        "State ID/events mismatch",
    )

    print(
        "Schedule events:",
        len(schedule_events),
    )

    print(
        "State events:",
        len(state_events),
    )

    for event in schedule_events[:10]:
        print(
            "SCHEDULE:",
            json.dumps(event, ensure_ascii=False),
        )

    for event in state_events[:10]:
        print(
            "STATE:",
            json.dumps(event, ensure_ascii=False),
        )

    if not write:
        print("\nREAD ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return

    print("\n=== SAVING ===")

    archive_created = save_calendar(
        content,
        source_raw,
        fetched.isoformat(),
    )

    states_created = save_states(states)

    observation = {
        "schema": "bubus-seriea-observation-v2",
        "fetched_utc": fetched.isoformat(),
        "previous_content_id": old_calendar_id,
        "content_id": current_calendar_id,
        "previous_state_id": old_state_id,
        "state_id": current_state_id,
        "calendar_changed": calendar_changed,
        "states_changed": states_changed,
        "source_sha256": season.sha(source_raw),
        "match_count": 380,
        "schedule_events": schedule_events,
        "state_events": state_events,
    }

    name = (
        fetched.strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + current_calendar_id[:12]
        + ".json"
    )

    publish_file(
        OBSERVATIONS / name,
        calendar.canonical(observation),
    )

    print("New calendar archive:", archive_created)
    print("New state snapshot:", states_created)
    print("Observation:", name)

    print("\n=== FINAL STATUS ===")
    print("OBSERVATION SAVED")
    print("FORECAST RECORDING: BLOCKED")


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.write:
        run(False)
        return

    BASE.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        run(True)


if __name__ == "__main__":
    main()
