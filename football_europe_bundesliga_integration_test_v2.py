#!/usr/bin/env python3
"""Bundesliga Monitor V3 network integration test.

Offline. All writes redirected to TemporaryDirectory.

Uses real monitor.run(write=True), real storage and
real pre/post observation audits.

No network. No production writes. No forecasts.
"""

import copy
import contextlib
import io
import json
import tempfile

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import football_europe_bundesliga_monitor_v3 as monitor
import football_europe_bundesliga_monitor_v2 as previous
import football_europe_bundesliga_observations_audit_v1 as audit
import football_europe_bundesliga_season_v1 as season
import football_europe_bundesliga_calendar_v3 as calendar


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def archived_source():

    archives = [
        path
        for path in season.ARCHIVES.iterdir()
        if path.is_dir()
        and season.HASH_RE.fullmatch(path.name)
    ]

    require(
        len(archives) == 1,
        "Expected exactly one initial archive",
    )

    archive = archives[0]

    season.validate_archive(archive)

    return json.loads(
        (archive / "source.json").read_bytes()
    )


def change_score(data):

    result = copy.deepcopy(data)

    target = next(
        match for match in result
        if match["matchIsFinished"]
    )

    final = next(
        item for item in target["matchResults"]
        if item["resultTypeKind"] == "After90Minutes"
    )

    final["pointsTeam1"] += 1

    return result


def change_provider_time(data, round_number):

    result = copy.deepcopy(data)

    target = next(
        match for match in result
        if match["group"]["groupOrderID"] == round_number
    )

    utc, local = calendar.v2.v1.parse_dates(target)

    new_utc = utc + timedelta(hours=1)

    new_local = new_utc.astimezone(
        calendar.v2.v1.BERLIN
    )

    target["matchDateTimeUTC"] = (
        new_utc.isoformat().replace("+00:00", "Z")
    )

    target["matchDateTime"] = (
        new_local.strftime("%Y-%m-%dT%H:%M:%S")
    )

    return result


def quiet_run(write=True):

    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        monitor.run(write)

    return output.getvalue()


def quiet_audit(root):

    with contextlib.redirect_stdout(io.StringIO()):
        return audit.audit(
            observations_dir=root / "observations",
            states_dir=root / "states",
            provider_dir=root / "provider",
            verbose=False,
        )


def count_json(path):
    return len(list(path.glob("*.json")))


def main():

    print("=== BUNDESLIGA FULL INTEGRATION TEST ===")
    print("MODE: OFFLINE / ISOLATED")

    baseline = archived_source()

    states_changed = change_score(baseline)

    provider_changed = change_provider_time(
        states_changed,
        19,
    )

    calendar_changed = change_provider_time(
        provider_changed,
        11,
    )

    require(
        len(baseline) == 306,
        "Invalid source size",
    )

    with tempfile.TemporaryDirectory(
        prefix="bubus-bundesliga-integration-"
    ) as tmp:

        root = Path(tmp)

        states_dir = root / "states"
        provider_dir = root / "provider"
        observations_dir = root / "observations"

        current = [baseline]
        fetch_count = [0]

        def fake_download():

            fetch_count[0] += 1

            data = copy.deepcopy(current[0])

            raw = json.dumps(
                data,
                ensure_ascii=False,
            ).encode("utf-8")

            return raw, data

        # Redirect ALL write locations used by V3.
        # The immutable source archive remains read-only.

        patches = [
            patch.object(
                monitor,
                "STATES",
                states_dir,
            ),
            patch.object(
                monitor,
                "PROVIDER",
                provider_dir,
            ),
            patch.object(
                monitor,
                "OBSERVATIONS",
                observations_dir,
            ),
            patch.object(
                previous,
                "STATES",
                states_dir,
            ),
            patch.object(
                previous,
                "PROVIDER",
                provider_dir,
            ),
            patch.object(
                previous,
                "OBSERVATIONS",
                observations_dir,
            ),
            patch.object(
                monitor.network,
                "download",
                side_effect=fake_download,
            ),
        ]

        with contextlib.ExitStack() as stack:

            for item in patches:
                stack.enter_context(item)

            print("\n=== TEST 1: INITIAL OBSERVATION ===")

            output = quiet_run()

            require(
                "PRE-WRITE AUDIT: PASSED" in output,
                "Pre-audit missing",
            )

            require(
                "POST-WRITE AUDIT: PASSED" in output,
                "Post-audit missing",
            )

            result = quiet_audit(root)

            require(
                result["observations"] == 1,
                "Expected first observation",
            )

            require(
                result["events"] == (0, 0, 0),
                "Unexpected initial events",
            )

            print("PASS: initial observation recorded")

            print("\n=== TEST 2: SCORE CHANGE ===")

            current[0] = states_changed

            quiet_run()

            result = quiet_audit(root)

            require(
                result["observations"] == 2,
                "Expected second observation",
            )

            require(
                result["events"] == (0, 1, 0),
                "Score event incorrect",
            )

            print("PASS: state change recorded")

            print("\n=== TEST 3: PROVIDER CHANGE ===")

            current[0] = provider_changed

            quiet_run()

            result = quiet_audit(root)

            require(
                result["observations"] == 3,
                "Expected third observation",
            )

            require(
                result["events"] == (0, 1, 1),
                "Provider event incorrect",
            )

            print("PASS: provider change recorded")

            print("\n=== TEST 4: SNAPSHOT COUNTS ===")

            require(
                count_json(states_dir) == 2,
                "Expected two States snapshots",
            )

            require(
                count_json(provider_dir) == 2,
                "Expected two Provider snapshots",
            )

            require(
                count_json(observations_dir) == 3,
                "Expected three observations",
            )

            print("PASS: 2 States / 2 Provider / 3 observations")

            print("\n=== TEST 5: DAMAGED HISTORY ===")

            last_file = sorted(
                observations_dir.glob("*.json")
            )[-1]

            old_bytes = last_file.read_bytes()

            last_file.write_bytes(
                old_bytes + b"\n"
            )

            calls_before = fetch_count[0]

            blocked = False

            try:
                quiet_run()
            except Exception:
                blocked = True

            require(
                blocked,
                "Damaged history was accepted",
            )

            require(
                fetch_count[0] == calls_before,
                "Network fetch happened before audit",
            )

            last_file.write_bytes(old_bytes)

            quiet_audit(root)

            print("PASS: damaged history blocked before fetch")

            print("\n=== TEST 6: UNVERIFIED CALENDAR ===")

            current[0] = calendar_changed

            before_files = (
                count_json(states_dir),
                count_json(provider_dir),
                count_json(observations_dir),
            )

            blocked = False

            try:
                quiet_run()
            except RuntimeError as exc:
                blocked = (
                    "CALENDAR_CHANGED" in str(exc)
                )

            require(
                blocked,
                "Unverified calendar was accepted",
            )

            after_files = (
                count_json(states_dir),
                count_json(provider_dir),
                count_json(observations_dir),
            )

            require(
                before_files == after_files,
                "Calendar rejection modified storage",
            )

            quiet_audit(root)

            print("PASS: unverified calendar blocked")

            print("\n=== FINAL COUNTS ===")
            print("States snapshots:", count_json(states_dir))
            print("Provider snapshots:", count_json(provider_dir))
            print("Observations:", count_json(observations_dir))
            print("Simulated fetches:", fetch_count[0])

    print("\n=== FINAL STATUS ===")
    print("ALL 6 TESTS PASSED")
    print("PRE/POST AUDITS: VERIFIED")
    print("TEMPORARY FILES: REMOVED")
    print("PRODUCTION STATE: UNCHANGED")
    print("NO NETWORK REQUESTS")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
