#!/usr/bin/env python3
"""Bubus Serie A Calendar V1.

Normalize official 2026/27 season.

Calendar and live match state are separate.

READ ONLY.
No archive writes.
No forecast recording.
"""

import hashlib
import json
import re

from collections import Counter
from datetime import datetime, timezone

from football_europe_seriea_season_audit_v1 import (
    fetch_season,
    SEASON_ID,
)


COMPETITION_ID = (
    "serie-a::Football_Competition::"
    "ec93b94f74294dc98ab5bcfd67fc0d88"
)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_id(value):
    return hashlib.sha256(
        canonical(value)
    ).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def normalize(data):

    competition = data["competition"]

    require(
        competition["seasonId"] == SEASON_ID,
        "Season mismatch",
    )

    require(
        competition["competitionId"] == COMPETITION_ID,
        "Competition mismatch",
    )

    raw_matches = data["matches"]

    require(
        len(raw_matches) == 380,
        "Expected 380 matches",
    )

    matches = []
    states = []

    teams = set()
    home_counts = Counter()
    away_counts = Counter()
    pairs = Counter()
    rounds = Counter()

    match_ids = set()
    provider_ids = set()

    for raw in raw_matches:

        match_id = raw["matchId"]
        provider_id = raw["providerId"]

        require(
            match_id not in match_ids,
            "Duplicate match ID",
        )

        require(
            provider_id not in provider_ids,
            "Duplicate provider ID",
        )

        match_ids.add(match_id)
        provider_ids.add(provider_id)

        require(
            raw["seasonId"] == SEASON_ID,
            "Match season mismatch",
        )

        home = raw["home"]
        away = raw["away"]

        home_id = home["teamId"]
        away_id = away["teamId"]

        require(
            home_id != away_id,
            "Self-match",
        )

        teams.update((home_id, away_id))

        home_counts[home_id] += 1
        away_counts[away_id] += 1

        pairs[(home_id, away_id)] += 1

        match_set = raw["matchSet"]

        round_name = match_set["name"]

        match_number = re.fullmatch(
            r"Matchday\s+(\d+)",
            round_name,
            re.IGNORECASE,
        )

        require(
            match_number is not None,
            "Unknown matchday format: " + round_name,
        )

        gameweek = int(
            match_number.group(1)
        )

        require(
            1 <= gameweek <= 38,
            "Invalid matchday number",
        )

        rounds[gameweek] += 1

        source_date = raw["matchDateUtc"]

        unknown = raw["isUnknownKickOffTime"]

        require(
            isinstance(unknown, bool),
            "Invalid unknown kickoff flag",
        )

        require(
            isinstance(source_date, str),
            "Invalid match date",
        )

        if unknown:

            # Unknown kickoff: date only.
            #
            # Example:
            # 2027-01-17Z
            #
            # Never interpret this as 00:00 UTC.

            require(
                re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}Z",
                    source_date,
                ) is not None,
                "Unexpected unknown kickoff format",
            )

            date = source_date[:10]

            kickoff = None

            schedule_quality = "TIME_UNCONFIRMED"

        else:

            parsed = datetime.fromisoformat(
                source_date.replace("Z", "+00:00")
            )

            require(
                parsed.tzinfo is not None,
                "Kickoff timezone missing",
            )

            kickoff = parsed.astimezone(
                timezone.utc
            ).isoformat()

            date = kickoff[:10]

            schedule_quality = "TIMED_UNVERIFIED"

        normalized_match = {
            "id": match_id,
            "provider_id": provider_id,
            "home_id": home_id,
            "home": (
                home.get("officialName")
                or home.get("shortName")
            ),
            "away_id": away_id,
            "away": (
                away.get("officialName")
                or away.get("shortName")
            ),
            "gameweek": gameweek,
            "date": date,
            "source_date_utc": source_date,
            "kickoff_utc": kickoff,
            "schedule_status": schedule_quality,
        }

        matches.append(normalized_match)

        states.append({
            "id": match_id,
            "status": raw.get("status"),
            "phase": raw.get("phase"),
            "home_score": raw.get("providerHomeScore"),
            "away_score": raw.get("providerAwayScore"),
        })

    require(
        len(teams) == 20,
        "Expected 20 teams",
    )

    require(
        len(pairs) == 380,
        "Expected 380 directed pairs",
    )

    require(
        all(v == 1 for v in pairs.values()),
        "Duplicate directed pairing",
    )

    for team_id in teams:

        require(
            home_counts[team_id] == 19,
            "Incorrect home count",
        )

        require(
            away_counts[team_id] == 19,
            "Incorrect away count",
        )

    require(
        set(rounds) == set(range(1, 39)),
        "Missing matchdays",
    )

    require(
        all(v == 10 for v in rounds.values()),
        "Invalid matchday distribution",
    )

    matches.sort(
        key=lambda m: m["id"]
    )

    states.sort(
        key=lambda m: m["id"]
    )

    content = {
        "schema": "bubus-seriea-calendar-v1",
        "competition": "IT1",
        "season": "2026-27",
        "season_id": SEASON_ID,
        "competition_id": COMPETITION_ID,
        "matches": matches,
    }

    return content, states


def main():

    print("=== BUBUS SERIE A CALENDAR V1 ===")
    print("MODE: READ ONLY")

    data = fetch_season()

    content, states = normalize(data)

    matches = content["matches"]

    unknown = [
        m for m in matches
        if m["schedule_status"] == "TIME_UNCONFIRMED"
    ]

    timed = [
        m for m in matches
        if m["schedule_status"] == "TIMED_UNVERIFIED"
    ]

    print("\n=== CALENDAR ===")

    print("Matches:", len(matches))
    print("Timed:", len(timed))
    print("Unknown kickoff:", len(unknown))

    print("\n=== CONTENT ID ===")

    print(content_id(content))

    print("\n=== FIRST 5 MATCHES ===")

    for match in sorted(
        matches,
        key=lambda m: (
            m["date"],
            m["id"],
        ),
    )[:5]:

        print(
            match["date"],
            "|",
            match["home"],
            "—",
            match["away"],
            "|",
            match["schedule_status"],
        )

    print("\n=== UNKNOWN KICKOFF SAMPLE ===")

    for match in unknown[:3]:

        print(
            match["home"],
            "—",
            match["away"],
        )

        print(
            "Date:",
            match["date"],
        )

        print(
            "Kickoff:",
            match["kickoff_utc"],
        )

    print("\n=== STATES ===")

    print("State records:", len(states))

    print("\n=== FINAL STATUS ===")
    print("STRUCTURE: VALID")
    print("CALENDAR AND STATES: SEPARATED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
