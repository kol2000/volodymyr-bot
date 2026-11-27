#!/usr/bin/env python3
"""Bubus EPL calendar diff V2.

Compare overlapping observation windows.
READ ONLY. No database or archive writes.
"""

import argparse
import sys
from datetime import datetime, timezone

from football_europe_epl_diff_v1 import (
    BASE,
    load_snapshot,
)


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def match_day(item):
    return datetime.fromisoformat(
        item["date"]
    ).date()


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--old")
    ap.add_argument("--new")

    args = ap.parse_args()

    directories = sorted(
        [
            p for p in BASE.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
        ],
        key=lambda p: p.stat().st_mtime,
    )

    print("=== BUBUS EPL CALENDAR DIFF V2 ===")
    print("MODE: READ ONLY")

    if args.old and args.new:
        old_path = BASE / args.old
        new_path = BASE / args.new

    elif args.old or args.new:
        raise ValueError("Specify both --old and --new")

    elif len(directories) >= 2:
        old_path, new_path = directories[-2:]

    elif len(directories) == 1:
        old_path = new_path = directories[0]

    else:
        raise RuntimeError("No calendar snapshots")

    _, old_calendar, old_matches, old_id = load_snapshot(
        old_path
    )

    _, new_calendar, new_matches, new_id = load_snapshot(
        new_path
    )

    old_start = timestamp(
        old_calendar["window_start_utc"]
    )

    old_end = timestamp(
        old_calendar["window_end_utc"]
    )

    new_start = timestamp(
        new_calendar["window_start_utc"]
    )

    new_end = timestamp(
        new_calendar["window_end_utc"]
    )

    if not old_start < old_end:
        raise RuntimeError("Invalid old calendar window")

    if not new_start < new_end:
        raise RuntimeError("Invalid new calendar window")

    if new_start < old_start:
        raise RuntimeError("Snapshots are in reverse order")

    overlap_start = max(old_start, new_start)
    overlap_end = min(old_end, new_end)

    print("OLD:", old_path.name)
    print("NEW:", new_path.name)

    print("Old content:", old_id)
    print("New content:", new_id)

    if overlap_start >= overlap_end:
        print("STATUS: NO OVERLAPPING WINDOWS")
        print("FORECAST RECORDING: BLOCKED")
        return 2

    print(
        "Overlap:",
        overlap_start.isoformat(),
        "->",
        overlap_end.isoformat(),
    )

    # Use calendar dates for inclusion because DISCOVERED
    # fixtures may not have an exact kickoff time.
    #
    # Exclude the first and last partial calendar days.
    from datetime import timedelta

    start_date = overlap_start.date() + timedelta(days=1)
    end_date = overlap_end.date() - timedelta(days=1)

    if start_date > end_date:
        print("STATUS: NO COMPLETE OVERLAPPING DAYS")
        return 2

    print("Complete dates:", start_date, "->", end_date)

    def inside(item):
        return start_date <= match_day(item) <= end_date

    old_common = {
        key: value
        for key, value in old_matches.items()
        if inside(value)
    }

    new_common = {
        key: value
        for key, value in new_matches.items()
        if inside(value)
    }

    keys = sorted(set(old_common) | set(new_common))

    added = 0
    removed = 0
    changed = 0
    unchanged = 0

    for key in keys:
        before = old_common.get(key)
        after = new_common.get(key)

        home, away = key

        if before is None:
            added += 1
            print("APPEARED:", home, "—", away)
            continue

        if after is None:
            removed += 1
            print(
                "DISAPPEARED FROM OVERLAP:",
                home, "—", away
            )
            continue

        fields = (
            "date",
            "kickoff_utc",
            "status",
            "reason",
        )

        differences = [
            field
            for field in fields
            if before.get(field) != after.get(field)
        ]

        if not differences:
            unchanged += 1
            continue

        changed += 1

        print("\nCHANGED:", home, "—", away)

        for field in differences:
            print(
                " ",
                field,
                repr(before.get(field)),
                "->",
                repr(after.get(field)),
            )

    print("\n=== SUMMARY ===")

    print("Unchanged:", unchanged)
    print("Changed:", changed)
    print("Appeared:", added)
    print("Disappeared:", removed)

    if old_id == new_id:
        print("CONTENT: IDENTICAL")
    else:
        print("CONTENT: DIFFERENT")

    if not (changed or added or removed):
        print("STATUS: NO CHANGES IN OVERLAP")
    else:
        print("STATUS: CHANGES REQUIRE REVIEW")

    print(
        "NOTE: Appearance or disappearance is not "
        "proof of a fixture transfer or cancellation."
    )

    print("FORECAST RECORDING: BLOCKED")
    print("READ ONLY")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "ERROR:",
            type(exc).__name__,
            str(exc),
        )
        sys.exit(2)
