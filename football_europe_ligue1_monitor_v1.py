#!/usr/bin/env python3
"""Bubus Ligue 1 Monitor V1.

Compare fresh Open Matchday data against the
immutable 2026/27 Ligue 1 season archive.

Datasets:
- Calendar
- States
- Provider schedule

STRICTLY READ ONLY.

No snapshots.
No observations.
No archive writes.
No forecast recording.
"""

import json
from collections import Counter
from pathlib import Path

import football_europe_ligue1_season_v1 as season
import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_season_audit_v1 as audit
import football_europe_ligue1_sources_v1 as sources


EXPECTED_CALENDAR_ID = (
    "aeead73e41ae50d4e7e90902c8f8aa787f6da17b57310428017d0ee60518f701"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_archive():

    archives = [
        path
        for path in season.ARCHIVES.iterdir()
        if path.is_dir()
    ]

    require(
        len(archives) == 1,
        "Expected exactly one Ligue 1 archive",
    )

    archive = archives[0]

    manifest = season.validate_archive(archive)

    require(
        manifest["calendar_id"]
        == EXPECTED_CALENDAR_ID,
        "Unexpected initial calendar ID",
    )

    content = json.loads(
        (archive / "calendar.json").read_bytes()
    )

    states = json.loads(
        (archive / "states.json").read_bytes()
    )

    provider = json.loads(
        (archive / "provider_schedule.json").read_bytes()
    )

    return (
        archive,
        manifest,
        content,
        states,
        provider,
    )


def fetch_current_sources():

    raw_sources = {
        "lfp_official.pdf": None,
    }

    print("Fetching fixtures CSV")

    status, content_type, fixtures = (
        sources.download(
            sources.SOURCES[
                "OPEN_MATCHDAY_FIXTURES"
            ]
        )
    )

    require(
        status == 200,
        "Fixtures HTTP error",
    )

    print("Fetching results CSV")

    status, content_type, results = (
        sources.download(
            sources.SOURCES[
                "OPEN_MATCHDAY_RESULTS"
            ]
        )
    )

    require(
        status == 200,
        "Results HTTP error",
    )

    raw_sources["fixtures_2627.csv"] = fixtures
    raw_sources["results_2627.csv"] = results

    return raw_sources


def match_keys(matches):

    return {
        (
            item["gameweek"],
            item["home_key"],
            item["away_key"],
        )
        for item in matches
    }


def parse_current(raw_sources):

    fixtures_rows = season.decode_csv(
        raw_sources["fixtures_2627.csv"]
    )

    results_rows = season.decode_csv(
        raw_sources["results_2627.csv"]
    )

    fixtures = [
        audit.parse_match(row, False)
        for row in fixtures_rows
    ]

    results = [
        audit.parse_match(row, True)
        for row in results_rows
    ]

    matches = fixtures + results

    calendar.validate_structure(matches)

    return matches, len(results), len(fixtures)


def compare_entries(old, new):

    old_map = {
        item["match_id"]: item
        for item in old["matches"]
    }

    new_map = {
        item["match_id"]: item
        for item in new["matches"]
    }

    require(
        old_map.keys() == new_map.keys(),
        "Dataset match IDs changed",
    )

    events = []

    for identifier in sorted(old_map):

        before = old_map[identifier]
        after = new_map[identifier]

        if before == after:
            continue

        changed_fields = {}

        for field in sorted(
            set(before) | set(after)
        ):

            old_value = before.get(field)
            new_value = after.get(field)

            if old_value == new_value:
                continue

            changed_fields[field] = {
                "old": old_value,
                "new": new_value,
            }

        events.append({
            "match_id": identifier,
            "changes": changed_fields,
        })

    return events


def print_events(title, events):

    print()
    print("=== " + title + " ===")

    print("Changes:", len(events))

    for event in events[:20]:

        print()
        print("MATCH:", event["match_id"])

        for field, values in (
            event["changes"].items()
        ):

            print(
                " ",
                field,
                ":",
                repr(values["old"]),
                "->",
                repr(values["new"]),
            )

    if len(events) > 20:

        print(
            "...",
            len(events) - 20,
            "additional events",
        )


def main():

    print(
        "=== BUBUS LIGUE 1 MONITOR V1 ==="
    )

    print("MODE: STRICTLY READ ONLY")

    print("\n=== INITIAL ARCHIVE ===")

    (
        archive,
        manifest,
        old_calendar,
        old_states,
        old_provider,
    ) = load_archive()

    print("Archive:", archive.name)

    print(
        "Calendar ID:",
        manifest["calendar_id"],
    )

    print(
        "States ID:",
        manifest["initial_states_id"],
    )

    print(
        "Provider ID:",
        manifest["initial_provider_id"],
    )

    print("\n=== CURRENT SOURCES ===")

    raw_sources = fetch_current_sources()

    matches, finished, upcoming = (
        parse_current(raw_sources)
    )

    print("Total matches:", len(matches))
    print("Finished:", finished)
    print("Upcoming:", upcoming)

    print("\n=== CALENDAR SAFETY CHECK ===")

    old_keys = {
        (
            item["round"],
            item["home"],
            item["away"],
        )
        for item in old_calendar["matches"]
    }

    current_keys = match_keys(matches)

    missing = old_keys - current_keys
    unexpected = current_keys - old_keys

    print(
        "Matching fixtures:",
        len(old_keys & current_keys),
    )

    print(
        "Missing fixtures:",
        len(missing),
    )

    print(
        "Unexpected fixtures:",
        len(unexpected),
    )

    for item in sorted(missing)[:10]:
        print("MISSING:", item)

    for item in sorted(unexpected)[:10]:
        print("UNEXPECTED:", item)

    require(
        not missing and not unexpected,
        "CALENDAR_CHANGED: manual verification required",
    )

    print("Calendar structure: UNCHANGED")

    print("\n=== NORMALIZATION ===")

    current_sources = dict(raw_sources)

    current_sources["lfp_official.pdf"] = (
        archive / "lfp_official.pdf"
    ).read_bytes()

    (
        new_calendar,
        new_states,
        new_provider,
    ) = season.normalize_sources(
        current_sources
    )

    new_calendar_id = calendar.dataset_id(
        new_calendar
    )

    new_states_id = calendar.dataset_id(
        new_states
    )

    new_provider_id = calendar.dataset_id(
        new_provider
    )

    require(
        new_calendar_id
        == manifest["calendar_id"],
        "CALENDAR_ID_CHANGED",
    )

    print(
        "Calendar ID:",
        new_calendar_id,
    )

    print(
        "States ID:",
        new_states_id,
    )

    print(
        "Provider ID:",
        new_provider_id,
    )

    print("\n=== COMPARISON ===")

    calendar_events = compare_entries(
        old_calendar,
        new_calendar,
    )

    state_events = compare_entries(
        old_states,
        new_states,
    )

    provider_events = compare_entries(
        old_provider,
        new_provider,
    )

    print_events(
        "CALENDAR EVENTS",
        calendar_events,
    )

    print_events(
        "STATE EVENTS",
        state_events,
    )

    print_events(
        "PROVIDER EVENTS",
        provider_events,
    )

    require(
        not calendar_events,
        "Unexpected calendar modifications",
    )

    print("\n=== SUMMARY ===")

    print(
        "Calendar changed:",
        False,
    )

    print(
        "States changed:",
        new_states_id
        != manifest["initial_states_id"],
    )

    print(
        "Provider changed:",
        new_provider_id
        != manifest["initial_provider_id"],
    )

    print(
        "State events:",
        len(state_events),
    )

    print(
        "Provider events:",
        len(provider_events),
    )

    print("\n=== FINAL STATUS ===")

    print("INITIAL ARCHIVE: VALID")
    print("CURRENT SOURCES: VALID")
    print("CALENDAR: UNCHANGED")
    print("CHANGE DETECTION: PASSED")

    print("NO ARCHIVES MODIFIED")
    print("NO SNAPSHOTS CREATED")
    print("NO OBSERVATIONS CREATED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
