#!/usr/bin/env python3
"""Bubus Bundesliga Calendar V2 Tests.

All mutations are performed in memory.

READ ONLY.
"""

import copy

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import football_europe_bundesliga_calendar_v2 as calendar


BERLIN = ZoneInfo("Europe/Berlin")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def normalize_id(data):
    content, states = calendar.normalize(data)

    return calendar.content_id(content), content, states


def change_kickoff(match, hours=0, days=0):

    old_utc = datetime.fromisoformat(
        match["matchDateTimeUTC"].replace(
            "Z", "+00:00"
        )
    )

    new_utc = old_utc + timedelta(
        hours=hours,
        days=days,
    )

    new_local = new_utc.astimezone(BERLIN)

    match["matchDateTimeUTC"] = (
        new_utc.isoformat().replace(
            "+00:00", "Z"
        )
    )

    match["matchDateTime"] = (
        new_local.strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    )


def main():

    print("=== BUNDESLIGA CALENDAR V2 TESTS ===")
    print("MODE: READ ONLY")

    original = calendar.fetch_season()

    base_id, content, states = normalize_id(
        original
    )

    print("\n=== BASELINE ===")

    print("Matches:", len(content["matches"]))
    print("States:", len(states))
    print("Content ID:", base_id)

    print("\n=== TEST 1: IDENTICAL DATA ===")

    same_id, _, _ = normalize_id(
        copy.deepcopy(original)
    )

    check(
        same_id == base_id,
        "Identical data changed Content ID",
    )

    print("PASS: identical data")

    print("\n=== TEST 2: FINAL SCORE CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        m for m in modified
        if m["matchIsFinished"]
    )

    result = next(
        r for r in target["matchResults"]
        if r["resultTypeKind"]
        == "After90Minutes"
    )

    result["pointsTeam1"] += 1

    changed_id, _, changed_states = normalize_id(
        modified
    )

    check(
        changed_id == base_id,
        "Score affected calendar ID",
    )

    state = next(
        s for s in changed_states
        if s["id"] == str(target["matchID"])
    )

    check(
        state["home_score"]
        == result["pointsTeam1"],
        "Updated score missing from states",
    )

    print("PASS: score change isolated")

    print("\n=== TEST 3: FINISHED FLAG CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        m for m in modified
        if m["matchIsFinished"]
    )

    target["matchIsFinished"] = False

    changed_id, _, changed_states = normalize_id(
        modified
    )

    check(
        changed_id == base_id,
        "Finished flag affected calendar ID",
    )

    state = next(
        s for s in changed_states
        if s["id"] == str(target["matchID"])
    )

    check(
        state["status"]
        == "NOT_FINISHED_WITH_RESULT",
        "Finished flag change missing",
    )

    print("PASS: finished flag isolated")

    print("\n=== TEST 4: TIMED KICKOFF CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        m for m in modified
        if (
            m["group"]["groupOrderID"] == 11
            and not m["matchIsFinished"]
        )
    )

    change_kickoff(target, hours=1)

    changed_id, _, _ = normalize_id(
        modified
    )

    check(
        changed_id != base_id,
        "Timed kickoff change not detected",
    )

    print("PASS: timed kickoff change detected")

    print("\n=== TEST 5: PLACEHOLDER HOUR CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        m for m in modified
        if m["group"]["groupOrderID"] == 19
    )

    change_kickoff(target, hours=1)

    changed_id, _, _ = normalize_id(
        modified
    )

    check(
        changed_id == base_id,
        "Placeholder hour affected calendar ID",
    )

    print("PASS: placeholder hour ignored")

    print("\n=== TEST 6: PROVISIONAL DATE CHANGE ===")

    modified = copy.deepcopy(original)

    target = next(
        m for m in modified
        if m["group"]["groupOrderID"] == 19
    )

    change_kickoff(target, days=1)

    changed_id, _, _ = normalize_id(
        modified
    )

    check(
        changed_id != base_id,
        "Provisional date change not detected",
    )

    print("PASS: calendar date change detected")

    print("\n=== TEST 7: MATCH ORDER ===")

    modified = copy.deepcopy(original)

    modified.reverse()

    changed_id, _, _ = normalize_id(
        modified
    )

    check(
        changed_id == base_id,
        "Match ordering affected Content ID",
    )

    print("PASS: match order ignored")

    print("\n=== FINAL STATUS ===")
    print("ALL 7 TESTS PASSED")
    print("CALENDAR CONTENT ID:", base_id)
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
