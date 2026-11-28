#!/usr/bin/env python3
"""Bubus Ligue 1 Monitor V3.

Monitor and optionally record observations.

--check: read only
--write: create immutable observations and snapshots

Calendar changes are prohibited.

No forecasts.
No changes to initial season archive.
"""

import argparse
import fcntl
import hashlib
import json
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path

import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_monitor_v1 as monitor
import football_europe_ligue1_monitor_v2 as previous
import football_europe_ligue1_observations_audit_v1 as auditor
import football_europe_ligue1_season_v1 as season


BASE = (
    Path(__file__).resolve().parent
    / "state/football_live/calendars"
)

STATES = BASE / "ligue1_states"
PROVIDER = BASE / "ligue1_provider"
OBSERVATIONS = BASE / "ligue1_observations"

LOCK = BASE / "ligue1_monitor.lock"

SCHEMA = "bubus-ligue1-observation-v1"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sync_dir(path):

    fd = os.open(
        str(path),
        os.O_RDONLY,
    )

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_file(path, raw):
    """Atomically publish without overwriting."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".ligue1-",
        dir=path.parent,
    )

    try:

        with os.fdopen(fd, "wb") as output:

            output.write(raw)
            output.flush()
            os.fsync(output.fileno())

        os.link(
            temporary,
            path,
        )

        sync_dir(path.parent)

    finally:

        if os.path.exists(temporary):
            os.unlink(temporary)


def save_snapshot(directory, value):

    identifier = calendar.dataset_id(value)

    path = directory / (
        identifier + ".json"
    )

    raw = calendar.canonical(value)

    require(
        sha(raw) == identifier,
        "Snapshot ID mismatch",
    )

    if path.exists():

        require(
            path.is_file(),
            "Snapshot path is not a file",
        )

        require(
            path.read_bytes() == raw,
            "Existing snapshot content mismatch",
        )

        return False

    publish_file(path, raw)

    return True


def calculate(old, new):

    old_ids = tuple(
        calendar.dataset_id(value)
        for value in old
    )

    new_ids = tuple(
        calendar.dataset_id(value)
        for value in new
    )

    events = previous.compare_all(
        old,
        new,
    )

    for index in range(3):

        require(
            (old_ids[index] != new_ids[index])
            == bool(events[index]),
            "Dataset change/event mismatch",
        )

    return old_ids, new_ids, events


def run(write=False):

    print("=== BUBUS LIGUE 1 MONITOR V3 ===")

    print(
        "MODE:",
        "WRITE" if write else "READ ONLY",
    )

    if write:
        print("\n=== INDEPENDENT PRE-WRITE AUDIT ===")
        auditor.audit(verbose=False)
        print("PRE-WRITE AUDIT: PASSED")

    print("\n=== PRE-WRITE HISTORY CHECK ===")

    old = previous.load_previous_observation()

    old_calendar, old_states, old_provider, last = old

    print(
        "Previous observation:",
        "FOUND" if last else "NONE",
    )

    print("\n=== INITIAL ARCHIVE ===")

    archive, manifest, _, _, _ = (
        monitor.load_archive()
    )

    print(
        "Calendar ID:",
        manifest["calendar_id"],
    )

    print("\n=== CURRENT SOURCES ===")

    raw = monitor.fetch_current_sources()

    raw["lfp_official.pdf"] = (
        archive / "lfp_official.pdf"
    ).read_bytes()

    fetched = datetime.now(timezone.utc)

    new = season.normalize_sources(raw)

    new_calendar, new_states, new_provider = new

    previous.validate_datasets(*new)

    old_datasets = (
        old_calendar,
        old_states,
        old_provider,
    )

    old_ids, new_ids, events = calculate(
        old_datasets,
        new,
    )

    print("\n=== DATASET IDS ===")

    for name, old_id, new_id in zip(
        ("Calendar", "States", "Provider"),
        old_ids,
        new_ids,
    ):

        print(name)
        print("  Previous:", old_id)
        print("  Current: ", new_id)

    print("\n=== CHANGES ===")

    monitor.print_events(
        "CALENDAR EVENTS",
        events[0],
    )

    monitor.print_events(
        "STATE EVENTS",
        events[1],
    )

    monitor.print_events(
        "PROVIDER EVENTS",
        events[2],
    )

    require(
        old_ids[0] == new_ids[0],
        "CALENDAR_CHANGED: manual verification required",
    )

    require(
        not events[0],
        "Unexpected calendar events",
    )

    if not write:

        print("\n=== FINAL STATUS ===")

        print("CHECK: PASSED")
        print("NO SNAPSHOTS CREATED")
        print("NO OBSERVATIONS CREATED")
        print("FORECAST RECORDING: BLOCKED")

        return

    print("\n=== SAVING SNAPSHOTS ===")

    # Initial archive remains immutable.
    # Only changed datasets need new snapshots.

    states_created = False
    provider_created = False

    if old_ids[1] != new_ids[1]:

        states_created = save_snapshot(
            STATES,
            new_states,
        )

    if old_ids[2] != new_ids[2]:

        provider_created = save_snapshot(
            PROVIDER,
            new_provider,
        )

    print(
        "States snapshot created:",
        states_created,
    )

    print(
        "Provider snapshot created:",
        provider_created,
    )

    print("\n=== SAVING OBSERVATION ===")

    observation = {
        "schema": SCHEMA,
        "fetched_utc": fetched.isoformat(),
        "previous_calendar_id": old_ids[0],
        "calendar_id": new_ids[0],
        "previous_states_id": old_ids[1],
        "states_id": new_ids[1],
        "previous_provider_id": old_ids[2],
        "provider_id": new_ids[2],
        "calendar_changed": False,
        "states_changed": (
            old_ids[1] != new_ids[1]
        ),
        "provider_changed": (
            old_ids[2] != new_ids[2]
        ),
        "match_count": 306,
        "fixtures_sha256": sha(
            raw["fixtures_2627.csv"]
        ),
        "results_sha256": sha(
            raw["results_2627.csv"]
        ),
        "calendar_events": events[0],
        "state_events": events[1],
        "provider_events": events[2],
    }

    filename = (
        fetched.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        + "-"
        + new_ids[0][:12]
        + ".json"
    )

    publish_file(
        OBSERVATIONS / filename,
        calendar.canonical(observation),
    )

    print(
        "Observation:",
        filename,
    )

    print("\n=== INDEPENDENT POST-WRITE AUDIT ===")
    auditor.audit(verbose=False)
    print("POST-WRITE AUDIT: PASSED")

    print("\n=== POST-WRITE HISTORY CHECK ===")

    (
        checked_calendar,
        checked_states,
        checked_provider,
        checked_observation,
    ) = previous.load_previous_observation()

    checked_ids = (
        calendar.dataset_id(checked_calendar),
        calendar.dataset_id(checked_states),
        calendar.dataset_id(checked_provider),
    )

    require(
        checked_ids == new_ids,
        "Post-write dataset IDs mismatch",
    )

    require(
        checked_observation is not None,
        "Post-write observation missing",
    )

    print("POST-WRITE CHECK: PASSED")

    print("\n=== FINAL STATUS ===")

    print("OBSERVATION: SAVED")
    print("INITIAL ARCHIVE: UNCHANGED")
    print("HISTORY: VALID")
    print("FORECAST RECORDING: BLOCKED")


def main():

    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--check",
        action="store_true",
    )

    group.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if args.check:
        run(False)
        return

    BASE.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(LOCK, "a+b") as lock:

        fcntl.flock(
            lock.fileno(),
            fcntl.LOCK_EX,
        )

        run(True)


if __name__ == "__main__":
    main()
