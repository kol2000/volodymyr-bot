#!/usr/bin/env python3
"""Bubus Europe Results Mapping Audit V1.

Match existing Football-Data CSV results against
validated EPL and LaLiga season calendars.

READ ONLY / OFFLINE.

No network requests.
No database writes.
No forecasts.
"""

import csv
from collections import Counter
from datetime import datetime, date
from pathlib import Path

import football_europe_adapter_v1 as adapter
import football_europe_adapter_integrity_v1 as integrity


SOURCE = Path("state/football_live/source")

CODES = {
    "EPL": "E0",
    "LaLiga": "SP1",
}

# Explicit CSV name -> calendar name mappings.
# No fuzzy matching.

ALIASES = {
    "EPL": {
        "Bournemouth": "AFC Bournemouth",
        "Brighton": "Brighton & Hove Albion",
        "Coventry": "Coventry City",
        "Hull": "Hull City",
        "Ipswich": "Ipswich Town",
        "Leeds": "Leeds United",
        "Man City": "Manchester City",
        "Man United": "Manchester United",
        "Newcastle": "Newcastle United",
        "Nott'm Forest": "Nottingham Forest",
        "Tottenham": "Tottenham Hotspur",
    },

    "LaLiga": {
        "Alaves": "Deportivo Alavés",
        "Ath Bilbao": "Athletic Club",
        "Ath Madrid": "Atlético de Madrid",
        "Barcelona": "FC Barcelona",
        "Betis": "Real Betis",
        "Elche": "Elche CF",
        "Espanol": "RCD Espanyol de Barcelona",
        "Getafe": "Getafe CF",
        "La Coruna": "RC Deportivo",
        "Levante": "Levante UD",
        "Malaga": "Málaga CF",
        "Osasuna": "CA Osasuna",
        "Santander": "R. Racing Club",
        "Sevilla": "Sevilla FC",
        "Sociedad": "Real Sociedad",
        "Valencia": "Valencia CF",
        "Vallecano": "Rayo Vallecano",
        "Villarreal": "Villarreal CF",
    },
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def csv_date(value):
    return datetime.strptime(
        value.strip(), "%d/%m/%Y"
    ).date()


def validate_score(row, league):
    gh = row["FTHG"].strip()
    ga = row["FTAG"].strip()

    require(
        gh.isdigit() and ga.isdigit(),
        f"{league}: incomplete score",
    )

    home = int(gh)
    away = int(ga)

    require(
        0 <= home <= 30 and 0 <= away <= 30,
        f"{league}: invalid score",
    )

    expected = (
        "H" if home > away
        else "A" if home < away
        else "D"
    )

    require(
        row["FTR"].strip() == expected,
        f"{league}: FTR conflicts with score",
    )

    return home, away


def audit_league(league):

    print()
    print("=" * 65)
    print("LEAGUE:", league)
    print("=" * 65)

    # Verify the original immutable archive.
    integrity.validate_league(league)

    archive, manifest, calendar = (
        adapter.load_archive(league)
    )

    calendar_matches = calendar["matches"]

    calendar_teams = {
        team
        for match in calendar_matches
        for team in (match["home"], match["away"])
    }

    path = SOURCE / f"{league}_2026-27.csv"

    require(
        path.is_file(),
        f"{league}: CSV not found",
    )

    with path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        reader = csv.DictReader(f)

        require(
            reader.fieldnames is not None,
            f"{league}: missing CSV header",
        )

        rows = list(reader)

    aliases = ALIASES[league]

    csv_teams = {
        name.strip()
        for row in rows
        for name in (
            row["HomeTeam"],
            row["AwayTeam"],
        )
    }

    # Validate the complete name mapping.
    mapped_teams = {
        aliases.get(name, name)
        for name in csv_teams
    }

    require(
        mapped_teams == calendar_teams,
        f"{league}: team mapping mismatch. "
        f"Missing={sorted(calendar_teams - mapped_teams)}; "
        f"Unexpected={sorted(mapped_teams - calendar_teams)}",
    )

    require(
        len(mapped_teams) == len(csv_teams),
        f"{league}: multiple CSV names map to one team",
    )

    print("Team mapping: PASSED")
    print("Mapped teams:", len(mapped_teams))

    # Directed pair is unique within one
    # double round-robin season.

    fixture_index = {}

    for match in calendar_matches:
        key = (match["home"], match["away"])

        require(
            key not in fixture_index,
            f"{league}: duplicate directed fixture {key}",
        )

        fixture_index[key] = match

    print(
        "Unique calendar fixtures:",
        len(fixture_index),
    )

    matched = []
    seen = set()
    date_mismatches = []

    for line_number, row in enumerate(rows, 2):

        require(
            row["Div"].strip() == CODES[league],
            f"{league}: wrong division at line {line_number}",
        )

        raw_home = row["HomeTeam"].strip()
        raw_away = row["AwayTeam"].strip()

        home = aliases.get(raw_home, raw_home)
        away = aliases.get(raw_away, raw_away)

        key = (home, away)

        require(
            key not in seen,
            f"{league}: duplicate CSV fixture {key}",
        )

        seen.add(key)

        match = fixture_index.get(key)

        require(
            match is not None,
            f"{league}: unmatched CSV fixture {key}",
        )

        score = validate_score(row, league)

        result_date = csv_date(row["Date"])

        calendar_date = date.fromisoformat(
            match["date"]
        )

        if result_date != calendar_date:
            date_mismatches.append({
                "match_id": (
                    match.get("id")
                    or f"{home} / {away}"
                ),
                "home": home,
                "away": away,
                "calendar_date": (
                    calendar_date.isoformat()
                ),
                "result_date": (
                    result_date.isoformat()
                ),
                "difference_days": (
                    result_date - calendar_date
                ).days,
            })

        matched.append({
            "match_id": (
                match.get("id")
                or f"{home} / {away}"
            ),
            "date": result_date,
            "home": home,
            "away": away,
            "score": score,
        })

    print("CSV rows:", len(rows))
    print("Matched results:", len(matched))
    print("Unmatched results:", len(rows) - len(matched))

    print(
        "Date mismatches:",
        len(date_mismatches),
    )

    for item in date_mismatches[:25]:
        print(
            "DATE DIFFERENCE:",
            item["home"],
            "—",
            item["away"],
            "| Calendar:",
            item["calendar_date"],
            "| Result:",
            item["result_date"],
            "| Days:",
            item["difference_days"],
        )

    if matched:
        newest = max(
            item["date"]
            for item in matched
        )

        print(
            "Latest result date:",
            newest.isoformat(),
        )

    print(
        "Calendar fixtures without CSV results:",
        len(calendar_matches) - len(matched),
    )

    return len(matched), len(date_mismatches)


def main():

    print(
        "=== EUROPE RESULTS MAPPING AUDIT V1 ==="
    )
    print("MODE: READ ONLY / OFFLINE")

    total = 0
    differences = 0

    for league in CODES:
        matched, mismatches = audit_league(league)
        total += matched
        differences += mismatches

    print()
    print("=== SUMMARY ===")
    print("Matched results:", total)
    print("Date mismatches:", differences)

    print()
    print("=== FINAL STATUS ===")
    print("ARCHIVE INTEGRITY: PASSED")
    print("TEAM MAPPING: PASSED")
    print("FIXTURE MATCHING: PASSED")
    print("RESULT SCORES: STRUCTURALLY VALID")
    print("RESULT SOURCE FRESHNESS: NOT VERIFIED")
    print("FORECAST RECORDING: BLOCKED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
