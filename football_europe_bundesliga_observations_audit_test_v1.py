#!/usr/bin/env python3
"""Bubus Bundesliga Observations Audit Tests V1.

Offline isolated observation chain tests.

All test files live in TemporaryDirectory.

No network requests.
No production state writes.
No forecast recording.
"""

import copy
import contextlib
import io
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_monitor_v2 as previous
import football_europe_bundesliga_monitor_v3 as monitor
import football_europe_bundesliga_observations_audit_v1 as audit


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_baseline():

    content, states, provider, observation = (
        previous.load_initial_baseline()
    )

    require(
        observation is None,
        "Unexpected initial observation",
    )

    return content, states, provider


def modify_states(states):

    result = copy.deepcopy(states)

    target = next(
        item for item in result
        if item["status"] == "FINISHED"
    )

    target["home_score"] += 1

    return result


def modify_provider(provider):

    result = copy.deepcopy(provider)

    target = next(
        item for item in result
        if item["gameweek"] == 19
    )

    old = datetime.fromisoformat(
        target["candidate_kickoff_utc"]
    )

    new = old + timedelta(hours=1)

    target["candidate_kickoff_utc"] = (
        new.isoformat()
    )

    target["source_date_utc"] = (
        new.isoformat().replace(
            "+00:00", "Z"
        )
    )

    return result


def create_observation(
    old,
    new,
    timestamp,
    states_dir,
    provider_dir,
    observations_dir,
):

    old_ids, new_ids, events = (
        monitor.calculate(old, new)
    )

    require(
        old_ids[0] == new_ids[0],
        "Unexpected calendar change in test",
    )

    monitor.save_snapshot(
        states_dir,
        new[1],
    )

    monitor.save_snapshot(
        provider_dir,
        new[2],
    )

    observation = {
        "schema": monitor.SCHEMA,
        "fetched_utc": timestamp.isoformat(),

        "previous_calendar_id": old_ids[0],
        "calendar_id": new_ids[0],

        "previous_states_id": old_ids[1],
        "states_id": new_ids[1],

        "previous_provider_id": old_ids[2],
        "provider_id": new_ids[2],

        "calendar_changed": False,
        "states_changed": old_ids[1] != new_ids[1],
        "provider_changed": old_ids[2] != new_ids[2],

        "source_sha256": "a" * 64,
        "match_count": 306,

        "calendar_events": events[0],
        "state_events": events[1],
        "provider_events": events[2],
    }

    filename = (
        timestamp.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        + "-"
        + new_ids[0][:12]
        + ".json"
    )

    path = observations_dir / filename

    monitor.publish_file(
        path,
        calendar.canonical(observation),
    )

    return path


def run_audit(
    observations_dir,
    states_dir,
    provider_dir,
):

    # Capture verbose output so individual tests
    # produce concise diagnostic results.

    with contextlib.redirect_stdout(
        io.StringIO()
    ):
        return audit.audit(
            observations_dir=observations_dir,
            states_dir=states_dir,
            provider_dir=provider_dir,
            verbose=False,
        )


def expect_failure(label, action):

    try:
        action()

    except Exception as exc:

        print(
            "PASS:",
            label,
            "->",
            type(exc).__name__,
            str(exc)[:130],
        )

        return

    raise RuntimeError(
        "AUDIT DID NOT DETECT: " + label
    )


def mutate_json(path, change):

    raw = path.read_bytes()

    value = audit.read_observation(path)

    change(value)

    path.write_bytes(
        calendar.canonical(value)
    )

    return raw


def restore(path, raw):

    path.write_bytes(raw)


def main():

    print(
        "=== BUNDESLIGA OBSERVATIONS AUDIT TEST V1 ==="
    )

    print("MODE: OFFLINE / ISOLATED")

    initial = load_baseline()

    print("\n=== INITIAL DATA ===")

    print(
        "Calendar:",
        len(initial[0]["matches"]),
    )

    print("States:", len(initial[1]))
    print("Provider:", len(initial[2]))

    with tempfile.TemporaryDirectory(
        prefix="bubus-bundesliga-chain-test-"
    ) as tmp:

        root = Path(tmp)

        states_dir = root / "states"
        provider_dir = root / "provider"
        observations_dir = root / "observations"

        base_time = datetime(
            2026,
            11,
            22,
            18,
            0,
            0,
            tzinfo=timezone.utc,
        )

        # Observation 1: unchanged baseline.

        first = copy.deepcopy(initial)

        path1 = create_observation(
            initial,
            first,
            base_time,
            states_dir,
            provider_dir,
            observations_dir,
        )

        # Observation 2: state change.

        second = (
            first[0],
            modify_states(first[1]),
            first[2],
        )

        path2 = create_observation(
            first,
            second,
            base_time + timedelta(minutes=10),
            states_dir,
            provider_dir,
            observations_dir,
        )

        # Observation 3: provider change.

        third = (
            second[0],
            second[1],
            modify_provider(second[2]),
        )

        path3 = create_observation(
            second,
            third,
            base_time + timedelta(minutes=20),
            states_dir,
            provider_dir,
            observations_dir,
        )

        print("\n=== TEST 1: VALID CHAIN ===")

        result = run_audit(
            observations_dir,
            states_dir,
            provider_dir,
        )

        require(
            result["observations"] == 3,
            "Expected 3 observations",
        )

        require(
            result["events"] == (0, 1, 1),
            "Unexpected event counts",
        )

        print(
            "PASS: three observations verified"
        )

        print(
            "Events:",
            result["events"],
        )

        print(
            "\n=== TEST 2: CORRUPTED STATES ==="
        )

        states_id = previous.dataset_id(
            second[1]
        )

        snapshot = (
            states_dir / (states_id + ".json")
        )

        original_bytes = snapshot.read_bytes()

        snapshot.write_bytes(
            original_bytes + b"\n"
        )

        expect_failure(
            "corrupted States snapshot",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(snapshot, original_bytes)

        print(
            "\n=== TEST 3: CORRUPTED PROVIDER ==="
        )

        provider_id = previous.dataset_id(
            third[2]
        )

        snapshot = (
            provider_dir / (provider_id + ".json")
        )

        original_bytes = snapshot.read_bytes()

        snapshot.write_bytes(
            original_bytes + b"\n"
        )

        expect_failure(
            "corrupted Provider snapshot",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(snapshot, original_bytes)

        print(
            "\n=== TEST 4: BROKEN PREVIOUS ID ==="
        )

        raw = mutate_json(
            path2,
            lambda value: value.update({
                "previous_states_id": "b" * 64
            }),
        )

        expect_failure(
            "broken previous_states_id",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(path2, raw)

        print(
            "\n=== TEST 5: FALSE EVENT LIST ==="
        )

        raw = mutate_json(
            path2,
            lambda value: value.update({
                "state_events": []
            }),
        )

        expect_failure(
            "incorrect event list",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(path2, raw)

        print(
            "\n=== TEST 6: FALSE CHANGE FLAG ==="
        )

        raw = mutate_json(
            path3,
            lambda value: value.update({
                "provider_changed": False
            }),
        )

        expect_failure(
            "incorrect provider_changed",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(path3, raw)

        print(
            "\n=== TEST 7: MISSING MIDDLE LINK ==="
        )

        missing_path = (
            path2.with_suffix(".removed")
        )

        path2.rename(missing_path)

        try:

            expect_failure(
                "missing intermediate observation",
                lambda: run_audit(
                    observations_dir,
                    states_dir,
                    provider_dir,
                ),
            )

        finally:

            missing_path.rename(path2)

        print(
            "\n=== TEST 8: INVALID FILENAME ==="
        )

        bad_path = (
            path3.with_name(
                "20261122T190000000000Z-"
                + previous.dataset_id(third[0])[:12]
                + ".json"
            )
        )

        path3.rename(bad_path)

        try:

            expect_failure(
                "filename/time mismatch",
                lambda: run_audit(
                    observations_dir,
                    states_dir,
                    provider_dir,
                ),
            )

        finally:

            bad_path.rename(path3)

        print(
            "\n=== TEST 9: UNVERIFIED CALENDAR ==="
        )

        raw = mutate_json(
            path3,
            lambda value: value.update({
                "calendar_id": "c" * 64
            }),
        )

        expect_failure(
            "unverified calendar",
            lambda: run_audit(
                observations_dir,
                states_dir,
                provider_dir,
            ),
        )

        restore(path3, raw)

        print(
            "\n=== FINAL INTEGRITY CHECK ==="
        )

        result = run_audit(
            observations_dir,
            states_dir,
            provider_dir,
        )

        require(
            result["observations"] == 3,
            "Final chain count mismatch",
        )

        require(
            result["events"] == (0, 1, 1),
            "Final events mismatch",
        )

        print(
            "PASS: original test chain restored"
        )

    print("\n=== FINAL STATUS ===")

    print("ALL 9 TESTS PASSED")
    print("FINAL INTEGRITY CHECK: PASSED")
    print("TEMPORARY TEST FILES: REMOVED")
    print("PRODUCTION STATE: UNCHANGED")
    print("NO NETWORK REQUESTS")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
