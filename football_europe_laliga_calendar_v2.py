#!/usr/bin/env python3
"""Bubus LaLiga Calendar V2.

Stable calendar identity independent of:
- current date/time
- match lifecycle status

READ ONLY.
No archive writes.
No forecast recording.
"""

import copy
from collections import Counter
from datetime import datetime, timezone, timedelta

import football_europe_laliga_calendar_v1 as v1


SCHEMA = "bubus-laliga-calendar-v2"


def normalize_stable(raw_matches):

    if len(raw_matches) != 380:
        raise RuntimeError("Expected 380 matches")

    # Reuse V1 structural checks.
    # Ignore its time-dependent calendar representation.
    v1.normalize(raw_matches)

    calendar = []
    statuses = []

    for match in raw_matches:

        match_id = match["id"]

        stamp = datetime.fromisoformat(
            match["date"]
        )

        if stamp.tzinfo is None:
            raise RuntimeError(
                f"Timezone missing: {match_id}"
            )

        stamp = stamp.astimezone(timezone.utc)

        midnight = (
            stamp.hour == 0
            and stamp.minute == 0
            and stamp.second == 0
            and stamp.microsecond == 0
        )

        home = match["home_team"]
        away = match["away_team"]

        week = match["gameweek"]["shortname"]

        if midnight:
            kickoff = None
            quality = "TIME_UNCONFIRMED"
        else:
            kickoff = stamp.isoformat()
            quality = "TIMED_UNVERIFIED"

        record = {
            "id": match_id,

            "home_id": home["id"],
            "home": (
                home.get("nickname")
                or home["name"]
            ),

            "away_id": away["id"],
            "away": (
                away.get("nickname")
                or away["name"]
            ),

            "gameweek": int(week[1:]),

            # Preserve the official API date,
            # even when its time is only a placeholder.
            "source_date_utc": stamp.isoformat(),

            "date": stamp.date().isoformat(),

            "kickoff_utc": kickoff,

            "schedule_status": quality,
        }

        calendar.append(record)

        statuses.append({
            "id": match_id,
            "match_status": match["status"],
        })

    calendar.sort(key=lambda x: x["id"])
    statuses.sort(key=lambda x: x["id"])

    content = {
        "schema": SCHEMA,
        "competition": "SP1",
        "competition_id": 1,
        "season": "2026-27",
        "season_id": 102,
        "matches": calendar,
    }

    return content, statuses


def content_id(content):
    return v1.sha256(v1.canonical(content))


def test_status_independence(raw_matches, original_id):

    modified = copy.deepcopy(raw_matches)

    changed = 0

    for match in modified:

        if match["status"] == "PreMatch":
            match["status"] = "FullTime"
            changed += 1

    content, statuses = normalize_stable(modified)

    test_id = content_id(content)

    if test_id != original_id:
        raise RuntimeError(
            "Content ID changed after match status update"
        )

    return changed


def test_date_independence(raw_matches, original_id):

    # No clock is consulted by normalize_stable().
    #
    # Test that changing only lifecycle status of
    # midnight fixtures does not alter calendar identity.

    modified = copy.deepcopy(raw_matches)

    changed = 0

    for match in modified:

        stamp = datetime.fromisoformat(
            match["date"]
        ).astimezone(timezone.utc)

        if (
            stamp.hour == 0
            and stamp.minute == 0
            and stamp.second == 0
            and stamp.microsecond == 0
        ):

            match["status"] = "FullTime"
            changed += 1

    content, statuses = normalize_stable(modified)

    test_id = content_id(content)

    if test_id != original_id:
        raise RuntimeError(
            "Content ID changed for midnight fixtures"
        )

    return changed


def test_schedule_change(raw_matches, original_id):

    modified = copy.deepcopy(raw_matches)

    target = next(
        match for match in modified
        if match["id"] == 102373
    )

    old = datetime.fromisoformat(target["date"])

    new = old + timedelta(hours=1)

    target["date"] = new.isoformat()

    content, statuses = normalize_stable(modified)

    test_id = content_id(content)

    if test_id == original_id:
        raise RuntimeError(
            "Kickoff change was not detected"
        )

    return old.isoformat(), new.isoformat(), test_id


def main():

    print("=== BUBUS LALIGA CALENDAR V2 ===")
    print("MODE: READ ONLY")

    raw_matches = v1.download_season()

    content, statuses = normalize_stable(raw_matches)

    original_id = content_id(content)

    counts = Counter(
        m["schedule_status"]
        for m in content["matches"]
    )

    status_counts = Counter(
        s["match_status"]
        for s in statuses
    )

    print("\n=== CALENDAR ===")

    print("Matches:", len(content["matches"]))
    print("Content ID:", original_id)

    print("\n=== SCHEDULE QUALITY ===")

    for key, count in sorted(counts.items()):
        print(key, count)

    print("\n=== MATCH STATUS ===")

    for key, count in sorted(status_counts.items()):
        print(key, count)

    print("\n=== STABILITY TESTS ===")

    changed = test_status_independence(
        raw_matches,
        original_id,
    )

    print(
        "Status transitions simulated:",
        changed
    )

    print("Status independence: PASSED")

    changed = test_date_independence(
        raw_matches,
        original_id,
    )

    print(
        "Midnight fixture transitions simulated:",
        changed
    )

    print("Midnight independence: PASSED")

    print("\n=== SCHEDULE CHANGE TEST ===")

    old, new, changed_id = test_schedule_change(
        raw_matches,
        original_id,
    )

    print("Match ID: 102373")
    print("Original kickoff:", old)
    print("Simulated kickoff:", new)

    print("Changed Content ID:", changed_id)
    print("Schedule change detection: PASSED")

    print("\n=== FINAL STATUS ===")

    print("STRUCTURE: VALID")
    print("CONTENT ID: STABLE AGAINST STATUS CHANGES")
    print("SCHEDULE CHANGE: DETECTED")
    print("KICKOFF VERIFICATION: NOT AUTHORIZED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
