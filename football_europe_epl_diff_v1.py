#!/usr/bin/env python3
"""Bubus EPL calendar snapshot comparison V1.

Read-only.

Compare archived calendars by match identity and schedule content.
Ignore observation timestamps when detecting calendar changes.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from football_europe_epl_snapshot_v1 import (
    validate_existing,
)

ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars/epl"


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def match_key(item):
    return (
        item["home"],
        item["away"],
    )


def load_snapshot(path):
    validate_existing(path, path.name)

    manifest = json.loads(
        (path / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    calendar = json.loads(
        (path / "calendar.json").read_text(
            encoding="utf-8"
        )
    )

    if calendar.get("schema") != "bubus-epl-calendar-v1":
        raise RuntimeError("Invalid calendar schema")

    if calendar.get("competition") != "E0":
        raise RuntimeError("Wrong competition")

    if calendar.get("source_url") != manifest.get("source_url"):
        raise RuntimeError("Source URL mismatch")

    if manifest.get("matches") != len(calendar["matches"]):
        raise RuntimeError("Manifest match count mismatch")

    matches = calendar["matches"]

    result = {}

    for item in matches:
        key = match_key(item)

        if key in result:
            raise RuntimeError(
                "Duplicate match identity: " + str(key)
            )

        result[key] = item

    content = [
        {
            "home": item["home"],
            "away": item["away"],
            "date": item["date"],
            "kickoff_utc": item["kickoff_utc"],
            "status": item["status"],
            "reason": item.get("reason"),
        }
        for item in matches
    ]

    content.sort(
        key=lambda r: (
            r["home"],
            r["away"],
            r["date"],
        )
    )

    content_id = sha(encode(content))

    return manifest, calendar, result, content_id


def compare(old, new):

    old_keys = set(old)
    new_keys = set(new)

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    common = sorted(old_keys & new_keys)

    changed = []
    unchanged = 0

    fields = (
        "date",
        "kickoff_utc",
        "status",
        "reason",
    )

    for key in common:
        before = old[key]
        after = new[key]

        differences = []

        for field in fields:
            a = before.get(field)
            b = after.get(field)

            if a != b:
                differences.append((field, a, b))

        if differences:
            changed.append((key, differences))
        else:
            unchanged += 1

    return added, removed, changed, unchanged


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--old",
        help="Old snapshot directory name",
    )

    parser.add_argument(
        "--new",
        help="New snapshot directory name",
    )

    args = parser.parse_args()

    print("=== BUBUS EPL CALENDAR DIFF V1 ===")
    print("MODE: READ ONLY")

    directories = sorted(
        (
            p for p in BASE.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
        ),
        key=lambda p: p.stat().st_mtime,
    )

    if args.old and args.new:
        old_path = BASE / args.old
        new_path = BASE / args.new

    elif len(directories) >= 2:
        old_path, new_path = directories[-2:]

    elif len(directories) == 1:
        current = directories[0]

        manifest, calendar, records, content_id = (
            load_snapshot(current)
        )

        print("\nOnly one snapshot available.")
        print("Snapshot:", current.name)
        print("Calendar Content ID:", content_id)
        print("Matches:", len(records))
        print("Integrity: VERIFIED")
        print("Comparison: NOT AVAILABLE")
        print("READ ONLY")
        return 0

    else:
        raise RuntimeError("No EPL snapshots found")

    for path in (old_path, new_path):
        if not path.is_dir():
            raise RuntimeError(
                "Snapshot not found: " + str(path)
            )

    old_manifest, old_calendar, old, old_id = (
        load_snapshot(old_path)
    )

    new_manifest, new_calendar, new, new_id = (
        load_snapshot(new_path)
    )

    print("\nOLD SNAPSHOT:", old_path.name)
    print("NEW SNAPSHOT:", new_path.name)

    print("\nOLD CONTENT ID:", old_id)
    print("NEW CONTENT ID:", new_id)

    added, removed, changed, unchanged = compare(
        old, new
    )

    print("\n=== CHANGES ===")

    for home, away in added:
        item = new[(home, away)]

        print(
            "ADDED:",
            home,
            "—",
            away,
            item["date"],
            item["kickoff_utc"],
        )

    for home, away in removed:
        item = old[(home, away)]

        print(
            "REMOVED FROM SNAPSHOT:",
            home,
            "—",
            away,
            item["date"],
        )

    for (home, away), differences in changed:

        print("\nCHANGED:", home, "—", away)

        for field, before, after in differences:
            print(
                f"  {field}:",
                repr(before),
                "->",
                repr(after),
            )

    print("\n=== SUMMARY ===")
    print("Added:", len(added))
    print("Removed from snapshot:", len(removed))
    print("Changed:", len(changed))
    print("Unchanged:", unchanged)

    if old_id == new_id:
        print("STATUS: IDENTICAL CALENDAR CONTENT")
    else:
        print("STATUS: CALENDAR CONTENT DIFFERS")

    print("\nNOTE:")
    print(
        "A removed fixture may simply be outside "
        "the newer 14-day window."
    )
    print(
        "A changed fixture requires independent "
        "re-verification."
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
            str(exc)
        )
        sys.exit(2)
