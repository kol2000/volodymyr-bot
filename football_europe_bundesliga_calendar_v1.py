#!/usr/bin/env python3
"""Bubus Bundesliga Calendar V1.

Normalize OpenLigaDB Bundesliga 2026/27.

Calendar and live match states are separated.

Unverified future kickoffs are never considered
officially confirmed.

READ ONLY.
No archive writes.
No forecast recording.
"""

import hashlib
import json

from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from football_europe_bundesliga_season_audit_v1 import (
    fetch_season,
)


BERLIN = ZoneInfo("Europe/Berlin")

SEASON = "2026-27"
COMPETITION = "DE1"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


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


def parse_dates(match):

    utc_raw = match["matchDateTimeUTC"]
    local_raw = match["matchDateTime"]

    require(
        isinstance(utc_raw, str),
        "Invalid UTC datetime",
    )

    require(
        isinstance(local_raw, str),
        "Invalid local datetime",
    )

    utc = datetime.fromisoformat(
        utc_raw.replace("Z", "+00:00")
    )

    local = datetime.fromisoformat(
        local_raw
    )

    require(
        utc.tzinfo is not None,
        "UTC datetime has no timezone",
    )

    require(
        local.tzinfo is None,
        "Expected naive local datetime",
    )

    utc = utc.astimezone(timezone.utc)

    berlin = utc.astimezone(BERLIN)

    require(
        berlin.replace(tzinfo=None) == local,
        "UTC/local datetime mismatch",
    )

    return utc, berlin


def normalize(data):

    require(
        isinstance(data, list),
        "Expected match list",
    )

    require(
        len(data) == 306,
        "Expected 306 matches",
    )

    matches = []
    states = []

    match_ids = set()
    teams = {}

    home_counts = Counter()
    away_counts = Counter()
    rounds = Counter()
    pairs = Counter()

    for raw in data:

        require(
            raw.get("leagueShortcut") == "bl1",
            "Unexpected competition",
        )

        require(
            str(raw.get("leagueSeason")) == "2026",
            "Unexpected season",
        )

        match_id = raw["matchID"]

        require(
            type(match_id) is int,
            "Invalid match ID",
        )

        require(
            match_id not in match_ids,
            "Duplicate match ID",
        )

        match_ids.add(match_id)

        home = raw["team1"]
        away = raw["team2"]

        home_id = home["teamId"]
        away_id = away["teamId"]

        require(
            type(home_id) is int
            and type(away_id) is int,
            "Invalid team ID",
        )

        require(
            home_id != away_id,
            "Same home and away team",
        )

        for team_id, team in (
            (home_id, home),
            (away_id, away),
        ):

            name = team["teamName"]

            require(
                isinstance(name, str)
                and bool(name.strip()),
                "Invalid team name",
            )

            if team_id in teams:

                require(
                    teams[team_id] == name,
                    "Inconsistent team name",
                )

            teams[team_id] = name

        home_counts[home_id] += 1
        away_counts[away_id] += 1

        pairs[(home_id, away_id)] += 1

        round_number = raw["group"]["groupOrderID"]

        require(
            type(round_number) is int
            and 1 <= round_number <= 34,
            "Invalid matchday",
        )

        rounds[round_number] += 1

        utc, local = parse_dates(raw)

        finished = raw["matchIsFinished"]

        require(
            type(finished) is bool,
            "Invalid finished flag",
        )

        # Until independently checked against DFL,
        # no future kickoff is officially confirmed.

        if finished:

            schedule_status = "TIMED_HISTORICAL"

            kickoff_utc = utc.isoformat()

        elif round_number >= 19:

            schedule_status = "TIME_UNCONFIRMED"

            kickoff_utc = None

        else:

            schedule_status = "TIMED_UNVERIFIED"

            kickoff_utc = utc.isoformat()

        # Keep the OpenLigaDB value separately,
        # including provisional kickoff times.

        candidate_kickoff_utc = utc.isoformat()

        identifier = str(match_id)

        matches.append({
            "id": identifier,
            "home_id": home_id,
            "home": home["teamName"],
            "away_id": away_id,
            "away": away["teamName"],
            "gameweek": round_number,
            "date": local.date().isoformat(),
            "source_date_utc": raw["matchDateTimeUTC"],
            "candidate_kickoff_utc": candidate_kickoff_utc,
            "kickoff_utc": kickoff_utc,
            "schedule_status": schedule_status,
        })

        results = raw.get("matchResults") or []

        require(
            isinstance(results, list),
            "Invalid results",
        )

        final_results = [
            r for r in results
            if r.get("resultTypeKind")
            == "After90Minutes"
        ]

        require(
            len(final_results) <= 1,
            "Duplicate final result",
        )

        final = (
            final_results[0]
            if final_results
            else None
        )

        if finished:

            require(
                final is not None,
                "Finished match without final result",
            )

            status = "FINISHED"

        elif final is not None:

            status = "NOT_FINISHED_WITH_RESULT"

        else:

            status = "NOT_FINISHED"

        # Never treat a score from an unfinished
        # match as a confirmed final score.

        final_home = None
        final_away = None

        reported_home = None
        reported_away = None

        if final is not None:

            reported_home = final["pointsTeam1"]
            reported_away = final["pointsTeam2"]

            require(
                type(reported_home) is int
                and type(reported_away) is int
                and reported_home >= 0
                and reported_away >= 0,
                "Invalid score",
            )

            if finished:

                final_home = reported_home
                final_away = reported_away

        states.append({
            "id": identifier,
            "status": status,
            "finished": finished,
            "home_score": final_home,
            "away_score": final_away,
            "reported_home_score": reported_home,
            "reported_away_score": reported_away,
        })

    require(
        len(teams) == 18,
        "Expected 18 teams",
    )

    require(
        set(rounds) == set(range(1, 35)),
        "Invalid matchday set",
    )

    require(
        all(rounds[r] == 9 for r in range(1, 35)),
        "Invalid matches per matchday",
    )

    require(
        all(home_counts[t] == 17 for t in teams),
        "Invalid home fixture count",
    )

    require(
        all(away_counts[t] == 17 for t in teams),
        "Invalid away fixture count",
    )

    require(
        len(pairs) == 306
        and all(v == 1 for v in pairs.values()),
        "Invalid home-away fixture pairs",
    )

    content = {
        "schema": "bubus-bundesliga-calendar-v1",
        "competition": COMPETITION,
        "season": SEASON,
        "source": "OpenLigaDB",
        "matches": sorted(
            matches,
            key=lambda m: m["id"],
        ),
    }

    states.sort(
        key=lambda item: item["id"]
    )

    return content, states


def main():

    print("=== BUBUS BUNDESLIGA CALENDAR V1 ===")
    print("MODE: READ ONLY")

    data = fetch_season()

    content, states = normalize(data)

    print("\n=== CALENDAR ===")

    print(
        "Matches:",
        len(content["matches"]),
    )

    schedule_counts = Counter(
        m["schedule_status"]
        for m in content["matches"]
    )

    for name, count in sorted(
        schedule_counts.items()
    ):

        print(name + ":", count)

    print("\n=== CONTENT ID ===")

    print(content_id(content))

    print("\n=== STATES ===")

    status_counts = Counter(
        s["status"]
        for s in states
    )

    for name, count in sorted(
        status_counts.items()
    ):

        print(name + ":", count)

    print("\n=== FIRST 5 MATCHES ===")

    ordered = sorted(
        content["matches"],
        key=lambda m: (
            m["date"],
            m["id"],
        ),
    )

    for match in ordered[:5]:

        print(
            match["date"],
            "|",
            match["home"],
            "—",
            match["away"],
            "|",
            match["schedule_status"],
        )

    print("\n=== UNCONFIRMED SAMPLE ===")

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

        print(
            "Date:",
            match["date"],
        )

        print(
            "Kickoff:",
            match["kickoff_utc"],
        )

        print(
            "Candidate:",
            match["candidate_kickoff_utc"],
        )

    print("\n=== FINAL STATUS ===")

    print("STRUCTURE: VALID")
    print("CALENDAR AND STATES: SEPARATED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
