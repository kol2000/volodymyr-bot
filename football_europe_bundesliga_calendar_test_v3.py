#!/usr/bin/env python3
"""Bubus Bundesliga V3 Independence Tests.

All changes are performed in memory.
READ ONLY.
"""

import copy

from datetime import timedelta

import football_europe_bundesliga_calendar_v3 as v3


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def identifiers(data):

    content, states, provider = v3.normalize(data)

    return (
        v3.content_id(content),
        v3.content_id(states),
        v3.content_id(provider),
    )


def move_kickoff(match):

    utc, local = v3.v2.v1.parse_dates(match)

    new_utc = utc + timedelta(hours=1)

    new_local = new_utc.astimezone(
        v3.v2.v1.BERLIN
    )

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

    print("=== BUNDESLIGA V3 INDEPENDENCE TESTS ===")
    print("MODE: READ ONLY")

    original = v3.fetch_season()

    base = identifiers(original)

    print("\n=== BASELINE ===")

    print("Calendar:", base[0])
    print("States:", base[1])
    print("Provider:", base[2])

    print("\n=== TEST 1: SCORE CHANGE ===")

    data = copy.deepcopy(original)

    target = next(
        m for m in data
        if m["matchIsFinished"]
    )

    result = next(
        r for r in target["matchResults"]
        if r["resultTypeKind"]
        == "After90Minutes"
    )

    result["pointsTeam1"] += 1

    changed = identifiers(data)

    require(
        changed[0] == base[0]
        and changed[1] != base[1]
        and changed[2] == base[2],
        "Score isolation failed",
    )

    print("PASS: only States ID changed")

    print("\n=== TEST 2: FINISHED FLAG ===")

    data = copy.deepcopy(original)

    target = next(
        m for m in data
        if m["matchIsFinished"]
    )

    target["matchIsFinished"] = False

    changed = identifiers(data)

    require(
        changed[0] == base[0]
        and changed[1] != base[1]
        and changed[2] == base[2],
        "Finished flag isolation failed",
    )

    print("PASS: only States ID changed")

    print("\n=== TEST 3: PROVISIONAL KICKOFF ===")

    data = copy.deepcopy(original)

    target = next(
        m for m in data
        if m["group"]["groupOrderID"] == 19
    )

    move_kickoff(target)

    changed = identifiers(data)

    require(
        changed[0] == base[0]
        and changed[1] == base[1]
        and changed[2] != base[2],
        "Provisional kickoff isolation failed",
    )

    print("PASS: only Provider ID changed")

    print("\n=== TEST 4: TIMED KICKOFF ===")

    data = copy.deepcopy(original)

    target = next(
        m for m in data
        if m["group"]["groupOrderID"] == 11
    )

    move_kickoff(target)

    changed = identifiers(data)

    require(
        changed[0] != base[0]
        and changed[1] == base[1]
        and changed[2] != base[2],
        "Timed kickoff isolation failed",
    )

    print("PASS: Calendar and Provider IDs changed")

    print("\n=== TEST 5: MATCH ORDER ===")

    data = copy.deepcopy(original)

    data.reverse()

    changed = identifiers(data)

    require(
        changed == base,
        "Match order affected Content IDs",
    )

    print("PASS: all Content IDs unchanged")

    print("\n=== FINAL STATUS ===")

    print("ALL 5 TESTS PASSED")
    print("THREE INDEPENDENT DATASETS: VERIFIED")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
