#!/usr/bin/env python3
"""Bubus Serie A Season Audit V1.

Validate official 2026/27 season structure.

READ ONLY.
No archive writes.
No forecast recording.
"""

import json
import urllib.request

from collections import Counter
from urllib.parse import quote

from football_europe_seriea_api_probe_v1 import (
    BASE,
    SEASON_ID,
    MAX_RESPONSE,
)


def fetch_season():

    url = (
        BASE
        + "/seasons/"
        + quote(SEASON_ID, safe="")
        + "/matches?locale=en-GB"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        if response.status != 200:
            raise RuntimeError(
                "Unexpected HTTP status"
            )

        content_type = response.headers.get(
            "Content-Type",
            "",
        )

        if "json" not in content_type.lower():
            raise RuntimeError(
                "Unexpected content type"
            )

        raw = response.read(
            MAX_RESPONSE + 1
        )

    if len(raw) > MAX_RESPONSE:
        raise RuntimeError(
            "API response exceeds limit"
        )

    return json.loads(raw)


def require(condition, message):

    if not condition:
        raise RuntimeError(message)


def main():

    print("=== BUBUS SERIE A SEASON AUDIT V1 ===")
    print("MODE: READ ONLY")

    data = fetch_season()

    require(
        isinstance(data, dict),
        "Invalid API root",
    )

    competition = data.get("competition")

    require(
        isinstance(competition, dict),
        "Missing competition information",
    )

    matches = data.get("matches")

    require(
        isinstance(matches, list),
        "Missing matches array",
    )

    print("\n=== COMPETITION ===")

    for field in (
        "name",
        "officialName",
        "seasonName",
        "seasonId",
        "competitionId",
        "providerId",
        "startDateUtc",
        "endDateUtc",
    ):

        print(
            field + ":",
            competition.get(field),
        )

    print("\n=== MATCH COUNT ===")

    print("Total matches:", len(matches))

    require(
        len(matches) == 380,
        "Expected exactly 380 matches",
    )

    require(
        competition.get("seasonId") == SEASON_ID,
        "Competition season ID mismatch",
    )

    match_ids = []
    provider_ids = []

    teams = {}
    home_counts = Counter()
    away_counts = Counter()
    pairs = Counter()

    rounds = Counter()
    round_names = {}

    statuses = Counter()
    phases = Counter()
    schedule_statuses = Counter()

    unknown_kickoff = Counter()
    missing_dates = 0

    unknown_samples = []
    timed_samples = []

    for index, match in enumerate(matches, 1):

        require(
            isinstance(match, dict),
            f"Invalid match #{index}",
        )

        require(
            match.get("seasonId") == SEASON_ID,
            f"Season mismatch at match #{index}",
        )

        match_id = match.get("matchId")
        provider_id = match.get("providerId")

        require(
            isinstance(match_id, str) and match_id,
            f"Missing matchId at #{index}",
        )

        require(
            isinstance(provider_id, str) and provider_id,
            f"Missing providerId at #{index}",
        )

        match_ids.append(match_id)
        provider_ids.append(provider_id)

        home = match.get("home")
        away = match.get("away")

        require(
            isinstance(home, dict)
            and isinstance(away, dict),
            f"Missing teams at #{index}",
        )

        home_id = home.get("teamId")
        away_id = away.get("teamId")

        require(
            isinstance(home_id, str) and home_id,
            f"Missing home team ID at #{index}",
        )

        require(
            isinstance(away_id, str) and away_id,
            f"Missing away team ID at #{index}",
        )

        require(
            home_id != away_id,
            f"Self-match at #{index}",
        )

        teams[home_id] = (
            home.get("officialName")
            or home.get("shortName")
            or home_id
        )

        teams[away_id] = (
            away.get("officialName")
            or away.get("shortName")
            or away_id
        )

        home_counts[home_id] += 1
        away_counts[away_id] += 1

        pairs[(home_id, away_id)] += 1

        match_set = match.get("matchSet")

        require(
            isinstance(match_set, dict),
            f"Missing matchSet at #{index}",
        )

        round_id = match_set.get("matchSetId")

        require(
            isinstance(round_id, str) and round_id,
            f"Missing matchSetId at #{index}",
        )

        rounds[round_id] += 1

        round_names[round_id] = {
            "index": match_set.get("index"),
            "name": match_set.get("name"),
            "shortName": match_set.get("shortName"),
        }

        statuses[str(match.get("status"))] += 1

        phases[str(match.get("phase"))] += 1

        schedule_statuses[
            str(match.get("scheduleStatus"))
        ] += 1

        unknown = match.get(
            "isUnknownKickOffTime"
        )

        require(
            isinstance(unknown, bool),
            f"Invalid kickoff flag at #{index}",
        )

        unknown_kickoff[unknown] += 1

        date = match.get("matchDateUtc")

        if not date:
            missing_dates += 1

        sample = {
            "id": match_id,
            "home": teams[home_id],
            "away": teams[away_id],
            "date": date,
            "status": match.get("status"),
            "schedule": match.get("scheduleStatus"),
        }

        if unknown and len(unknown_samples) < 5:
            unknown_samples.append(sample)

        if (
            not unknown
            and match.get("status") == "UPCOMING"
            and len(timed_samples) < 5
        ):
            timed_samples.append(sample)

    print("\n=== UNIQUE IDENTIFIERS ===")

    print("matchId:", len(set(match_ids)))
    print("providerId:", len(set(provider_ids)))

    require(
        len(set(match_ids)) == 380,
        "Duplicate matchId",
    )

    require(
        len(set(provider_ids)) == 380,
        "Duplicate providerId",
    )

    print("\n=== TEAMS ===")

    print("Unique teams:", len(teams))

    require(
        len(teams) == 20,
        "Expected 20 teams",
    )

    for team_id in sorted(
        teams,
        key=lambda t: str(teams[t])
    ):

        home = home_counts[team_id]
        away = away_counts[team_id]

        print(
            f"{str(teams[team_id]):25}"
            f" Home: {home:2}"
            f" Away: {away:2}"
            f" Total: {home + away:2}"
        )

        require(
            home == 19 and away == 19,
            "Invalid home/away distribution: "
            + str(teams[team_id]),
        )

    print("\n=== MATCH PAIRS ===")

    print("Unique directed pairs:", len(pairs))

    require(
        len(pairs) == 380,
        "Duplicate home/away pairing",
    )

    require(
        all(count == 1 for count in pairs.values()),
        "Repeated directed pairing",
    )

    print("\n=== MATCHDAYS ===")

    print("Unique match sets:", len(rounds))

    for round_id in sorted(
        rounds,
        key=lambda r: (
            str(round_names[r]["index"]).zfill(4),
            r,
        ),
    ):

        info = round_names[round_id]

        print(
            "Index:",
            info["index"],
            "| Name:",
            info["name"],
            "| Short:",
            info["shortName"],
            "| Matches:",
            rounds[round_id],
        )

    print("\n=== MATCH STATUSES ===")

    for name, count in statuses.most_common():
        print(name, count)

    print("\n=== PHASES ===")

    for name, count in phases.most_common():
        print(name, count)

    print("\n=== SCHEDULE STATUSES ===")

    for name, count in schedule_statuses.most_common():
        print(name, count)

    print("\n=== KICKOFF QUALITY ===")

    print(
        "Known kickoff:",
        unknown_kickoff[False],
    )

    print(
        "Unknown kickoff:",
        unknown_kickoff[True],
    )

    print("Missing dates:", missing_dates)

    print("\n=== UNKNOWN KICKOFF SAMPLES ===")

    for sample in unknown_samples:

        print(
            json.dumps(
                sample,
                ensure_ascii=False,
            )
        )

    print("\n=== UPCOMING TIMED SAMPLES ===")

    for sample in timed_samples:

        print(
            json.dumps(
                sample,
                ensure_ascii=False,
            )
        )

    print("\n=== FINAL VALIDATION ===")

    print("Matches:", len(matches))
    print("Teams:", len(teams))
    print("Match sets:", len(rounds))

    require(
        len(rounds) == 38,
        "Expected 38 matchdays",
    )

    require(
        all(count == 10 for count in rounds.values()),
        "Expected 10 matches per matchday",
    )

    require(
        missing_dates == 0,
        "Missing match dates",
    )

    print("\nSTATUS: STRUCTURAL AUDIT PASSED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
