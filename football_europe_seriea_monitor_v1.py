#!/usr/bin/env python3
"""Bubus Serie A Monitor V1.

Compare archived official season with current API.

READ ONLY.
No archive writes.
No observations writes.
No forecast recording.
"""

import json
from pathlib import Path

import football_europe_seriea_calendar_v1 as calendar
import football_europe_seriea_season_v1 as season


ROOT = Path(__file__).resolve().parent

ARCHIVES = (
    ROOT
    / "state/football_live/calendars/seriea_season"
)


def index_by_id(items):

    result = {}

    for item in items:

        match_id = item["id"]

        if match_id in result:
            raise RuntimeError(
                "Duplicate match ID: " + match_id
            )

        result[match_id] = item

    return result


def compare_calendar(old_matches, new_matches):

    before = index_by_id(old_matches)
    after = index_by_id(new_matches)

    events = []

    for match_id in sorted(
        set(before) | set(after)
    ):

        old = before.get(match_id)
        new = after.get(match_id)

        if old is None:

            events.append({
                "type": "MATCH_ADDED",
                "id": match_id,
                "new": new,
            })

            continue

        if new is None:

            events.append({
                "type": "MATCH_REMOVED",
                "id": match_id,
                "old": old,
            })

            continue

        changes = {}

        for field in sorted(
            set(old) | set(new)
        ):

            if field == "id":
                continue

            if old.get(field) != new.get(field):

                changes[field] = {
                    "old": old.get(field),
                    "new": new.get(field),
                }

        if changes:

            events.append({
                "type": "SCHEDULE_CHANGED",
                "id": match_id,
                "match": (
                    new["home"]
                    + " — "
                    + new["away"]
                ),
                "changes": changes,
            })

    return events


def compare_states(old_states, new_states):

    before = index_by_id(old_states)
    after = index_by_id(new_states)

    if set(before) != set(after):

        raise RuntimeError(
            "Match state ID sets differ"
        )

    events = []

    fields = (
        "status",
        "phase",
        "home_score",
        "away_score",
    )

    for match_id in sorted(before):

        old = before[match_id]
        new = after[match_id]

        changes = {}

        for field in fields:

            if old.get(field) != new.get(field):

                changes[field] = {
                    "old": old.get(field),
                    "new": new.get(field),
                }

        if changes:

            events.append({
                "type": "MATCH_STATE_CHANGED",
                "id": match_id,
                "changes": changes,
            })

    return events


def load_archive():

    if not ARCHIVES.is_dir():

        raise RuntimeError(
            "Serie A archive directory missing"
        )

    archives = [
        path
        for path in ARCHIVES.iterdir()
        if (
            path.is_dir()
            and not path.name.startswith(".")
        )
    ]

    if len(archives) != 1:

        raise RuntimeError(
            "Expected exactly one archived version; "
            "found " + str(len(archives))
        )

    path = archives[0]

    season.validate_archive(path)

    content = json.loads(
        (path / "calendar.json").read_text(
            encoding="utf-8"
        )
    )

    source = json.loads(
        (path / "source.json").read_text(
            encoding="utf-8"
        )
    )

    reconstructed, states = calendar.normalize(
        source
    )

    if calendar.canonical(reconstructed) != (
        calendar.canonical(content)
    ):

        raise RuntimeError(
            "Archive reconstruction mismatch"
        )

    return path, content, states


def main():

    print("=== BUBUS SERIE A MONITOR V1 ===")
    print("MODE: READ ONLY")

    print("\n=== ARCHIVED CALENDAR ===")

    archive, old_content, old_states = (
        load_archive()
    )

    old_id = calendar.content_id(
        old_content
    )

    print("Content ID:", old_id)
    print("Matches:", len(old_content["matches"]))
    print("States:", len(old_states))

    print("\n=== LIVE OFFICIAL API ===")

    raw, data = season.download()

    new_content, new_states = (
        calendar.normalize(data)
    )

    new_id = calendar.content_id(
        new_content
    )

    print("Content ID:", new_id)
    print("Matches:", len(new_content["matches"]))
    print("States:", len(new_states))
    print("Source SHA256:", season.sha(raw))

    print("\n=== CALENDAR CHANGES ===")

    schedule_events = compare_calendar(
        old_content["matches"],
        new_content["matches"],
    )

    print("Schedule events:", len(schedule_events))

    for event in schedule_events[:20]:

        print(
            json.dumps(
                event,
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\n=== MATCH STATE CHANGES ===")

    state_events = compare_states(
        old_states,
        new_states,
    )

    print("State events:", len(state_events))

    for event in state_events[:20]:

        print(
            json.dumps(
                event,
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\n=== CONSISTENCY CHECK ===")

    calendar_changed = old_id != new_id

    if calendar_changed and not schedule_events:

        raise RuntimeError(
            "Content ID changed without "
            "detected schedule events"
        )

    if not calendar_changed and schedule_events:

        raise RuntimeError(
            "Schedule events without "
            "Content ID change"
        )

    print(
        "Calendar:",
        "CHANGED" if calendar_changed
        else "UNCHANGED"
    )

    print("Schedule events:", len(schedule_events))
    print("State events:", len(state_events))

    print("\n=== FINAL STATUS ===")

    print("ARCHIVE: UNCHANGED")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")
    print("Exit status: SUCCESS")


if __name__ == "__main__":
    main()
