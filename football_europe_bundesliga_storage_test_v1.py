#!/usr/bin/env python3
"""Bubus Bundesliga Storage Tests V1.

Offline storage integrity tests.

Uses the immutable archived season as input.
Writes ONLY to a temporary directory.

No network requests.
No production state writes.
No forecast recording.
"""

import json
import tempfile

from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_monitor_v3 as monitor
import football_europe_bundesliga_monitor_v2 as previous
import football_europe_bundesliga_season_v1 as season


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_archived_data():

    archives = [
        path
        for path in season.ARCHIVES.iterdir()
        if (
            path.is_dir()
            and season.HASH_RE.fullmatch(path.name)
        )
    ]

    require(
        len(archives) == 1,
        "Expected one initial archive",
    )

    archive = archives[0]

    season.validate_archive(archive)

    source = json.loads(
        (archive / "source.json").read_bytes()
    )

    return calendar.normalize(source)


def main():

    print("=== BUNDESLIGA STORAGE TEST V1 ===")
    print("MODE: OFFLINE / ISOLATED")

    content, states, provider = (
        load_archived_data()
    )

    print("\n=== BASELINE ===")

    print("Calendar:", len(content["matches"]))
    print("States:", len(states))
    print("Provider:", len(provider))

    with tempfile.TemporaryDirectory(
        prefix="bubus-bundesliga-test-"
    ) as directory:

        root = Path(directory)

        states_dir = root / "states"
        provider_dir = root / "provider"
        observations_dir = root / "observations"

        print("\n=== TEST 1: SAVE STATES ===")

        created = monitor.save_snapshot(
            states_dir,
            states,
        )

        require(
            created is True,
            "States snapshot was not created",
        )

        states_id = previous.dataset_id(
            states
        )

        restored = previous.load_snapshot(
            states_dir,
            states_id,
        )

        require(
            restored == states,
            "States reconstruction mismatch",
        )

        print("PASS: states saved and verified")

        print("\n=== TEST 2: SAVE PROVIDER ===")

        created = monitor.save_snapshot(
            provider_dir,
            provider,
        )

        require(
            created is True,
            "Provider snapshot was not created",
        )

        provider_id = previous.dataset_id(
            provider
        )

        restored = previous.load_snapshot(
            provider_dir,
            provider_id,
        )

        require(
            restored == provider,
            "Provider reconstruction mismatch",
        )

        print("PASS: provider saved and verified")

        print("\n=== TEST 3: IDEMPOTENCE ===")

        states_created = monitor.save_snapshot(
            states_dir,
            states,
        )

        provider_created = monitor.save_snapshot(
            provider_dir,
            provider,
        )

        require(
            states_created is False,
            "Duplicate states snapshot",
        )

        require(
            provider_created is False,
            "Duplicate provider snapshot",
        )

        print("PASS: no duplicate snapshots")

        print("\n=== TEST 4: CORRUPTION DETECTION ===")

        snapshot = (
            states_dir / (states_id + ".json")
        )

        original_bytes = snapshot.read_bytes()

        snapshot.write_bytes(
            original_bytes + b"\n"
        )

        detected = False

        try:

            previous.load_snapshot(
                states_dir,
                states_id,
            )

        except RuntimeError:
            detected = True

        require(
            detected,
            "Corruption was not detected",
        )

        print("PASS: corruption detected")

        print("\n=== TEST 5: NO OVERWRITE ===")

        observation_path = (
            observations_dir / "test.json"
        )

        monitor.publish_file(
            observation_path,
            b'{"test":1}',
        )

        blocked = False

        try:

            monitor.publish_file(
                observation_path,
                b'{"test":2}',
            )

        except FileExistsError:
            blocked = True

        require(
            blocked,
            "Existing observation was overwritten",
        )

        require(
            observation_path.read_bytes()
            == b'{"test":1}',
            "Existing observation changed",
        )

        print("PASS: overwrite blocked")

        print("\n=== TEST 6: SNAPSHOT COUNTS ===")

        require(
            len(list(states_dir.glob("*.json")))
            == 1,
            "Invalid states snapshot count",
        )

        require(
            len(list(provider_dir.glob("*.json")))
            == 1,
            "Invalid provider snapshot count",
        )

        require(
            len(list(observations_dir.glob("*.json")))
            == 1,
            "Invalid observation count",
        )

        print("PASS: snapshot counts valid")

    print("\n=== FINAL STATUS ===")

    print("ALL 6 TESTS PASSED")
    print("TEMPORARY FILES: REMOVED")
    print("PRODUCTION ARCHIVES: UNCHANGED")
    print("NO NETWORK REQUESTS")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
