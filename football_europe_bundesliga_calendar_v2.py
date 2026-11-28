#!/usr/bin/env python3
"""Bubus Bundesliga Calendar V2.

Stable normalized Bundesliga 2026/27 calendar.

Calendar content ID does NOT depend on:
- matchIsFinished
- matchResults
- placeholder kickoff times in rounds 19-34
- lastUpdateDateTime

READ ONLY.
No archive writes.
No forecast recording.
"""

from collections import Counter
from datetime import datetime

import football_europe_bundesliga_calendar_v1 as v1


canonical = v1.canonical
content_id = v1.content_id
require = v1.require
fetch_season = v1.fetch_season


def normalize(data):

    # V1 performs structural validation and
    # extracts match results separately.

    original_content, states = v1.normalize(data)

    matches = []

    for source in original_content["matches"]:

        round_number = source["gameweek"]

        # Schedule quality must NOT depend on
        # whether the match has finished.

        if round_number <= 18:

            schedule_status = "TIMED_UNVERIFIED"

            kickoff_utc = source[
                "candidate_kickoff_utc"
            ]

        else:

            schedule_status = "TIME_UNCONFIRMED"

            kickoff_utc = None

        match = {
            "id": source["id"],
            "home_id": source["home_id"],
            "home": source["home"],
            "away_id": source["away_id"],
            "away": source["away"],
            "gameweek": round_number,
            "date": source["date"],
            "kickoff_utc": kickoff_utc,
            "schedule_status": schedule_status,
        }

        # For rounds with specified kickoff times,
        # preserve the provider's timestamp.
        #
        # For provisional rounds, do not include
        # placeholder times in calendar content.

        if round_number <= 18:

            match["source_date_utc"] = source[
                "source_date_utc"
            ]

        matches.append(match)

    content = {
        "schema": "bubus-bundesliga-calendar-v2",
        "competition": "DE1",
        "season": "2026-27",
        "source": "OpenLigaDB",
        "matches": sorted(
            matches,
            key=lambda m: m["id"],
        ),
    }

    require(
        len(content["matches"]) == 306,
        "Invalid calendar size",
    )

    require(
        len(states) == 306,
        "Invalid state count",
    )

    return content, states


def main():

    print("=== BUBUS BUNDESLIGA CALENDAR V2 ===")
    print("MODE: READ ONLY")

    data = fetch_season()

    content, states = normalize(data)

    print("\n=== CALENDAR ===")

    print("Matches:", len(content["matches"]))

    counts = Counter(
        m["schedule_status"]
        for m in content["matches"]
    )

    for name, count in sorted(counts.items()):
        print(name + ":", count)

    print("\n=== CONTENT ID ===")

    print(content_id(content))

    print("\n=== STATES ===")

    statuses = Counter(
        s["status"]
        for s in states
    )

    for name, count in sorted(statuses.items()):
        print(name + ":", count)

    print("\n=== UNCONFIRMED KICKOFFS ===")

    unknown = [
        m for m in content["matches"]
        if m["schedule_status"]
        == "TIME_UNCONFIRMED"
    ]

    for match in unknown[:3]:

        print(
            match["home"],
            "—",
            match["away"],
        )

        print("Date:", match["date"])
        print("Kickoff:", match["kickoff_utc"])

    print("\n=== FINAL STATUS ===")

    print("CALENDAR STRUCTURE: VALID")
    print("STATES: SEPARATED")
    print("RESULT-DEPENDENT FIELDS: EXCLUDED")
    print("PROVISIONAL KICKOFFS: EXCLUDED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
