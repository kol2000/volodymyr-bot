#!/usr/bin/env python3
"""Bubus EPL Calendar Monitor V1.

Compare current official calendar with archived season.

READ ONLY.
Includes an in-memory test of schedule-change detection.
"""

import copy
import hashlib
import json
from pathlib import Path

import football_europe_epl_season_v1 as season
from football_europe_sources_v3 import SOURCES, fetch


BASE = Path(
    "state/football_live/calendars/epl_season"
)


def identity(item):
    return (
        item["home"],
        item["away"],
    )


def index(matches):
    result = {}

    for item in matches:
        key = identity(item)

        if key in result:
            raise RuntimeError(
                f"Duplicate fixture identity: {key}"
            )

        result[key] = item

    return result


def compare(old_matches, new_matches):
    old = index(old_matches)
    new = index(new_matches)

    events = []

    for key in sorted(set(old) | set(new)):
        before = old.get(key)
        after = new.get(key)

        if before is None:
            events.append({
                "type": "ADDED",
                "match": key,
                "new": after,
            })
            continue

        if after is None:
            events.append({
                "type": "REMOVED",
                "match": key,
                "old": before,
            })
            continue

        changes = {}

        for field in (
            "date",
            "kickoff_utc",
            "status",
            "reason",
            "requires_reverification",
        ):
            a = before.get(field)
            b = after.get(field)

            if a != b:
                changes[field] = {
                    "old": a,
                    "new": b,
                }

        if changes:
            events.append({
                "type": "CHANGED",
                "match": key,
                "changes": changes,
            })

    return events


def load_latest():
    archives = []

    for directory in BASE.iterdir():
        if not directory.is_dir():
            continue

        if directory.name.startswith("."):
            continue

        manifest = season.validate_archive(directory)

        archives.append((
            manifest["fetched_utc"],
            directory,
        ))

    if not archives:
        raise RuntimeError("No valid season archive")

    archives.sort(key=lambda x: x[0])

    directory = archives[-1][1]

    content = json.loads(
        (directory / "calendar.json").read_text(
            encoding="utf-8"
        )
    )

    return directory, content


def main():
    print("=== BUBUS EPL CALENDAR MONITOR V1 ===")
    print("MODE: READ ONLY")

    directory, old = load_latest()

    print("Archived version:", directory.name)

    status, content_type, raw = fetch(SOURCES["EPL"])

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official source unavailable")

    current, original_count = season.build(raw)

    current_id = hashlib.sha256(
        season.encode(current)
    ).hexdigest()

    print("Current content ID:", current_id)
    print("Original source records:", original_count)

    events = compare(
        old["matches"],
        current["matches"],
    )

    print("\n=== ACTUAL SOURCE CHANGES ===")

    if not events:
        print("NO CALENDAR CHANGES")

    for event in events:
        print(json.dumps(
            event,
            ensure_ascii=False,
            indent=2,
        ))

    # Conflicts are calendar metadata and may change
    # without affecting the normalized match list.
    # Compare canonical JSON representations.
    # This normalizes tuples to JSON arrays.
    metadata_changed = (
        season.encode({
            "conflicts": old.get("conflicts"),
            "schema": old.get("schema"),
            "source_url": old.get("source_url"),
        })
        !=
        season.encode({
            "conflicts": current.get("conflicts"),
            "schema": current.get("schema"),
            "source_url": current.get("source_url"),
        })
    )

    print("Metadata changed:", metadata_changed)

    if current_id != directory.name:
        print("STATUS: NEW CALENDAR VERSION DETECTED")
    else:
        print("STATUS: CALENDAR CONTENT UNCHANGED")

    print("\n=== IN-MEMORY TRANSFER TEST ===")

    test_old = copy.deepcopy(old["matches"])
    test_new = copy.deepcopy(test_old)

    # Simulate a one-hour kickoff change.
    target = next(
        item for item in test_new
        if item["home"] == "Arsenal"
        and item["away"] == "Manchester City"
        and item["date"] == "2026-11-29"
    )

    from datetime import datetime, timedelta

    original = target["kickoff_utc"]

    shifted = (
        datetime.fromisoformat(original)
        + timedelta(hours=1)
    ).isoformat()

    target["kickoff_utc"] = shifted

    simulated = compare(test_old, test_new)

    if len(simulated) != 1:
        raise RuntimeError(
            "Transfer test did not produce exactly one change"
        )

    event = simulated[0]

    if (
        event["type"] != "CHANGED"
        or set(event["changes"]) != {"kickoff_utc"}
        or event["changes"]["kickoff_utc"]["old"] != original
        or event["changes"]["kickoff_utc"]["new"] != shifted
    ):
        raise RuntimeError(
            "Transfer detection returned unexpected changes"
        )

    print("TEST MATCH: Arsenal — Manchester City")
    print("Original kickoff:", original)
    print("Simulated kickoff:", shifted)
    print("Detected events:", len(simulated))
    print("EVENT:", event["type"])
    print("TEST STATUS: PASSED")

    print("\n=== FINAL STATUS ===")
    print("Actual changes:", len(events))
    print("Actual metadata changed:", metadata_changed)
    print("Simulation: PASSED")
    print("Existing archives: UNCHANGED")
    print("Forecast recording: BLOCKED")
    print("READ ONLY")


if __name__ == "__main__":
    main()
