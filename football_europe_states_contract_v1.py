#!/usr/bin/env python3
"""Bubus Europe States Contract V1.

Inspect restored states from existing league monitors.

READ ONLY / OFFLINE.
No source downloads.
No forecasts.
No database writes.
"""

import json
from collections import Counter

import football_europe_seriea_monitor_v2 as seriea
import football_europe_bundesliga_monitor_v2 as bundesliga
import football_europe_ligue1_monitor_v2 as ligue1


def describe(name, value):

    print()
    print("DATASET:", name)
    print("Root type:", type(value).__name__)

    if isinstance(value, dict):

        print("Root keys:", sorted(value))

        matches = value.get("matches")

        if isinstance(matches, list):

            describe_matches(matches)

        elif isinstance(matches, dict):

            print("Matches:", len(matches))

            if matches:

                key = next(iter(matches))

                print("First key:", key)

                print(
                    "First value:",
                    json.dumps(
                        matches[key],
                        ensure_ascii=False,
                        default=str,
                    )[:1200],
                )

    elif isinstance(value, list):

        describe_matches(value)


def describe_matches(matches):

    print("Matches:", len(matches))

    if not matches:
        return

    first = matches[0]

    print(
        "First match:",
        json.dumps(
            first,
            ensure_ascii=False,
            default=str,
        )[:1200],
    )

    if not isinstance(first, dict):
        return

    print("Fields:", sorted(first))

    for field in (
        "finished",
        "status",
        "state",
        "home_score",
        "away_score",
        "home_goals",
        "away_goals",
    ):

        if field not in first:
            continue

        counts = Counter(
            str(row.get(field))
            for row in matches
            if isinstance(row, dict)
        )

        print(
            field,
            dict(counts.most_common(12)),
        )


def main():

    print("=== EUROPE STATES CONTRACT V1 ===")
    print("MODE: READ ONLY / OFFLINE")

    print("\n=== SERIE A ===")

    content, states, observation = (
        seriea.load_previous_observation()
    )

    describe("SerieA Calendar", content)
    describe("SerieA States", states)

    print(
        "Previous observation:",
        observation is not None,
    )

    print("\n=== BUNDESLIGA ===")

    content, states, provider, observation = (
        bundesliga.load_previous_observation()
    )

    describe("Bundesliga Calendar", content)
    describe("Bundesliga States", states)
    describe("Bundesliga Provider", provider)

    print(
        "Previous observation:",
        observation is not None,
    )

    print("\n=== LIGUE 1 ===")

    content, states, provider, observation = (
        ligue1.load_previous_observation()
    )

    describe("Ligue1 Calendar", content)
    describe("Ligue1 States", states)
    describe("Ligue1 Provider", provider)

    print(
        "Previous observation:",
        observation is not None,
    )

    print("\n=== LIGUE 1 RESULT CHECK ===")

    matches = states["matches"]

    target = [
        row
        for row in matches
        if row.get("match_id")
        == "ligue1-2026-27-r11-paris fc-angers sco"
    ]

    if len(target) != 1:
        raise RuntimeError(
            "Expected exactly one Paris FC - Angers match"
        )

    match = target[0]

    print(
        json.dumps(
            match,
            ensure_ascii=False,
            indent=2,
        )
    )

    if not (
        match["finished"] is True
        and match["home_score"] == 4
        and match["away_score"] == 0
    ):
        raise RuntimeError(
            "Latest Ligue 1 result was not restored"
        )

    print("LATEST LIGUE 1 RESULT: VERIFIED")

    print("\n=== FINAL STATUS ===")

    print("STATE RESTORATION: PASSED")
    print("LIGUE 1 LATEST RESULT: VERIFIED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
