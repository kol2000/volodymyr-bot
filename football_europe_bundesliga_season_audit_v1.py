#!/usr/bin/env python3
"""Bubus Bundesliga Season Audit V1.

Structural audit of OpenLigaDB 2026/27 Bundesliga.

READ ONLY.
No archive writes.
No forecast recording.
"""

import json
import urllib.request

from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


URL = (
    "https://api.openligadb.de/"
    "getmatchdata/bl1/2026"
)

MAX_RESPONSE = 5_000_000

BERLIN = ZoneInfo("Europe/Berlin")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fetch_season():

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Bubus-Football-Monitor/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        require(
            response.status == 200,
            "Unexpected HTTP status",
        )

        ctype = response.headers.get(
            "Content-Type", ""
        )

        require(
            "json" in ctype.lower(),
            "Unexpected content type",
        )

        raw = response.read(
            MAX_RESPONSE + 1
        )

    require(
        len(raw) <= MAX_RESPONSE,
        "Response too large",
    )

    data = json.loads(raw)

    require(
        isinstance(data, list),
        "Expected JSON list",
    )

    return data


def main():

    print("=== BUBUS BUNDESLIGA SEASON AUDIT V1 ===")
    print("MODE: READ ONLY")

    matches = fetch_season()

    print("\n=== SEASON STRUCTURE ===")

    require(
        len(matches) == 306,
        "Expected 306 matches",
    )

    match_ids = set()
    teams = {}
    home_counts = Counter()
    away_counts = Counter()
    rounds = defaultdict(list)
    pairs = Counter()

    finished = 0
    upcoming = 0

    dates_missing = 0
    invalid_dates = 0

    timezone_mismatches = 0

    future_times = defaultdict(Counter)
    future_dates = defaultdict(set)

    result_kinds = Counter()
    result_combinations = Counter()

    score_samples = []
    unknown_timezones = Counter()

    for match in matches:

        require(
            isinstance(match, dict),
            "Invalid match object",
        )

        require(
            match.get("leagueShortcut") == "bl1",
            "Unexpected league shortcut",
        )

        require(
            str(match.get("leagueSeason")) == "2026",
            "Unexpected season",
        )

        match_id = match["matchID"]

        require(
            isinstance(match_id, int),
            "Invalid match ID",
        )

        require(
            match_id not in match_ids,
            "Duplicate match ID",
        )

        match_ids.add(match_id)

        home = match["team1"]
        away = match["team2"]

        home_id = home["teamId"]
        away_id = away["teamId"]

        require(
            home_id != away_id,
            "Team plays itself",
        )

        for team_id, team in (
            (home_id, home),
            (away_id, away),
        ):

            name = team["teamName"]

            require(
                isinstance(name, str) and name.strip(),
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

        group = match["group"]
        round_number = group["groupOrderID"]

        require(
            isinstance(round_number, int),
            "Invalid matchday",
        )

        rounds[round_number].append(
            (home_id, away_id)
        )

        is_finished = match["matchIsFinished"]

        require(
            type(is_finished) is bool,
            "Invalid finished flag",
        )

        if is_finished:
            finished += 1
        else:
            upcoming += 1

        raw_utc = match.get(
            "matchDateTimeUTC"
        )

        raw_local = match.get(
            "matchDateTime"
        )

        if not raw_utc or not raw_local:

            dates_missing += 1
            continue

        try:

            utc_time = datetime.fromisoformat(
                raw_utc.replace("Z", "+00:00")
            )

            local_time = datetime.fromisoformat(
                raw_local
            )

            require(
                utc_time.tzinfo is not None,
                "UTC datetime has no timezone",
            )

            converted = utc_time.astimezone(
                BERLIN
            )

            if (
                converted.replace(tzinfo=None)
                != local_time.replace(tzinfo=None)
            ):
                timezone_mismatches += 1

        except (ValueError, TypeError):

            invalid_dates += 1
            continue

        tz = match.get("timeZoneID")

        unknown_timezones[str(tz)] += 1

        if not is_finished:

            local_label = converted.strftime(
                "%H:%M"
            )

            future_times[round_number][
                local_label
            ] += 1

            future_dates[round_number].add(
                converted.date().isoformat()
            )

        results = match.get(
            "matchResults"
        ) or []

        kinds = []

        for result in results:

            kind = result.get(
                "resultTypeKind"
            )

            result_kinds[str(kind)] += 1

            kinds.append(str(kind))

        result_combinations[
            tuple(sorted(kinds))
        ] += 1

        if (
            is_finished
            and results
            and len(score_samples) < 3
        ):

            score_samples.append({
                "match_id": match_id,
                "home": home["teamName"],
                "away": away["teamName"],
                "results": [
                    {
                        "kind": r.get(
                            "resultTypeKind"
                        ),
                        "home": r.get(
                            "pointsTeam1"
                        ),
                        "away": r.get(
                            "pointsTeam2"
                        ),
                    }
                    for r in results
                ],
            })

    print("Matches:", len(matches))
    print("Unique IDs:", len(match_ids))
    print("Teams:", len(teams))
    print("Matchdays:", len(rounds))
    print("Finished:", finished)
    print("Not finished:", upcoming)

    print("\n=== STRUCTURAL VALIDATION ===")

    require(
        len(teams) == 18,
        "Expected 18 teams",
    )

    require(
        set(rounds) == set(range(1, 35)),
        "Matchday set mismatch",
    )

    for round_number in range(1, 35):

        games = rounds[round_number]

        require(
            len(games) == 9,
            f"Matchday {round_number}: expected 9 games",
        )

        participants = []

        for home_id, away_id in games:
            participants.extend(
                [home_id, away_id]
            )

        require(
            len(set(participants)) == 18,
            f"Matchday {round_number}: repeated team",
        )

    for team_id in teams:

        require(
            home_counts[team_id] == 17,
            "Invalid home game count: "
            + teams[team_id],
        )

        require(
            away_counts[team_id] == 17,
            "Invalid away game count: "
            + teams[team_id],
        )

    require(
        len(pairs) == 306,
        "Invalid directed fixture count",
    )

    require(
        all(count == 1 for count in pairs.values()),
        "Repeated home-away fixture",
    )

    print("18 teams: OK")
    print("34 matchdays: OK")
    print("9 matches per round: OK")
    print("17 home + 17 away: OK")
    print("Double round robin: OK")
    print("Unique match IDs: OK")

    print("\n=== DATES AND TIMEZONES ===")

    print("Missing dates:", dates_missing)
    print("Invalid dates:", invalid_dates)

    print(
        "Local/UTC mismatches:",
        timezone_mismatches,
    )

    print(
        "timeZoneID values:",
        dict(unknown_timezones),
    )

    require(
        dates_missing == 0,
        "Missing match dates",
    )

    require(
        invalid_dates == 0,
        "Invalid match dates",
    )

    require(
        timezone_mismatches == 0,
        "UTC/local date mismatch",
    )

    print("\n=== FUTURE MATCH TIMES BY ROUND ===")

    for round_number in sorted(future_times):

        times = future_times[round_number]

        print(
            f"Round {round_number:02d}",
            "dates=" + ",".join(
                sorted(future_dates[round_number])
            ),
            "times=" + str(dict(sorted(
                times.items()
            ))),
        )

    print("\n=== RESULT TYPES ===")

    for kind, count in result_kinds.most_common():
        print(kind, count)

    print("\n=== RESULT COMBINATIONS ===")

    for combination, count in (
        result_combinations.most_common()
    ):

        print(
            str(combination),
            count,
        )

    print("\n=== FINISHED MATCH SAMPLES ===")

    for item in score_samples:

        print(
            json.dumps(
                item,
                ensure_ascii=False,
            )
        )

    print("\n=== FINAL STATUS ===")
    print("STRUCTURAL AUDIT PASSED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
