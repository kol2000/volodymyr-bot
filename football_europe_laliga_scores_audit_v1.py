#!/usr/bin/env python3
"""Bubus LaLiga Scores Audit V1.

Compare restored LaLiga provider results
against Football-Data CSV results.

STRICTLY READ ONLY / OFFLINE.

No network.
No database writes.
No forecast generation.
No Telegram.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

import football_europe_adapter_v3 as adapter
import football_europe_laliga_monitor_v3 as laliga


ROOT = Path(__file__).resolve().parent

SOURCE_ROOT = (
    ROOT
    / "state/football_live/calendars"
    / "laliga_observation_sources"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_score(home, away):
    return (
        type(home) is int
        and type(away) is int
        and 0 <= home <= 30
        and 0 <= away <= 30
    )


def load_provider():

    content_id, statuses, observation = (
        laliga.previous_state()
    )

    require(
        observation is not None,
        "No LaLiga observation",
    )

    source_id = observation["source_sha256"]

    path = (
        SOURCE_ROOT
        / source_id
        / "source_pages.json"
    )

    raw = path.read_bytes()

    require(
        hashlib.sha256(raw).hexdigest()
        == source_id,
        "Provider source SHA256 mismatch",
    )

    pages = json.loads(raw)

    require(
        isinstance(pages, list),
        "Invalid source pages",
    )

    records = {}

    for page in pages:

        require(
            isinstance(page, dict),
            "Invalid page",
        )

        response = page["response"]
        matches = response["matches"]

        require(
            isinstance(matches, list),
            "Invalid matches list",
        )

        for row in matches:

            match_id = str(row["id"])

            require(
                match_id not in records,
                "Duplicate provider match ID: "
                + match_id,
            )

            records[match_id] = row

    require(
        len(records) == 380,
        "Expected 380 provider matches",
    )

    status_index = {}

    for state in statuses:

        match_id = str(state["id"])

        require(
            match_id not in status_index,
            "Duplicate restored status ID",
        )

        status_index[match_id] = (
            state["match_status"]
        )

    require(
        set(status_index) == set(records),
        "Restored statuses/provider IDs differ",
    )

    for match_id, row in records.items():

        require(
            row["status"] == status_index[match_id],
            "Provider/restored status mismatch: "
            + match_id,
        )

    finished = {}

    for match_id, row in records.items():

        if row["status"] != "FullTime":
            continue

        home = row.get("home_score")
        away = row.get("away_score")

        require(
            valid_score(home, away),
            "Invalid FullTime score: "
            + match_id,
        )

        finished[match_id] = (home, away)

    return finished, records, source_id


def main():

    print("=== LALIGA RESULT CROSSCHECK V1 ===")
    print("MODE: READ ONLY / OFFLINE")

    print("\n=== UNIFIED ADAPTER ===")

    matches, counts = adapter.load_all()

    fixtures = {
        m["match_id"]: m
        for m in matches
        if m["competition"] == "LaLiga"
    }

    require(
        len(fixtures) == 380,
        "Incorrect LaLiga fixture count",
    )

    csv_results = {}

    for match_id, match in fixtures.items():

        if match["state_status"] != "RESULT_REPORTED":
            continue

        require(
            valid_score(
                match["home_score"],
                match["away_score"],
            ),
            "Invalid CSV result",
        )

        csv_results[match_id] = (
            match["home_score"],
            match["away_score"],
        )

    print(
        "Football-Data results:",
        len(csv_results),
    )

    print("\n=== PROVIDER ===")

    provider, records, source_id = (
        load_provider()
    )

    require(
        set(records) == set(fixtures),
        "Provider/calendar match IDs differ",
    )

    print("Source SHA256:", source_id)

    print(
        "Provider FullTime results:",
        len(provider),
    )

    print("\n=== CROSSCHECK ===")

    common = (
        set(csv_results) & set(provider)
    )

    only_csv = (
        set(csv_results) - set(provider)
    )

    only_provider = (
        set(provider) - set(csv_results)
    )

    conflicts = [
        match_id
        for match_id in common
        if csv_results[match_id]
        != provider[match_id]
    ]

    print("Common results:", len(common))

    print(
        "Matching scores:",
        len(common) - len(conflicts),
    )

    print("Score conflicts:", len(conflicts))

    print(
        "Only Football-Data:",
        len(only_csv),
    )

    print(
        "Only provider:",
        len(only_provider),
    )

    print("\n=== SCORE CONFLICTS ===")

    for match_id in sorted(conflicts):

        match = fixtures[match_id]

        print(
            match_id,
            "|",
            match["home"],
            "—",
            match["away"],
            "| CSV:",
            csv_results[match_id],
            "| Provider:",
            provider[match_id],
        )

    print("\n=== PROVIDER-ONLY RESULTS ===")

    for match_id in sorted(only_provider):

        match = fixtures[match_id]
        score = provider[match_id]

        print(
            match_id,
            "|",
            match["date"],
            "|",
            match["home"],
            "—",
            match["away"],
            "|",
            f"{score[0]}:{score[1]}",
        )

    print("\n=== CSV-ONLY RESULTS ===")

    for match_id in sorted(only_csv):

        match = fixtures[match_id]

        print(
            match_id,
            "|",
            match["home"],
            "—",
            match["away"],
            "|",
            csv_results[match_id],
        )

    print("\n=== SUMMARY ===")

    print("Calendar matches:", len(fixtures))
    print("CSV results:", len(csv_results))
    print("Provider results:", len(provider))
    print("Common:", len(common))
    print("Conflicts:", len(conflicts))
    print("Provider-only:", len(only_provider))
    print("CSV-only:", len(only_csv))

    print("\n=== FINAL STATUS ===")

    if conflicts or only_csv:

        print("RESULT RECONCILIATION: REVIEW REQUIRED")

    else:

        print("RESULT RECONCILIATION: PASSED")

    print("SOURCE CHECKSUM: VERIFIED")
    print("MATCH IDENTITIES: VERIFIED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")

    if conflicts or only_csv:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
