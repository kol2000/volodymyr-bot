#!/usr/bin/env python3
"""Bubus Bundesliga Monitor V3.

Three independent observation streams:
- calendar
- states
- provider schedule

Default: READ ONLY.
--write: persist observation and changed snapshots.

Calendar changes require separate DFL verification.

No forecast recording.
"""

import argparse
import fcntl
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_season_v1 as season
import football_europe_bundesliga_network_v1 as network
import football_europe_bundesliga_monitor_v2 as previous
import football_europe_bundesliga_observations_audit_v1 as audit


BASE = Path(__file__).resolve().parent / (
    "state/football_live/calendars"
)

STATES = BASE / "bundesliga_states"
PROVIDER = BASE / "bundesliga_provider"
OBSERVATIONS = BASE / "bundesliga_observations"

LOCK = BASE / "bundesliga_monitor.lock"

SCHEMA = "bubus-bundesliga-observation-v1"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sync_dir(path):

    fd = os.open(
        path,
        os.O_RDONLY,
    )

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_file(path, raw):
    """Atomic no-overwrite file publication."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".staging-",
        dir=path.parent,
    )

    temporary = Path(temporary)

    try:

        with os.fdopen(fd, "wb") as output:

            output.write(raw)
            output.flush()

            os.fsync(
                output.fileno()
            )

        require(
            temporary.read_bytes() == raw,
            "Staging verification failed",
        )

        # Atomic publication.
        # Never replace an existing file.

        os.link(
            temporary,
            path,
        )

        sync_dir(path.parent)

    finally:

        temporary.unlink(
            missing_ok=True
        )


def save_snapshot(directory, value):

    identifier = previous.dataset_id(
        value
    )

    raw = calendar.canonical(value)

    require(
        season.sha(raw) == identifier,
        "Snapshot SHA256 mismatch",
    )

    path = directory / (
        identifier + ".json"
    )

    if path.exists():

        require(
            path.read_bytes() == raw,
            "Existing snapshot mismatch",
        )

        return False

    publish_file(
        path,
        raw,
    )

    require(
        path.read_bytes() == raw,
        "Published snapshot mismatch",
    )

    return True


def calculate(old, new):

    old_ids = tuple(
        previous.dataset_id(item)
        for item in old
    )

    new_ids = tuple(
        previous.dataset_id(item)
        for item in new
    )

    events = previous.compare_all(
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

        require(
            changed == bool(events[index]),
            name + ": ID/events mismatch",
        )

    return old_ids, new_ids, events


def run(write):

    print("=== BUBUS BUNDESLIGA MONITOR V3 ===")

    print(
        "MODE:",
        "WRITE" if write else "READ ONLY",
    )

    if write:
        print("\n=== PRE-WRITE AUDIT ===")
        audit.audit(verbose=False)
        print("PRE-WRITE AUDIT: PASSED")

    print("\n=== PREVIOUS OBSERVATION ===")

    (
        old_content,
        old_states,
        old_provider,
        observation,
    ) = previous.load_previous_observation()

    old = (
        old_content,
        old_states,
        old_provider,
    )

    print(
        "Baseline:",
        "LATEST OBSERVATION"
        if observation
        else "INITIAL ARCHIVE",
    )

    print("\n=== CURRENT OPENLIGADB ===")

    source_raw, source = network.download()

    fetched = datetime.now(
        timezone.utc
    )

    content, states, provider = (
        calendar.normalize(source)
    )

    previous.validate_datasets(
        content,
        states,
        provider,
    )

    new = (
        content,
        states,
        provider,
    )

    old_ids, new_ids, events = calculate(
        old,
        new,
    )

    names = (
        "CALENDAR",
        "STATES",
        "PROVIDER",
    )

    print("\n=== CONTENT IDS ===")

    for index, name in enumerate(names):

        print(
            name,
            "OLD:",
            old_ids[index],
        )

        print(
            name,
            "NEW:",
            new_ids[index],
        )

    print("\n=== CHANGES ===")

    for index, name in enumerate(names):

        print(
            name + ":",
            len(events[index]),
            "events",
        )

        for event in events[index][:5]:

            print(
                calendar.canonical(event)
                .decode("utf-8")
            )

    if not write:

        print("\n=== FINAL STATUS ===")
        print("COMPARISON: VALID")
        print("READ ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return

    print("\n=== WRITE SAFETY ===")

    # The official DFL PDF was validated against
    # the original fixture set.
    #
    # Calendar updates need an additional check.
    #
    # For now, fail closed instead of publishing
    # an unverified calendar version.

    require(
        old_ids[0] == new_ids[0],
        "CALENDAR_CHANGED: manual DFL "
        "verification required"
    )

    # The currently referenced calendar archive
    # must already exist and validate.

    previous.load_calendar(
        new_ids[0]
    )

    print("Calendar: VERIFIED EXISTING ARCHIVE")

    print("\n=== SAVING SNAPSHOTS ===")

    states_created = save_snapshot(
        STATES,
        states,
    )

    provider_created = save_snapshot(
        PROVIDER,
        provider,
    )

    print(
        "New states snapshot:",
        states_created,
    )

    print(
        "New provider snapshot:",
        provider_created,
    )

    observation_data = {
        "schema": SCHEMA,
        "fetched_utc": fetched.isoformat(),
        "previous_calendar_id": old_ids[0],
        "calendar_id": new_ids[0],
        "previous_states_id": old_ids[1],
        "states_id": new_ids[1],
        "previous_provider_id": old_ids[2],
        "provider_id": new_ids[2],
        "calendar_changed": False,
        "states_changed": old_ids[1] != new_ids[1],
        "provider_changed": old_ids[2] != new_ids[2],
        "source_sha256": season.sha(source_raw),
        "match_count": 306,
        "calendar_events": events[0],
        "state_events": events[1],
        "provider_events": events[2],
    }

    name = (
        fetched.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        + "-"
        + new_ids[0][:12]
        + ".json"
    )

    publish_file(
        OBSERVATIONS / name,
        calendar.canonical(observation_data),
    )

    print("\n=== POST-WRITE AUDIT ===")
    audit.audit(verbose=False)
    print("POST-WRITE AUDIT: PASSED")

    print("\n=== OBSERVATION ===")

    print("Saved:", name)

    print("\n=== FINAL STATUS ===")

    print("OBSERVATION SAVED")
    print("CALENDAR ARCHIVES: UNCHANGED")
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

    BASE.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LOCK.open("a+b") as lock:

        fcntl.flock(
            lock,
            fcntl.LOCK_EX,
        )

        run(True)


if __name__ == "__main__":
    main()
