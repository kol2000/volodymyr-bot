#!/usr/bin/env python3
"""Bubus Ligue 1 Discrepancy Investigation V1.

Investigate PSG vs Rennes:
- round 1
- round 23

Sources:
- official LFP PDF
- Open Matchday fixtures/results
- Football-Data results

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import csv
import io
from datetime import datetime

import football_europe_ligue1_lfp_validation_v1 as lfp
import football_europe_ligue1_season_audit_v1 as season
import football_europe_ligue1_sources_v1 as sources


PSG = "paris saint-germain"
RENNES = "stade rennais fc"

TARGET = {PSG, RENNES}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def show_match(source_name, item):

    print()
    print("SOURCE:", source_name)

    for field in (
        "gameweek",
        "date",
        "time",
        "home",
        "away",
        "home_score",
        "away_score",
        "finished",
    ):

        if field in item:
            print(
                field + ":",
                item[field],
            )


def is_target(home, away):

    return {
        season.normalize_name(home),
        season.normalize_name(away),
    } == TARGET


def inspect_official():

    print("\n=== OFFICIAL LFP ===")

    text, size = lfp.download_pdf()

    matches, groups, problems = (
        lfp.parse_official_calendar(text)
    )

    require(
        not problems,
        "Official PDF parsing failed",
    )

    require(
        len(matches) == 306,
        "Expected 306 official matches",
    )

    found = []

    for item in matches:

        if {
            item["home_key"],
            item["away_key"],
        } != TARGET:
            continue

        found.append(item)

        show_match(
            "LFP PDF",
            {
                "gameweek": item["gameweek"],
                "home": item["home_key"],
                "away": item["away_key"],
            },
        )

    require(
        len(found) == 2,
        "Expected two official encounters",
    )

    return found


def inspect_open_matchday():

    print("\n=== OPEN MATCHDAY ===")

    found = []

    for source_name, finished in (
        ("OPEN_MATCHDAY_RESULTS", True),
        ("OPEN_MATCHDAY_FIXTURES", False),
    ):

        rows, raw = season.fetch_csv(
            source_name
        )

        for row in rows:

            if not is_target(
                row["home"],
                row["away"],
            ):
                continue

            item = season.parse_match(
                row,
                finished,
            )

            found.append(item)

            show_match(
                source_name,
                item,
            )

            if finished:

                print(
                    "Original source:",
                    row.get("source"),
                )

    require(
        len(found) == 2,
        "Expected two Open Matchday encounters",
    )

    return found


FOOTBALL_DATA_NAMES = {
    "psg": PSG,
    "paris sg": PSG,
    "paris saint-germain": PSG,
    "rennes": RENNES,
    "stade rennais": RENNES,
    "stade rennais fc": RENNES,
}


def football_data_name(value):

    normalized = season.normalize_name(value)

    return FOOTBALL_DATA_NAMES.get(
        normalized,
        normalized,
    )


def inspect_football_data():

    print("\n=== FOOTBALL-DATA ===")

    url = sources.SOURCES[
        "FOOTBALL_DATA_RESULTS"
    ]

    status, content_type, raw = (
        sources.download(url)
    )

    require(
        status == 200,
        "Football-Data HTTP error",
    )

    rows = list(
        csv.DictReader(
            io.StringIO(
                raw.decode("utf-8-sig")
            )
        )
    )

    print("Available rows:", len(rows))

    found = []

    for row in rows:

        home = football_data_name(
            row["HomeTeam"]
        )

        away = football_data_name(
            row["AwayTeam"]
        )

        if {home, away} != TARGET:
            continue

        date = datetime.strptime(
            row["Date"],
            "%d/%m/%Y",
        ).date().isoformat()

        item = {
            "date": date,
            "time": row.get("Time"),
            "home": row["HomeTeam"],
            "away": row["AwayTeam"],
            "home_score": row.get("FTHG"),
            "away_score": row.get("FTAG"),
        }

        found.append(item)

        show_match(
            "FOOTBALL-DATA",
            item,
        )

    print(
        "\nMatches found in Football-Data:",
        len(found),
    )

    return found


def main():

    print(
        "=== BUBUS LIGUE 1 DISCREPANCY V1 ==="
    )

    print("MODE: READ ONLY")

    print("\nInvestigating:")
    print("Paris Saint-Germain")
    print("Stade Rennais FC")

    official = inspect_official()

    provider = inspect_open_matchday()

    independent = inspect_football_data()

    print("\n=== COMPARISON ===")

    for gameweek in (1, 23):

        official_match = next(
            item
            for item in official
            if item["gameweek"] == gameweek
        )

        provider_match = next(
            item
            for item in provider
            if item["gameweek"] == gameweek
        )

        official_pair = (
            official_match["home_key"],
            official_match["away_key"],
        )

        provider_pair = (
            provider_match["home_key"],
            provider_match["away_key"],
        )

        print()
        print("ROUND:", gameweek)
        print("Official:", official_pair)
        print("Provider:", provider_pair)

        print(
            "Exact match:",
            official_pair == provider_pair,
        )

        print(
            "Home/away reversed:",
            official_pair == provider_pair[::-1],
        )

    print("\n=== FINAL STATUS ===")

    print("OFFICIAL PDF: INSPECTED")
    print("OPEN MATCHDAY: INSPECTED")
    print("FOOTBALL-DATA: INSPECTED")

    print("NO AUTOMATIC CORRECTIONS")
    print("NO ARCHIVES CREATED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
