#!/usr/bin/env python3
"""Bubus Ligue 1 Monitor V3 Integration Test V1.

Tests real recording and observation-chain validation.

OFFLINE / ISOLATED.
No network requests.
No production state writes.
No forecasts.
"""

import contextlib
import copy
import csv
import io
import json
import tempfile

from pathlib import Path
from unittest.mock import patch

import football_europe_ligue1_monitor_v1 as monitor1
import football_europe_ligue1_monitor_v2 as monitor2
import football_europe_ligue1_monitor_v3 as monitor3
import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_observations_audit_v1 as auditor


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def decode_csv(raw):
    return list(
        csv.DictReader(
            io.StringIO(
                raw.decode("utf-8-sig")
            )
        )
    )


def encode_csv(rows):
    output = io.StringIO()

    writer = csv.DictWriter(
        output,
        fieldnames=list(rows[0]),
        lineterminator="\n",
    )

    writer.writeheader()
    writer.writerows(rows)

    return output.getvalue().encode("utf-8")


def archived_sources():

    archive, manifest, _, _, _ = (
        monitor1.load_archive()
    )

    raw = {
        name: (archive / name).read_bytes()
        for name in (
            "fixtures_2627.csv",
            "results_2627.csv",
        )
    }

    return raw


def completed_match(raw):

    result = copy.deepcopy(raw)

    fixtures = decode_csv(
        result["fixtures_2627.csv"]
    )

    results = decode_csv(
        result["results_2627.csv"]
    )

    match = fixtures.pop(0)

    completed = dict.fromkeys(
        results[0].keys(),
        "",
    )

    completed.update(match)

    completed["ft_home"] = "2"
    completed["ft_away"] = "1"
    completed["ft_result"] = "H"

    results.append(completed)

    result["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    result["results_2627.csv"] = (
        encode_csv(results)
    )

    return result


def changed_time(raw):

    result = copy.deepcopy(raw)

    fixtures = decode_csv(
        result["fixtures_2627.csv"]
    )

    old_time = fixtures[0]["time"]

    fixtures[0]["time"] = (
        "22:45"
        if old_time != "22:45"
        else "21:45"
    )

    result["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    return result


def changed_calendar(raw):

    result = copy.deepcopy(raw)

    fixtures = decode_csv(
        result["fixtures_2627.csv"]
    )

    match = fixtures[0]

    match["home"], match["away"] = (
        match["away"],
        match["home"],
    )

    result["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    return result


def count_files(directory):

    if not directory.exists():
        return 0

    return len(
        list(directory.glob("*.json"))
    )


def run_quiet():

    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        monitor3.run(True)

    return output.getvalue()


def baseline_ids():

    content, states, provider, observation = (
        monitor2.load_previous_observation()
    )

    return (
        calendar.dataset_id(content),
        calendar.dataset_id(states),
        calendar.dataset_id(provider),
    ), observation


def main():

    print(
        "=== LIGUE 1 MONITOR V3 INTEGRATION ==="
    )

    print("MODE: OFFLINE / ISOLATED")

    initial = archived_sources()

    current = [initial]

    fetch_count = [0]

    def fake_fetch():

        fetch_count[0] += 1

        return copy.deepcopy(
            current[0]
        )

    with tempfile.TemporaryDirectory(
        prefix="bubus-ligue1-integration-"
    ) as tmp:

        root = Path(tmp)

        states_dir = root / "states"
        provider_dir = root / "provider"
        observations_dir = root / "observations"

        with contextlib.ExitStack() as stack:

            # Redirect all observation/snapshot paths.
            # The original immutable archive is read-only.

            for module in (monitor2, monitor3):

                stack.enter_context(
                    patch.object(
                        module,
                        "STATES",
                        states_dir,
                    )
                )

                stack.enter_context(
                    patch.object(
                        module,
                        "PROVIDER",
                        provider_dir,
                    )
                )

                stack.enter_context(
                    patch.object(
                        module,
                        "OBSERVATIONS",
                        observations_dir,
                    )
                )

            fetch_mock = stack.enter_context(
                patch.object(
                    monitor1,
                    "fetch_current_sources",
                    side_effect=fake_fetch,
                )
            )

            print(
                "\n=== TEST 1: FIRST OBSERVATION ==="
            )

            output = run_quiet()

            require(
                "POST-WRITE CHECK: PASSED"
                in output,
                "First post-write check failed",
            )

            require(
                count_files(observations_dir) == 1,
                "First observation missing",
            )

            require(
                count_files(states_dir) == 0,
                "Unexpected initial States snapshot",
            )

            require(
                count_files(provider_dir) == 0,
                "Unexpected initial Provider snapshot",
            )

            ids1, observation1 = baseline_ids()

            require(
                observation1 is not None,
                "Initial observation not loaded",
            )

            print(
                "PASS: first observation recorded"
            )

            print(
                "\n=== TEST 2: NEW RESULT ==="
            )

            current[0] = completed_match(initial)

            run_quiet()

            ids2, observation2 = baseline_ids()

            require(
                ids1[0] == ids2[0],
                "Calendar changed",
            )

            require(
                ids1[1] != ids2[1],
                "States unchanged",
            )

            require(
                ids1[2] == ids2[2],
                "Provider unexpectedly changed",
            )

            require(
                count_files(states_dir) == 1,
                "States snapshot missing",
            )

            require(
                len(observation2["state_events"]) == 1,
                "Expected one State event",
            )

            print(
                "PASS: new result recorded"
            )

            print(
                "\n=== TEST 3: PROVIDER TIME ==="
            )

            current[0] = changed_time(
                current[0]
            )

            run_quiet()

            ids3, observation3 = baseline_ids()

            require(
                ids2[0] == ids3[0],
                "Calendar changed",
            )

            require(
                ids2[1] == ids3[1],
                "States unexpectedly changed",
            )

            require(
                ids2[2] != ids3[2],
                "Provider unchanged",
            )

            require(
                count_files(provider_dir) == 1,
                "Provider snapshot missing",
            )

            require(
                len(observation3["provider_events"]) == 1,
                "Expected one Provider event",
            )

            print(
                "PASS: provider change recorded"
            )

            print(
                "\n=== TEST 4: REPEAT CHECK ==="
            )

            previous_ids = ids3

            run_quiet()

            ids4, observation4 = baseline_ids()

            require(
                ids4 == previous_ids,
                "Repeated check changed datasets",
            )

            require(
                observation4["state_events"] == [],
                "Unexpected repeated State events",
            )

            require(
                observation4["provider_events"] == [],
                "Unexpected repeated Provider events",
            )

            require(
                count_files(observations_dir) == 4,
                "Expected four observations",
            )

            require(
                count_files(states_dir) == 1,
                "Duplicate States snapshot",
            )

            require(
                count_files(provider_dir) == 1,
                "Duplicate Provider snapshot",
            )

            print(
                "PASS: repeated check is consistent"
            )

            print(
                "\n=== INDEPENDENT AUDITOR TESTS ==="
            )

            def audit_history():
                return auditor.audit(
                    observations_dir=observations_dir,
                    states_dir=states_dir,
                    provider_dir=provider_dir,
                    verbose=False,
                )

            def expect_rejected(label, path, altered):

                original = path.read_bytes()

                require(
                    altered != original,
                    "Mutation did not modify file",
                )

                try:
                    path.write_bytes(altered)

                    rejected = False

                    try:
                        audit_history()
                    except (
                        RuntimeError,
                        ValueError,
                        KeyError,
                        TypeError,
                        AssertionError,
                    ):
                        rejected = True

                    require(
                        rejected,
                        "Auditor accepted: " + label,
                    )

                finally:
                    path.write_bytes(original)

                # Confirm chain recovers after restoration.
                audit_history()

                print("PASS:", label)

            def mutate_observation(label, change):

                latest = sorted(
                    observations_dir.glob("*.json")
                )[-1]

                data = json.loads(
                    latest.read_bytes()
                )

                change(data)

                expect_rejected(
                    label,
                    latest,
                    calendar.canonical(data),
                )

            print(
                "\n=== AUDIT TEST 1: VALID CHAIN ==="
            )

            summary = audit_history()

            require(
                summary["observations"] == 4,
                "Expected four observations",
            )

            require(
                summary["events"] == (0, 1, 1),
                "Unexpected audited event counts",
            )

            print("PASS: four-observation chain")

            print(
                "\n=== AUDIT TEST 2: STATES CORRUPTION ==="
            )

            state_file = next(
                states_dir.glob("*.json")
            )

            expect_rejected(
                "corrupted States snapshot",
                state_file,
                state_file.read_bytes() + b"\\n",
            )

            print(
                "\n=== AUDIT TEST 3: PROVIDER CORRUPTION ==="
            )

            provider_file = next(
                provider_dir.glob("*.json")
            )

            expect_rejected(
                "corrupted Provider snapshot",
                provider_file,
                provider_file.read_bytes() + b"\\n",
            )

            print(
                "\n=== AUDIT TEST 4: BROKEN CHAIN ==="
            )

            mutate_observation(
                "broken previous States ID",
                lambda d: d.__setitem__(
                    "previous_states_id",
                    "0" * 64,
                ),
            )

            print(
                "\n=== AUDIT TEST 5: FAKE EVENTS ==="
            )

            mutate_observation(
                "fake State events",
                lambda d: d.__setitem__(
                    "state_events",
                    [{"fake": True}],
                ),
            )

            print(
                "\n=== AUDIT TEST 6: FAKE FLAGS ==="
            )

            mutate_observation(
                "fake Provider changed flag",
                lambda d: d.__setitem__(
                    "provider_changed",
                    True,
                ),
            )

            print(
                "\n=== AUDIT TEST 7: TIMESTAMP ==="
            )

            mutate_observation(
                "filename/fetched time mismatch",
                lambda d: d.__setitem__(
                    "fetched_utc",
                    "2020-01-01T00:00:00+00:00",
                ),
            )

            print(
                "\n=== AUDIT TEST 8: CALENDAR ID ==="
            )

            mutate_observation(
                "fake Calendar ID",
                lambda d: d.__setitem__(
                    "calendar_id",
                    "f" * 64,
                ),
            )

            summary = audit_history()

            require(
                summary["observations"] == 4,
                "Final audit failed",
            )

            require(
                summary["events"] == (0, 1, 1),
                "Final event counts changed",
            )

            print(
                "\nALL 8 AUDITOR TESTS PASSED"
            )


            print(
                "\n=== TEST 5: DAMAGED HISTORY ==="
            )

            latest = sorted(
                observations_dir.glob("*.json")
            )[-1]

            original_bytes = latest.read_bytes()

            latest.write_bytes(
                original_bytes + b"\n"
            )

            before_fetch = fetch_count[0]

            blocked = False

            try:
                run_quiet()
            except Exception:
                blocked = True

            require(
                blocked,
                "Damaged observation accepted",
            )

            require(
                fetch_count[0] == before_fetch,
                "Fetch happened before history check",
            )

            latest.write_bytes(
                original_bytes
            )

            baseline_ids()

            print(
                "PASS: corrupted history blocked before fetch"
            )

            print(
                "\n=== TEST 6: CALENDAR MUTATION ==="
            )

            current[0] = changed_calendar(
                current[0]
            )

            before_counts = (
                count_files(states_dir),
                count_files(provider_dir),
                count_files(observations_dir),
            )

            blocked = False

            try:
                run_quiet()
            except (
                RuntimeError,
                ValueError,
                KeyError,
            ):
                blocked = True

            require(
                blocked,
                "Calendar mutation accepted",
            )

            after_counts = (
                count_files(states_dir),
                count_files(provider_dir),
                count_files(observations_dir),
            )

            require(
                before_counts == after_counts,
                "Rejected calendar modified storage",
            )

            baseline_ids()

            print(
                "PASS: unverified calendar blocked"
            )

            print("\n=== FINAL COUNTS ===")

            print(
                "States snapshots:",
                count_files(states_dir),
            )

            print(
                "Provider snapshots:",
                count_files(provider_dir),
            )

            print(
                "Observations:",
                count_files(observations_dir),
            )

            print(
                "Simulated fetches:",
                fetch_mock.call_count,
            )

    print("\n=== FINAL STATUS ===")

    print("ALL 6 TESTS PASSED")
    print("OBSERVATION CHAIN: VALID")
    print("SNAPSHOT CHECKSUMS: VERIFIED")
    print("TEMPORARY FILES: REMOVED")
    print("PRODUCTION STATE: UNCHANGED")
    print("NO NETWORK REQUESTS")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
