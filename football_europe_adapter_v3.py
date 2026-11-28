#!/usr/bin/env python3
"""Bubus Europe Adapter V3.

Unified five-league calendar with restored states
and mapped Football-Data CSV results.

STRICTLY READ ONLY / OFFLINE.

No network requests.
No database writes.
No forecast calculations.
No Telegram messages.

CSV results are marked RESULT_REPORTED until
independently verified.
"""

import csv
from collections import Counter
from datetime import datetime, timezone, date
from pathlib import Path

import football_europe_adapter_v2 as v2
import football_europe_results_mapping_audit_v1 as mapping


ROOT = Path(__file__).resolve().parent

SOURCE = (
    ROOT / "state/football_live/source"
)

CSV_LEAGUES = {
    "EPL": "E0",
    "LaLiga": "SP1",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_csv_results(league, fixtures, source_dir=None):

    path = (SOURCE if source_dir is None else Path(source_dir)) / f"{league}_2026-27.csv"

    require(
        path.is_file(),
        f"{league}: CSV missing",
    )

    with path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        require(
            reader.fieldnames is not None,
            f"{league}: CSV header missing",
        )

        required = {
            "Div",
            "Date",
            "HomeTeam",
            "AwayTeam",
            "FTHG",
            "FTAG",
            "FTR",
        }

        require(
            required.issubset(reader.fieldnames),
            f"{league}: CSV schema mismatch",
        )

        rows = list(reader)

    aliases = mapping.ALIASES[league]

    fixture_index = {}

    for fixture in fixtures:

        key = (
            fixture["home"],
            fixture["away"],
        )

        require(
            key not in fixture_index,
            f"{league}: duplicate calendar pair",
        )

        fixture_index[key] = fixture

    calendar_teams = {
        team
        for home, away in fixture_index
        for team in (home, away)
    }

    csv_teams = {
        value.strip()
        for row in rows
        for value in (
            row["HomeTeam"],
            row["AwayTeam"],
        )
    }

    mapped_teams = {
        aliases.get(name, name)
        for name in csv_teams
    }

    require(
        mapped_teams == calendar_teams,
        f"{league}: incomplete team mapping",
    )

    require(
        len(mapped_teams) == len(csv_teams),
        f"{league}: ambiguous team mapping",
    )

    results = {}
    now = datetime.now(timezone.utc).date()

    for row in rows:

        require(
            row["Div"].strip()
            == CSV_LEAGUES[league],
            f"{league}: unexpected division",
        )

        home = aliases.get(
            row["HomeTeam"].strip(),
            row["HomeTeam"].strip(),
        )

        away = aliases.get(
            row["AwayTeam"].strip(),
            row["AwayTeam"].strip(),
        )

        fixture = fixture_index.get(
            (home, away)
        )

        require(
            fixture is not None,
            f"{league}: unmatched fixture "
            f"{home} - {away}",
        )

        match_id = fixture["match_id"]

        require(
            match_id not in results,
            f"{league}: duplicate result "
            f"{match_id}",
        )

        result_date = datetime.strptime(
            row["Date"].strip(),
            "%d/%m/%Y",
        ).date()

        require(
            result_date
            == date.fromisoformat(
                fixture["date"]
            ),
            f"{league}: result/calendar "
            f"date mismatch: {match_id}",
        )

        require(
            result_date <= now,
            f"{league}: future result date",
        )

        home_score, away_score = (
            mapping.validate_score(
                row,
                league,
            )
        )

        results[match_id] = {
            "date": result_date.isoformat(),
            "home_score": home_score,
            "away_score": away_score,
        }

    return results


def load_all(source_dir=None):

    matches, previous_counts = v2.load_all()

    result = [
        dict(match)
        for match in matches
    ]

    for league in CSV_LEAGUES:

        fixtures = [
            match
            for match in result
            if match["competition"] == league
        ]

        csv_results = load_csv_results(
            league,
            fixtures,
            source_dir=source_dir,
        )

        for match in fixtures:

            entry = csv_results.get(
                match["match_id"]
            )

            if entry is None:

                # No CSV result does NOT imply
                # that the match is upcoming.

                match["state_status"] = (
                    "STATE_UNAVAILABLE"
                )

                match["result_source"] = None
                match["result_date"] = None

                continue

            match["state_status"] = (
                "RESULT_REPORTED"
            )

            match["finished"] = None

            match["home_score"] = (
                entry["home_score"]
            )

            match["away_score"] = (
                entry["away_score"]
            )

            match["result_date"] = (
                entry["date"]
            )

            match["result_source"] = (
                "football_data_local_csv"
            )

    # Other leagues: retain restored V2
    # statuses and identify their origin.

    for match in result:

        if match["competition"] in CSV_LEAGUES:
            continue

        match["result_date"] = None

        match["result_source"] = (
            "restored_league_states"
        )

    require(
        len(result) == 1752,
        "Unexpected match count",
    )

    identities = [
        (
            match["competition"],
            match["match_id"],
        )
        for match in result
    ]

    require(
        len(identities)
        == len(set(identities)),
        "Duplicate match identity",
    )

    counts = Counter(
        (
            match["competition"],
            match["state_status"],
        )
        for match in result
    )

    return result, counts


def main():

    print(
        "=== BUBUS EUROPE ADAPTER V3 ==="
    )

    print("MODE: READ ONLY / OFFLINE")

    matches, counts = load_all()

    statuses = (
        "FINISHED",
        "RESULT_REPORTED",
        "UPCOMING",
        "QUARANTINED",
        "STATE_UNAVAILABLE",
    )

    print("\n=== LEAGUE RESULTS ===")

    for league in (
        "EPL",
        "LaLiga",
        "SerieA",
        "Bundesliga",
        "Ligue1",
    ):

        print()
        print(league)

        for status in statuses:

            print(
                f"  {status:20s}",
                counts[(league, status)],
            )

    print("\n=== TOTALS ===")

    print("Matches:", len(matches))

    for status in statuses:

        print(
            status,
            sum(
                count
                for (league, current), count
                in counts.items()
                if current == status
            ),
        )

    print("\n=== RESULT COVERAGE ===")

    finished = sum(
        match["state_status"] == "FINISHED"
        for match in matches
    )

    reported = sum(
        match["state_status"]
        == "RESULT_REPORTED"
        for match in matches
    )

    print(
        "Restored finished:",
        finished,
    )

    print(
        "CSV results reported:",
        reported,
    )

    print(
        "Total results represented:",
        finished + reported,
    )

    print("\n=== FINAL STATUS ===")

    print("ARCHIVE INTEGRITY: PASSED")
    print("MATCH IDENTITIES: UNIQUE")
    print("RESULT MAPPING: PASSED")
    print("CSV RESULT FRESHNESS: NOT VERIFIED")
    print("INDEPENDENT RESULT VERIFICATION: PENDING")
    print("KICKOFF VERIFICATION: NOT PERFORMED")
    print("FORECAST RECORDING: BLOCKED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
