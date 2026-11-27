#!/usr/bin/env python3
"""Read-only acceptance audit for European football snapshots."""

import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import football_europe_update_v1 as update
import football_europe_snapshots_manage_v1 as manager


ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "state/football_live/source"
SNAPSHOTS = SOURCE.parent / "snapshots"

CODES = update.LEAGUES

issues = []


def block(message):
    issues.append(message)
    print("BLOCK:", message)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fixture_index(rows):
    result = {}

    for row in rows:
        code = (row.get("Div") or "").strip()

        if code not in CODES.values():
            continue

        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        day = (row.get("Date") or "").strip()
        clock = (row.get("Time") or "").strip()

        if not all((home, away, day, clock)):
            raise RuntimeError("Incomplete fixture")

        datetime.strptime(day, "%d/%m/%Y")
        datetime.strptime(clock, "%H:%M")

        key = (code, home, away)

        if key in result:
            raise RuntimeError(
                f"Duplicate fixture identity: {key}"
            )

        result[key] = (day, clock)

    return result


def select_snapshot():
    valid = []

    if not SNAPSHOTS.exists():
        raise RuntimeError("Snapshot directory missing")

    for path in SNAPSHOTS.iterdir():
        if not path.is_dir():
            continue

        if path.name.startswith(".staging-"):
            continue

        created, manifest = manager.validate(path)
        valid.append((created, path, manifest))

    if not valid:
        raise RuntimeError("No valid snapshots")

    return max(valid, key=lambda x: x[0])


def compare_results(snapshot):
    print("\n=== RESULTS ===")

    for league, code in CODES.items():
        name = f"{league}_2026-27.csv"

        old = update.result_index(
            read_csv(SOURCE / name), code
        )
        new = update.result_index(
            read_csv(snapshot / name), code
        )

        added = set(new) - set(old)
        removed = set(old) - set(new)
        changed = {
            key for key in set(old) & set(new)
            if old[key] != new[key]
        }

        print(
            f"{league}: old={len(old)} "
            f"new={len(new)} "
            f"added={len(added)} "
            f"removed={len(removed)} "
            f"changed={len(changed)}"
        )

        for key in sorted(added):
            print(" NEW:", key, new[key])

        for key in sorted(changed):
            print(" CHANGED:", key, old[key], "=>", new[key])

        for key in sorted(removed):
            print(" REMOVED:", key)

        if removed or changed:
            block(f"{league}: historical results conflict")


def compare_fixtures(snapshot):
    print("\n=== FIXTURES ===")

    old = fixture_index(
        read_csv(SOURCE / "fixtures.csv")
    )
    new = fixture_index(
        read_csv(snapshot / "fixtures.csv")
    )

    added = set(new) - set(old)
    removed = set(old) - set(new)
    changed = {
        key for key in set(old) & set(new)
        if old[key] != new[key]
    }

    print("Previous fixtures:", len(old))
    print("Snapshot fixtures:", len(new))
    print("Added:", len(added))
    print("Removed:", len(removed))
    print("Changed kickoff:", len(changed))

    for key in sorted(added):
        print(" NEW:", key, new[key])

    for key in sorted(removed):
        print(" REMOVED:", key, old[key])

    for key in sorted(changed):
        print(
            " RESCHEDULED:",
            key,
            old[key],
            "=>",
            new[key]
        )

    if added or removed or changed:
        block(
            "Fixture changes require independent review"
        )


def main():
    print("=== BUBUS EUROPE ACCEPTANCE V1 ===")

    created, snapshot, manifest = select_snapshot()

    print("Snapshot:", snapshot.name)
    print("Created UTC:", created.isoformat())
    print("Integrity: VERIFIED")

    compare_results(snapshot)
    compare_fixtures(snapshot)

    print("\n=== FINAL STATUS ===")

    if issues:
        print("Blocking issues:", len(issues))
        print("STATUS: NOT READY")
        return 2

    print("Historical results: NO CONFLICTS")
    print("Fixture differences: NONE")
    print("STATUS: DATA CONSISTENT")
    print("KICKOFF INDEPENDENT VERIFICATION: PENDING")
    print("FORECAST RECORDING: NOT AUTHORIZED")
    print("READ ONLY")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "STATUS: NOT READY",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )
        sys.exit(2)
