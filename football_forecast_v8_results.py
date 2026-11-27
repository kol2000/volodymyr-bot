#!/usr/bin/env python3

"""Bubus V8 result collector — read-only validation."""

import json
import urllib.request

from datetime import datetime, timezone
from pathlib import Path


URL = "https://api.openligadb.de/getmatchdata/bl1/2026"

JOURNAL = Path(
    "state/football_v8_shadow/observations.jsonl"
)

MATCH_MAPPING = {
    "s365:2485946": 83253,
    "s365:2485943": 83250,
    "s365:2485941": 83251,
    "s365:2485939": 83252,
    "s365:2485944": 83254,
    "s365:2485945": 83247,
    "s365:2485940": 83249,
    "s365:2485942": 83248,
    "s365:2485938": 83246,
}


EXPECTED_TEAMS = {
    "s365:2485938": (
        "Боруссия Дортмунд", "Байер 04",
        "Borussia Dortmund", "Bayer 04 Leverkusen",
    ),
    "s365:2485939": (
        "Кёльн", "Эльферсберг",
        "1. FC Köln", "SV 07 Elversberg",
    ),
    "s365:2485940": (
        "Фрайбург", "РБ Лейпциг",
        "SC Freiburg", "RB Leipzig",
    ),
    "s365:2485941": (
        "Гамбург", "Бавария Мюнхен",
        "Hamburger SV", "FC Bayern München",
    ),
    "s365:2485942": (
        "Хоффенхайм", "Аугсбург",
        "TSG Hoffenheim", "FC Augsburg",
    ),
    "s365:2485943": (
        "Майнц 05", "Унион Берлин",
        "1. FSV Mainz 05", "1. FC Union Berlin",
    ),
    "s365:2485944": (
        "Падерборн 07", "Шальке 04",
        "SC Paderborn 07", "FC Schalke 04",
    ),
    "s365:2485945": (
        "Штутгарт", "Айнтрахт Франкфурт",
        "VfB Stuttgart", "Eintracht Frankfurt",
    ),
    "s365:2485946": (
        "Вердер Бремен", "Боруссия Мёнхенгладбах",
        "SV Werder Bremen", "Borussia Mönchengladbach",
    ),
}


def fetch_matches():

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Bubus-V8-Research/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        return json.load(response)


def get_final_score(match):

    if match.get("matchIsFinished") is not True:
        return None

    results = match.get("matchResults") or []

    final = [
        r for r in results
        if r.get("resultTypeID") == 2
    ]

    if len(final) != 1:
        return None

    result = final[0]

    home = result.get("pointsTeam1")
    away = result.get("pointsTeam2")

    if not (
        type(home) is int
        and type(away) is int
        and 0 <= home <= 30
        and 0 <= away <= 30
    ):
        return None

    return home, away


def main():

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()


    print("=== BUBUS V8 RESULT COLLECTOR ===")
    print("MODE: READ ONLY")

    now = datetime.now(timezone.utc)

    observations = [
        json.loads(line)
        for line in JOURNAL.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    v2 = [
        r for r in observations
        if r.get("schema")
        == "bubus_v8_observation_v2"
    ]

    print("Observations V2:", len(v2))

    unique_matches = sorted({
        r["match_id"] for r in v2
    })

    print("Unique matches:", len(unique_matches))

    data = fetch_matches()

    matches = {}

    for match in data:

        match_id = match.get("matchID")

        if match_id in matches:
            raise RuntimeError(
                f"Duplicate OpenLigaDB ID: {match_id}"
            )

        matches[match_id] = match

    print("OpenLigaDB matches:", len(matches))

    pending = 0
    finished = 0
    blocked = 0
    verified_results = []


    for match_id in unique_matches:

        print("\n" + "-" * 55)
        print("Match:", match_id)

        provider_id = MATCH_MAPPING.get(match_id)

        if provider_id is None:
            print("STATUS: UNMAPPED")
            blocked += 1
            continue

        match = matches.get(provider_id)

        if match is None:
            print("STATUS: PROVIDER_MATCH_MISSING")
            blocked += 1
            continue

        home = (match.get("team1") or {}).get(
            "teamName"
        )

        away = (match.get("team2") or {}).get(
            "teamName"
        )

        print("Provider ID:", provider_id)
        print("Teams:", home, "—", away)

        candidates = [
            row for row in v2
            if row["match_id"] == match_id
        ]

        expected = EXPECTED_TEAMS.get(match_id)

        if expected is None:
            print("STATUS: TEAM_MAPPING_MISSING")
            blocked += 1
            continue

        expected_home, expected_away, provider_home, provider_away = expected

        if (home, away) != (provider_home, provider_away):
            print("STATUS: PROVIDER_TEAMS_MISMATCH")
            blocked += 1
            continue

        if any(
            (row.get("home"), row.get("away"))
            != (expected_home, expected_away)
            for row in candidates
        ):
            print("STATUS: OBSERVATION_TEAMS_MISMATCH")
            blocked += 1
            continue

        print("Team identities: VERIFIED")

        dates = {
            row.get("kickoff_utc")
            for row in candidates
        }

        if len(dates) != 1:
            print("STATUS: KICKOFF_CONFLICT")
            blocked += 1
            continue

        observed_kickoff = datetime.fromisoformat(
            next(iter(dates)).replace("Z", "+00:00")
        )

        provider_kickoff = datetime.fromisoformat(
            match["matchDateTimeUTC"].replace(
                "Z", "+00:00"
            )
        )

        if (
            observed_kickoff.tzinfo is None
            or provider_kickoff.tzinfo is None
        ):
            print("STATUS: INVALID_TIMEZONE")
            blocked += 1
            continue

        difference = abs(
            (
                observed_kickoff
                - provider_kickoff
            ).total_seconds()
        )

        print("Kickoff difference:", difference, "sec")

        if difference > 60:
            print("STATUS: KICKOFF_MISMATCH")
            blocked += 1
            continue

        if now <= provider_kickoff:
            print("STATUS: UPCOMING")
            pending += 1
            continue

        if match.get("matchIsFinished") is not True:
            print("STATUS: NOT_FINISHED")
            pending += 1
            continue

        score = get_final_score(match)

        if score is None:
            print("STATUS: FINAL_SCORE_UNVERIFIED")
            blocked += 1
            continue

        home_goals, away_goals = score

        if home_goals > away_goals:
            outcome = "HOME"
        elif home_goals < away_goals:
            outcome = "AWAY"
        else:
            outcome = "DRAW"

        print("STATUS: VERIFIED_FINISHED")
        print("Score:", home_goals, ":", away_goals)
        print("Outcome:", outcome)

        verified_results.append({
            "schema": "bubus_v8_result_v1",
            "match_id": match_id,
            "provider": "OpenLigaDB",
            "provider_id": provider_id,
            "home": home,
            "away": away,
            "kickoff_utc": provider_kickoff.isoformat(),
            "home_goals": home_goals,
            "away_goals": away_goals,
            "outcome": outcome,
            "collected_utc": now.isoformat(),
        })

        finished += 1

    print("\n=== RESULT SUMMARY ===")

    print("Pending:", pending)
    print("Verified finished:", finished)
    print("Blocked:", blocked)

    if args.write:
        from football_forecast_v8_result_store import save_results

        added, skipped = save_results(verified_results)

        print("\n=== STORAGE ===")
        print("New results:", added)
        print("Already stored:", skipped)
    else:
        print("\nNo journal writes.")
    print("No Telegram messages.")
    print("No forecast modifications.")


if __name__ == "__main__":
    main()
