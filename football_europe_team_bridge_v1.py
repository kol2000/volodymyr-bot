#!/usr/bin/env python3
"""Bubus Europe Team Bridge V1.

Connect canonical European calendar team names
to Football-Data CSV names and Forecast Live V7 IDs.

STRICTLY READ ONLY / OFFLINE.

No fuzzy matching.
No database writes.
No forecasts.
No Telegram publication.
"""

import csv
import json
from pathlib import Path

import football_europe_adapter_v1 as adapter
import football_europe_results_mapping_audit_v1 as existing


ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "state/football_live/source"

MAPPING = (
    ROOT
    / "state/football_live/team_mapping.json"
)

LEAGUES = {
    "EPL": 20,
    "LaLiga": 20,
    "SerieA": 20,
    "Bundesliga": 18,
    "Ligue1": 18,
}

CODES = {
    "EPL": "E0",
    "LaLiga": "SP1",
    "SerieA": "I1",
    "Bundesliga": "D1",
    "Ligue1": "F1",
}


# CSV name -> canonical calendar name.
#
# EPL and LaLiga use previously audited aliases.
# Other leagues use explicit mappings from
# the source audit.
#
# No automatic name similarity matching.

ALIASES = {
    "EPL": existing.ALIASES["EPL"],

    "LaLiga": existing.ALIASES["LaLiga"],

    "SerieA": {
        "Inter": "Internazionale",
    },

    "Bundesliga": {
        "Augsburg": "FC Augsburg",
        "Bayern Munich": "FC Bayern München",
        "Dortmund": "Borussia Dortmund",
        "Ein Frankfurt": "Eintracht Frankfurt",
        "Elversberg": "SV 07 Elversberg",
        "FC Koln": "1. FC Köln",
        "Freiburg": "SC Freiburg",
        "Hamburg": "Hamburger SV",
        "Hoffenheim": "TSG Hoffenheim",
        "Leverkusen": "Bayer 04 Leverkusen",
        "M'gladbach": "Borussia Mönchengladbach",
        "Mainz": "1. FSV Mainz 05",
        "Paderborn": "SC Paderborn 07",
        "Schalke 04": "FC Schalke 04",
        "Stuttgart": "VfB Stuttgart",
        "Union Berlin": "1. FC Union Berlin",
        "Werder Bremen": "SV Werder Bremen",
    },

    "Ligue1": {
        "Angers": "angers sco",
        "Auxerre": "aj auxerre",
        "Brest": "stade brestois 29",
        "Le Havre": "le havre",
        "Le Mans": "le mans fc",
        "Lens": "rc lens",
        "Lille": "losc",
        "Lorient": "fc lorient",
        "Lyon": "olympique lyonnais",
        "Marseille": "olympique de marseille",
        "Monaco": "as monaco",
        "Nice": "ogc nice",
        "Paris FC": "paris fc",
        "Paris SG": "paris saint-germain",
        "Rennes": "stade rennais fc",
        "Strasbourg": "rc strasbourg alsace",
        "Toulouse": "toulouse fc",
        "Troyes": "estac troyes",
    },
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_csv_teams(league):

    path = SOURCE / f"{league}_2026-27.csv"

    require(
        path.is_file(),
        f"{league}: missing CSV",
    )

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        require(
            reader.fieldnames is not None,
            f"{league}: missing CSV header",
        )

        require(
            {"Div", "HomeTeam", "AwayTeam"}
            .issubset(reader.fieldnames),
            f"{league}: invalid CSV schema",
        )

        rows = list(reader)

    require(
        bool(rows),
        f"{league}: empty CSV",
    )

    for row in rows:

        require(
            row["Div"].strip() == CODES[league],
            f"{league}: incorrect division",
        )

    return {
        name.strip()
        for row in rows
        for name in (
            row["HomeTeam"],
            row["AwayTeam"],
        )
    }


def load_bridge():

    mapping = json.loads(
        MAPPING.read_text(encoding="utf-8")
    )

    bridge = {}

    all_ids = set()

    for league, expected in LEAGUES.items():

        archive, manifest, calendar = (
            adapter.load_archive(league)
        )

        calendar_teams = {
            name
            for match in calendar["matches"]
            for name in (
                match["home"],
                match["away"],
            )
        }

        csv_teams = load_csv_teams(league)

        require(
            len(calendar_teams) == expected,
            f"{league}: calendar team count mismatch",
        )

        require(
            len(csv_teams) == expected,
            f"{league}: CSV team count mismatch",
        )

        aliases = ALIASES[league]

        require(
            set(aliases).issubset(csv_teams),
            f"{league}: unused or unknown CSV alias",
        )

        normalized = {
            csv_name: aliases.get(
                csv_name,
                csv_name,
            )
            for csv_name in csv_teams
        }

        require(
            set(normalized.values())
            == calendar_teams,
            f"{league}: calendar mapping mismatch",
        )

        require(
            len(set(normalized.values()))
            == len(normalized),
            f"{league}: ambiguous team mapping",
        )

        verified = mapping[league]["verified"]

        new_teams = set(
            mapping[league][
                "no_top_division_history"
            ]
        )

        require(
            not (set(verified) & new_teams),
            f"{league}: conflicting team identity",
        )

        require(
            set(verified) | new_teams
            == csv_teams,
            f"{league}: V7 identity coverage mismatch",
        )

        result = {}

        for csv_name, calendar_name in normalized.items():

            is_new = csv_name in new_teams

            if is_new:

                historical_id = (
                    "__new_2627__" + csv_name
                )

            else:

                historical_id = (
                    verified[csv_name]
                )

            require(
                isinstance(historical_id, str)
                and bool(historical_id.strip()),
                f"{league}: invalid historical ID",
            )

            identity_key = (
                league,
                historical_id,
            )

            require(
                identity_key not in all_ids,
                f"{league}: duplicate historical ID "
                + historical_id,
            )

            all_ids.add(identity_key)

            result[calendar_name] = {
                "calendar_name": calendar_name,
                "csv_name": csv_name,
                "historical_id": historical_id,
                "new_team": is_new,
            }

        require(
            len(result) == expected,
            f"{league}: bridge count mismatch",
        )

        bridge[league] = result

    require(
        sum(len(x) for x in bridge.values())
        == 96,
        "Global team bridge count mismatch",
    )

    return bridge


def main():

    print("=== BUBUS EUROPE TEAM BRIDGE V1 ===")
    print("MODE: READ ONLY / OFFLINE")

    bridge = load_bridge()

    total = 0
    historical = 0
    new = 0

    for league, teams in bridge.items():

        print()
        print("=" * 65)
        print("LEAGUE:", league)
        print("=" * 65)

        league_new = 0

        for calendar_name, info in sorted(
            teams.items()
        ):

            if info["new_team"]:
                status = "NEW"
                league_new += 1
                new += 1
            else:
                status = "HISTORY"
                historical += 1

            print(
                f"{calendar_name:30s}"
                f" => {info['csv_name']:18s}"
                f" => {info['historical_id']}"
                f" [{status}]"
            )

        total += len(teams)

        print()
        print("Teams:", len(teams))
        print("Historical:", len(teams) - league_new)
        print("New:", league_new)
        print("Mapping: PASSED")

    require(
        total == 96,
        "Incorrect total team count",
    )

    require(
        historical == 86 and new == 10,
        "Unexpected history/new team counts",
    )

    print("\n=== TOTALS ===")

    print("Teams:", total)
    print("With historical IDs:", historical)
    print("New teams:", new)

    print("\n=== FINAL STATUS ===")

    print("TEAM BRIDGE: PASSED")
    print("CSV IDENTITIES: VERIFIED")
    print("HISTORICAL IDS: UNIQUE")
    print("NO FUZZY MATCHING")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
