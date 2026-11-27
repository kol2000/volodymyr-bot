#!/usr/bin/env python3
"""Bubus Bundesliga Monitor V1.

Compare archived season with current OpenLigaDB data.

Three independent datasets:
- Calendar
- Match states
- Provider schedule

STRICTLY READ ONLY.
No archive or observation writes.
No forecast recording.
"""

import json
import re
from collections import Counter
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_season_v1 as season


ROOT = Path(__file__).resolve().parent

ARCHIVES = (
    ROOT
    / "state/football_live/calendars/bundesliga_season"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def index_by_id(items):

    result = {}

    for item in items:

        identifier = item["id"]

        require(
            identifier not in result,
            "Duplicate match ID: " + identifier,
        )

        result[identifier] = item

    return result


def compare_datasets(old_items, new_items, event_type):

    old = index_by_id(old_items)
    new = index_by_id(new_items)

    events = []

    for identifier in sorted(set(old) | set(new)):

        before = old.get(identifier)
        after = new.get(identifier)

        if before is None:

            events.append({
                "type": event_type + "_ADDED",
                "id": identifier,
                "new": after,
            })

            continue

        if after is None:

            events.append({
                "type": event_type + "_REMOVED",
                "id": identifier,
                "old": before,
            })

            continue

        changes = {}

        for field in sorted(set(before) | set(after)):

            if field == "id":
                continue

            if before.get(field) != after.get(field):

                changes[field] = {
                    "old": before.get(field),
                    "new": after.get(field),
                }

        if changes:

            events.append({
                "type": event_type + "_CHANGED",
                "id": identifier,
                "changes": changes,
            })

    return events


def load_archive():

    require(
        ARCHIVES.is_dir(),
        "Bundesliga archive directory missing",
    )

    archives = [
        path
        for path in ARCHIVES.iterdir()
        if (
            path.is_dir()
            and HASH_RE.fullmatch(path.name)
        )
    ]

    require(
        len(archives) == 1,
        "Expected exactly one initial archive",
    )

    path = archives[0]

    # Validate all checksums and reconstruct
    # the archive from its source JSON.

    season.validate_archive(path)

    source = json.loads(
        (path / "source.json").read_bytes()
    )

    content, states, provider = (
        calendar.normalize(source)
    )

    return path, content, states, provider


def summarize(items, field):

    return dict(sorted(
        Counter(
            item[field]
            for item in items
        ).items()
    ))


def main():

    print("=== BUBUS BUNDESLIGA MONITOR V1 ===")
    print("MODE: READ ONLY")

    print("\n=== ARCHIVED DATA ===")

    archive, old_calendar, old_states, old_provider = (
        load_archive()
    )

    old_ids = {
        "calendar": calendar.content_id(old_calendar),
        "states": calendar.content_id(old_states),
        "provider": calendar.content_id(old_provider),
    }

    for name, identifier in old_ids.items():
        print(name + ":", identifier)

    print("\n=== CURRENT OPENLIGADB ===")

    source_raw, source = season.download()

    new_calendar, new_states, new_provider = (
        calendar.normalize(source)
    )

    new_ids = {
        "calendar": calendar.content_id(new_calendar),
        "states": calendar.content_id(new_states),
        "provider": calendar.content_id(new_provider),
    }

    print("Matches:", len(new_calendar["matches"]))
    print("States:", len(new_states))
    print("Provider records:", len(new_provider))

    print("Source SHA256:", season.sha(source_raw))

    for name, identifier in new_ids.items():
        print(name + ":", identifier)

    print("\n=== CALENDAR CHANGES ===")

    calendar_events = compare_datasets(
        old_calendar["matches"],
        new_calendar["matches"],
        "CALENDAR",
    )

    print("Events:", len(calendar_events))

    for event in calendar_events[:10]:
        print(json.dumps(
            event,
            ensure_ascii=False,
        ))

    print("\n=== MATCH STATE CHANGES ===")

    state_events = compare_datasets(
        old_states,
        new_states,
        "STATE",
    )

    print("Events:", len(state_events))

    for event in state_events[:10]:
        print(json.dumps(
            event,
            ensure_ascii=False,
        ))

    print("\n=== PROVIDER SCHEDULE CHANGES ===")

    provider_events = compare_datasets(
        old_provider,
        new_provider,
        "PROVIDER",
    )

    print("Events:", len(provider_events))

    for event in provider_events[:10]:
        print(json.dumps(
            event,
            ensure_ascii=False,
        ))

    print("\n=== CONSISTENCY ===")

    datasets = (
        ("calendar", calendar_events),
        ("states", state_events),
        ("provider", provider_events),
    )

    for name, events in datasets:

        changed = old_ids[name] != new_ids[name]

        require(
            changed == bool(events),
            name + ": Content ID/events mismatch",
        )

        print(
            name + ":",
            "CHANGED" if changed else "UNCHANGED",
        )

    print("\n=== CURRENT MATCH STATES ===")

    print(
        summarize(new_states, "status")
    )

    print("\n=== CURRENT CALENDAR QUALITY ===")

    print(
        summarize(
            new_calendar["matches"],
            "schedule_status",
        )
    )

    print("\n=== FINAL STATUS ===")

    print("CALENDAR COMPARISON: VALID")
    print("STATE COMPARISON: VALID")
    print("PROVIDER COMPARISON: VALID")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
