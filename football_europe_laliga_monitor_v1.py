#!/usr/bin/env python3
"""Bubus LaLiga Monitor V1.

Compare official live API with archived season.
READ ONLY. No archive, observation or forecast writes.
"""

import json
from collections import Counter
from pathlib import Path

import football_europe_laliga_season_v1 as season
import football_europe_laliga_calendar_v2 as calendar


ROOT = Path(__file__).resolve().parent

ARCHIVES = (
    ROOT / "state/football_live/calendars/laliga_season"
)


def index_by_id(matches):
    result = {}

    for match in matches:
        match_id = match["id"]

        if match_id in result:
            raise RuntimeError(
                f"Duplicate match ID: {match_id}"
            )

        result[match_id] = match

    return result


def compare(old, new):
    before = index_by_id(old)
    after = index_by_id(new)

    events = []

    fields = (
        "home_id",
        "away_id",
        "gameweek",
        "source_date_utc",
        "date",
        "kickoff_utc",
        "schedule_status",
    )

    for match_id in sorted(set(before) | set(after)):

        a = before.get(match_id)
        b = after.get(match_id)

        if a is None:
            events.append({
                "type": "ADDED",
                "id": match_id,
            })
            continue

        if b is None:
            events.append({
                "type": "REMOVED",
                "id": match_id,
            })
            continue

        changes = {}

        for field in fields:
            if a.get(field) != b.get(field):
                changes[field] = {
                    "old": a.get(field),
                    "new": b.get(field),
                }

        if changes:
            events.append({
                "type": "SCHEDULE_CHANGED",
                "id": match_id,
                "match": f"{b['home']} — {b['away']}",
                "changes": changes,
            })

    return events


def status_index(statuses):
    return {
        item["id"]: item["match_status"]
        for item in statuses
    }


def compare_statuses(old, new):
    before = status_index(old)
    after = status_index(new)

    events = []

    for match_id in sorted(set(before) & set(after)):

        if before[match_id] != after[match_id]:
            events.append({
                "type": "MATCH_STATUS_CHANGED",
                "id": match_id,
                "old": before[match_id],
                "new": after[match_id],
            })

    return events


def main():

    print("=== BUBUS LALIGA MONITOR V1 ===")
    print("MODE: READ ONLY")

    if not ARCHIVES.is_dir():
        raise RuntimeError("Archive directory missing")

    directories = sorted(
        p for p in ARCHIVES.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )

    if len(directories) != 1:
        raise RuntimeError(
            "V1 requires exactly one archived version"
        )

    archived_path = directories[0]

    season.validate_archive(archived_path)

    old_content = json.loads(
        (archived_path / "calendar.json").read_text(
            encoding="utf-8"
        )
    )

    pages = json.loads(
        (archived_path / "source_pages.json").read_text(
            encoding="utf-8"
        )
    )

    _, old_statuses = season.reconstruct(pages)

    print("Archived Content ID:", archived_path.name)

    print("\n=== LIVE API ===")

    live_pages, live_matches = season.download()

    new_content, new_statuses = season.reconstruct(
        live_pages
    )

    new_id = calendar.content_id(new_content)

    print("Live Content ID:", new_id)
    print("Matches:", len(new_content["matches"]))

    print("\n=== SCHEDULE CHANGES ===")

    schedule_events = compare(
        old_content["matches"],
        new_content["matches"]
    )

    for event in schedule_events[:30]:
        print(json.dumps(
            event,
            ensure_ascii=False,
            indent=2,
        ))

    print("Schedule events:", len(schedule_events))

    print("\n=== MATCH STATUS CHANGES ===")

    status_events = compare_statuses(
        old_statuses,
        new_statuses,
    )

    for event in status_events[:30]:
        print(json.dumps(
            event,
            ensure_ascii=False,
            indent=2,
        ))

    print("Status events:", len(status_events))

    print("\n=== SUMMARY ===")

    if new_id == archived_path.name:
        print("Calendar content: UNCHANGED")
    else:
        print("Calendar content: CHANGED")

    print("Schedule events:", len(schedule_events))
    print("Match status events:", len(status_events))

    if new_id != archived_path.name and not schedule_events:
        raise RuntimeError(
            "Calendar content changed without detected events"
        )

    print("Existing archives: UNCHANGED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
