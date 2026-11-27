#!/usr/bin/env python3
"""Bubus Bundesliga Calendar V3.

Three independent datasets:
1. Calendar
2. Match states
3. Provider schedule observations

DFL fixture correspondence was validated separately.

READ ONLY.
No archive writes.
No forecast recording.
"""

from collections import Counter

import football_europe_bundesliga_calendar_v2 as v2


canonical = v2.canonical
content_id = v2.content_id
require = v2.require
fetch_season = v2.fetch_season


def normalize(data):

    content_v2, states = v2.normalize(data)

    # Keep the already validated V2 calendar
    # structure and rules unchanged.

    content = {
        "schema": "bubus-bundesliga-calendar-v3",
        "competition": "DE1",
        "season": "2026-27",
        "source": "OpenLigaDB",
        "matches": content_v2["matches"],
    }

    provider_schedule = []

    for raw in data:

        match_id = raw["matchID"]

        round_number = raw[
            "group"
        ]["groupOrderID"]

        # V1 validates both UTC and local dates.
        utc, local = v2.v1.parse_dates(raw)

        provider_schedule.append({
            "id": str(match_id),
            "gameweek": round_number,
            "date": local.date().isoformat(),
            "candidate_kickoff_utc": (
                utc.isoformat()
            ),
            "source_date_utc": (
                raw["matchDateTimeUTC"]
            ),
        })

    provider_schedule.sort(
        key=lambda item: item["id"]
    )

    calendar_ids = {
        match["id"]
        for match in content["matches"]
    }

    state_ids = {
        state["id"]
        for state in states
    }

    provider_ids = {
        item["id"]
        for item in provider_schedule
    }

    require(
        len(content["matches"]) == 306,
        "Invalid calendar size",
    )

    require(
        len(states) == 306,
        "Invalid state count",
    )

    require(
        len(provider_schedule) == 306,
        "Invalid provider schedule size",
    )

    require(
        len(calendar_ids) == 306,
        "Duplicate calendar IDs",
    )

    require(
        len(state_ids) == 306,
        "Duplicate state IDs",
    )

    require(
        len(provider_ids) == 306,
        "Duplicate provider schedule IDs",
    )

    require(
        calendar_ids == state_ids == provider_ids,
        "Dataset match IDs differ",
    )

    return content, states, provider_schedule


def main():

    print("=== BUBUS BUNDESLIGA CALENDAR V3 ===")
    print("MODE: READ ONLY")

    data = fetch_season()

    content, states, provider = normalize(data)

    calendar_id = content_id(content)
    states_id = content_id(states)
    provider_id = content_id(provider)

    print("\n=== DATASETS ===")

    print("Calendar matches:", len(content["matches"]))
    print("Match states:", len(states))
    print("Provider schedule:", len(provider))

    print("\n=== INDEPENDENT CONTENT IDS ===")

    print("Calendar ID:", calendar_id)
    print("States ID:", states_id)
    print("Provider Schedule ID:", provider_id)

    print("\n=== CALENDAR QUALITY ===")

    counts = Counter(
        m["schedule_status"]
        for m in content["matches"]
    )

    for name, count in sorted(counts.items()):
        print(name + ":", count)

    print("\n=== MATCH STATES ===")

    counts = Counter(
        state["status"]
        for state in states
    )

    for name, count in sorted(counts.items()):
        print(name + ":", count)

    print("\n=== PROVISIONAL KICKOFF SAMPLE ===")

    matches = {
        m["id"]: m
        for m in content["matches"]
    }

    examples = [
        item
        for item in provider
        if item["gameweek"] >= 19
    ]

    for item in examples[:3]:

        match = matches[item["id"]]

        print(
            match["home"],
            "—",
            match["away"],
        )

        print("Date:", match["date"])

        print(
            "Confirmed kickoff:",
            match["kickoff_utc"],
        )

        print(
            "Provider candidate:",
            item["candidate_kickoff_utc"],
        )

    print("\n=== FINAL STATUS ===")

    print("CALENDAR: VALID")
    print("MATCH STATES: VALID")
    print("PROVIDER SCHEDULE: VALID")
    print("ALL DATASETS: SEPARATED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
