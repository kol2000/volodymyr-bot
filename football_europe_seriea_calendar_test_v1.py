#!/usr/bin/env python3
"""Bubus Serie A Calendar Tests V1.

Test calendar stability and schedule-change detection.

All modifications are in memory.

READ ONLY.
No archive writes.
No forecast recording.
"""

import copy

from datetime import datetime, timedelta, timezone

import football_europe_seriea_calendar_v1 as calendar


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def get_id(data):
    content, states = calendar.normalize(data)

    return calendar.content_id(content), content, states


def main():

    print("=== BUBUS SERIE A CALENDAR TEST V1 ===")
    print("MODE: READ ONLY")

    print("\n=== DOWNLOAD OFFICIAL SEASON ===")

    original = calendar.fetch_season()

    base_id, base_content, base_states = get_id(
        original
    )

    print("Matches:", len(base_content["matches"]))
    print("States:", len(base_states))
    print("Content ID:", base_id)

    print("\n=== TEST 1: IDENTICAL DATA ===")

    same = copy.deepcopy(original)

    same_id, _, _ = get_id(same)

    check(
        same_id == base_id,
        "Identical data changed Content ID",
    )

    print("PASS: Content ID unchanged")

    print("\n=== TEST 2: SCORE CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        match for match in modified["matches"]
        if match["status"] == "FINISHED"
    )

    old_score = target["providerHomeScore"]

    check(
        isinstance(old_score, int),
        "Expected integer score",
    )

    target["providerHomeScore"] = old_score + 1

    changed_id, _, changed_states = get_id(
        modified
    )

    check(
        changed_id == base_id,
        "Score affected calendar Content ID",
    )

    changed_state = next(
        state for state in changed_states
        if state["id"] == target["matchId"]
    )

    check(
        changed_state["home_score"] == old_score + 1,
        "Score change missing from states",
    )

    print("PASS: score change isolated")
    print("Match:", target["matchId"])
    print("Old score:", old_score)
    print("New score:", old_score + 1)

    print("\n=== TEST 3: MATCH STATUS CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        match for match in modified["matches"]
        if match["status"] == "UPCOMING"
    )

    target["status"] = "FINISHED"
    target["phase"] = "FULL_TIME"

    changed_id, _, changed_states = get_id(
        modified
    )

    check(
        changed_id == base_id,
        "Status affected calendar Content ID",
    )

    changed_state = next(
        state for state in changed_states
        if state["id"] == target["matchId"]
    )

    check(
        changed_state["status"] == "FINISHED",
        "Status change missing from states",
    )

    print("PASS: status change isolated")
    print("Match:", target["matchId"])

    print("\n=== TEST 4: KICKOFF TRANSFER ===")

    modified = copy.deepcopy(original)

    target = next(
        match for match in modified["matches"]
        if (
            match["status"] == "UPCOMING"
            and match["isUnknownKickOffTime"] is False
        )
    )

    old = target["matchDateUtc"]

    parsed = datetime.fromisoformat(
        old.replace("Z", "+00:00")
    )

    new = (
        parsed + timedelta(hours=1)
    ).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )

    target["matchDateUtc"] = new

    changed_id, changed_content, _ = get_id(
        modified
    )

    check(
        changed_id != base_id,
        "Kickoff transfer was not detected",
    )

    updated_match = next(
        match for match in changed_content["matches"]
        if match["id"] == target["matchId"]
    )

    check(
        updated_match["source_date_utc"] == new,
        "New kickoff missing from calendar",
    )

    print("PASS: kickoff transfer detected")
    print("Match:", target["matchId"])
    print("Old:", old)
    print("New:", new)

    print("\n=== TEST 5: UNKNOWN TO TIMED ===")

    modified = copy.deepcopy(original)

    target = next(
        match for match in modified["matches"]
        if (
            match["status"] == "UPCOMING"
            and match["isUnknownKickOffTime"] is True
        )
    )

    old = target["matchDateUtc"]

    date = old[:10]

    new = date + "T19:45:00Z"

    target["matchDateUtc"] = new

    target["isUnknownKickOffTime"] = False

    changed_id, changed_content, _ = get_id(
        modified
    )

    check(
        changed_id != base_id,
        "New kickoff time was not detected",
    )

    updated_match = next(
        match for match in changed_content["matches"]
        if match["id"] == target["matchId"]
    )

    check(
        updated_match["schedule_status"]
        == "TIMED_UNVERIFIED",
        "Schedule status not updated",
    )

    check(
        updated_match["kickoff_utc"] is not None,
        "New kickoff was not saved",
    )

    print("PASS: unknown kickoff became timed")
    print(
        "Match:",
        updated_match["home"],
        "—",
        updated_match["away"],
    )
    print("Old:", old)
    print("New:", new)

    print("\n=== TEST 6: MATCH ORDER ===")

    modified = copy.deepcopy(original)

    modified["matches"].reverse()

    reordered_id, _, _ = get_id(
        modified
    )

    check(
        reordered_id == base_id,
        "Match ordering affected Content ID",
    )

    print("PASS: match order does not matter")

    print("\n=== FINAL STATUS ===")
    print("ALL 6 TESTS PASSED")
    print("Original Content ID:", base_id)
    print("ARCHIVES: UNCHANGED")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
