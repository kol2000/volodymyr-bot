#!/usr/bin/env python3
"""Bubus Bundesliga Monitor V1 Tests.

Test three independent event streams.

All modifications are in memory.
No network requests.
No file writes.
No forecast recording.
"""

import copy

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import football_europe_bundesliga_monitor_v1 as monitor


BERLIN = ZoneInfo("Europe/Berlin")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def compare_all(old, new):

    old_calendar, old_states, old_provider = old
    new_calendar, new_states, new_provider = new

    calendar_events = monitor.compare_datasets(
        old_calendar["matches"],
        new_calendar["matches"],
        "CALENDAR",
    )

    state_events = monitor.compare_datasets(
        old_states,
        new_states,
        "STATE",
    )

    provider_events = monitor.compare_datasets(
        old_provider,
        new_provider,
        "PROVIDER",
    )

    return (
        calendar_events,
        state_events,
        provider_events,
    )


def check_counts(events, expected):

    actual = tuple(
        len(group)
        for group in events
    )

    require(
        actual == expected,
        f"Expected {expected}, got {actual}",
    )

    print(
        "PASS:",
        "calendar=" + str(actual[0]),
        "states=" + str(actual[1]),
        "provider=" + str(actual[2]),
    )


def main():

    print("=== BUNDESLIGA MONITOR V1 TESTS ===")
    print("MODE: READ ONLY")

    archive, content, states, provider = (
        monitor.load_archive()
    )

    original = (
        content,
        states,
        provider,
    )

    print("\n=== BASELINE ===")

    print("Matches:", len(content["matches"]))
    print("States:", len(states))
    print("Provider:", len(provider))

    print("\n=== TEST 1: IDENTICAL DATA ===")

    check_counts(
        compare_all(original, original),
        (0, 0, 0),
    )

    print("\n=== TEST 2: SCORE CHANGE ===")

    modified_states = copy.deepcopy(states)

    target = next(
        item for item in modified_states
        if item["status"] == "FINISHED"
    )

    target["home_score"] += 1

    events = compare_all(
        original,
        (content, modified_states, provider),
    )

    check_counts(events, (0, 1, 0))

    require(
        "home_score" in events[1][0]["changes"],
        "Score event missing",
    )

    print("\n=== TEST 3: FINISHED FLAG ===")

    modified_states = copy.deepcopy(states)

    target = next(
        item for item in modified_states
        if item["status"] == "NOT_FINISHED_WITH_RESULT"
    )

    target["finished"] = True
    target["status"] = "FINISHED"

    target["home_score"] = (
        target["reported_home_score"]
    )

    target["away_score"] = (
        target["reported_away_score"]
    )

    events = compare_all(
        original,
        (content, modified_states, provider),
    )

    check_counts(events, (0, 1, 0))

    require(
        "status" in events[1][0]["changes"],
        "Status event missing",
    )

    print("\n=== TEST 4: PROVISIONAL KICKOFF ===")

    modified_provider = copy.deepcopy(provider)

    target = next(
        item for item in modified_provider
        if item["gameweek"] == 19
    )

    old = datetime.fromisoformat(
        target["candidate_kickoff_utc"]
    )

    new = old + timedelta(hours=1)

    target["candidate_kickoff_utc"] = (
        new.isoformat()
    )

    target["source_date_utc"] = (
        new.isoformat().replace(
            "+00:00", "Z"
        )
    )

    events = compare_all(
        original,
        (content, states, modified_provider),
    )

    check_counts(events, (0, 0, 1))

    print("\n=== TEST 5: TIMED KICKOFF ===")

    modified_calendar = copy.deepcopy(content)
    modified_provider = copy.deepcopy(provider)

    target = next(
        item for item in modified_calendar["matches"]
        if item["gameweek"] == 11
    )

    identifier = target["id"]

    old = datetime.fromisoformat(
        target["kickoff_utc"]
    )

    new = old + timedelta(hours=1)

    target["kickoff_utc"] = new.isoformat()

    target["source_date_utc"] = (
        new.isoformat().replace(
            "+00:00", "Z"
        )
    )

    provider_target = next(
        item for item in modified_provider
        if item["id"] == identifier
    )

    provider_target["candidate_kickoff_utc"] = (
        new.isoformat()
    )

    provider_target["source_date_utc"] = (
        new.isoformat().replace(
            "+00:00", "Z"
        )
    )

    events = compare_all(
        original,
        (
            modified_calendar,
            states,
            modified_provider,
        ),
    )

    check_counts(events, (1, 0, 1))

    print("\n=== TEST 6: PROVISIONAL DATE ===")

    modified_calendar = copy.deepcopy(content)
    modified_provider = copy.deepcopy(provider)

    target = next(
        item for item in modified_calendar["matches"]
        if item["gameweek"] == 19
    )

    identifier = target["id"]

    old_date = datetime.fromisoformat(
        target["date"]
    )

    new_date = old_date + timedelta(days=1)

    target["date"] = (
        new_date.date().isoformat()
    )

    provider_target = next(
        item for item in modified_provider
        if item["id"] == identifier
    )

    old = datetime.fromisoformat(
        provider_target["candidate_kickoff_utc"]
    )

    new = old + timedelta(days=1)

    provider_target["date"] = (
        new.astimezone(BERLIN)
        .date()
        .isoformat()
    )

    provider_target["candidate_kickoff_utc"] = (
        new.isoformat()
    )

    provider_target["source_date_utc"] = (
        new.isoformat().replace(
            "+00:00", "Z"
        )
    )

    events = compare_all(
        original,
        (
            modified_calendar,
            states,
            modified_provider,
        ),
    )

    check_counts(events, (1, 0, 1))

    print("\n=== FINAL STATUS ===")

    print("ALL 6 TESTS PASSED")
    print("THREE EVENT STREAMS: VERIFIED")
    print("ARCHIVE: UNCHANGED")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
