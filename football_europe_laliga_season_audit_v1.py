#!/usr/bin/env python3
"""Bubus LaLiga full-season audit V1.

READ ONLY.
Fetch all season fixtures and validate structure.
No archive writes. No forecast recording.
"""

import json
import urllib.request

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlencode

from football_europe_laliga_api_audit_v1 import (
    BASE_URL,
    get_key,
)

EXPECTED_TOTAL = 380
EXPECTED_SEASON = 102
EXPECTED_COMPETITION = 1

PAGE_SIZE = 100
MAX_PAGES = 20
MAX_RESPONSE = 2_000_000


def fetch_page(key, offset):

    params = {
        "subscription": "laliga-easports-2026",
        "competition": "primera-division",
        "limit": PAGE_SIZE,
        "offset": offset,
    }

    url = BASE_URL + "?" + urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://www.laliga.com",
            "Referer": (
                "https://www.laliga.com/"
                "en-GB/laliga-easports/calendar"
            ),
            "Ocp-Apim-Subscription-Key": key,
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        if response.status != 200:
            raise RuntimeError("Unexpected HTTP status")

        ctype = response.headers.get("Content-Type", "")

        if "json" not in ctype.lower():
            raise RuntimeError("Unexpected content type")

        raw = response.read(MAX_RESPONSE + 1)

    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("API response too large")

    result = json.loads(raw)

    if not isinstance(result, dict):
        raise RuntimeError("Invalid API response")

    if not isinstance(result.get("matches"), list):
        raise RuntimeError("Missing matches list")

    return result


def main():

    print("=== BUBUS LALIGA FULL SEASON AUDIT V1 ===")
    print("MODE: READ ONLY")

    key = get_key()

    print("API key: PRESENT (hidden)")

    all_matches = []
    expected_total = None
    offset = 0

    print("\n=== PAGINATION ===")

    for page_number in range(1, MAX_PAGES + 1):

        data = fetch_page(key, offset)

        total = data.get("total")
        matches = data["matches"]

        if type(total) is not int:
            raise RuntimeError("Invalid total")

        if expected_total is None:
            expected_total = total

            if expected_total != EXPECTED_TOTAL:
                raise RuntimeError(
                    f"Expected 380 matches, API reports {total}"
                )

        elif total != expected_total:
            raise RuntimeError("Total changed between pages")

        print(
            f"Page {page_number}: "
            f"offset={offset}, "
            f"received={len(matches)}, "
            f"total={total}"
        )

        if not matches:
            raise RuntimeError("Empty page before completion")

        all_matches.extend(matches)
        offset += len(matches)

        if offset > expected_total:
            raise RuntimeError("Received too many matches")

        if offset == expected_total:
            break

    else:
        raise RuntimeError("Pagination page limit exceeded")

    print("\n=== DATA VALIDATION ===")

    ids = set()
    home_count = Counter()
    away_count = Counter()
    pair_count = Counter()
    weeks = Counter()
    statuses = Counter()
    seasons = Counter()
    competitions = Counter()

    midnight_dates = []
    other_dates = []
    problems = []

    for match in all_matches:

        if not isinstance(match, dict):
            problems.append("Non-dict match")
            continue

        match_id = match.get("id")

        if match_id is None:
            problems.append("Missing match ID")
            continue

        if match_id in ids:
            problems.append(f"Duplicate match ID: {match_id}")

        ids.add(match_id)

        season = match.get("season") or {}
        competition = match.get("competition") or {}

        if not isinstance(season, dict):
            problems.append(f"Invalid season: {match_id}")
            continue

        if not isinstance(competition, dict):
            problems.append(f"Invalid competition: {match_id}")
            continue

        season_id = season.get("id")
        competition_id = competition.get("id")

        seasons[season_id] += 1
        competitions[competition_id] += 1

        if season_id != EXPECTED_SEASON:
            problems.append(
                f"Wrong season: {match_id}: {season_id}"
            )

        if competition_id != EXPECTED_COMPETITION:
            problems.append(
                f"Wrong competition: {match_id}: "
                f"{competition_id}"
            )

        home = match.get("home_team") or {}
        away = match.get("away_team") or {}

        if not isinstance(home, dict) or not isinstance(away, dict):
            problems.append(f"Invalid teams: {match_id}")
            continue

        h = home.get("id")
        a = away.get("id")

        if h is None or a is None or h == a:
            problems.append(f"Invalid team IDs: {match_id}")
            continue

        home_count[h] += 1
        away_count[a] += 1

        pair_count[(h, a)] += 1

        week = match.get("gameweek") or {}

        if not isinstance(week, dict):
            problems.append(f"Invalid gameweek: {match_id}")
            continue

        week_name = week.get("shortname")
        weeks[week_name] += 1

        statuses[match.get("status")] += 1

        date_text = match.get("date")

        try:
            stamp = datetime.fromisoformat(date_text)

            if stamp.tzinfo is None:
                raise ValueError("Timezone missing")

            utc = stamp.astimezone(timezone.utc)

        except (TypeError, ValueError):
            problems.append(f"Invalid date: {match_id}")
            continue

        info = (
            match_id,
            home.get("nickname") or home.get("name"),
            away.get("nickname") or away.get("name"),
            date_text,
            week_name,
        )

        if (
            utc.hour == 0
            and utc.minute == 0
            and utc.second == 0
            and utc.microsecond == 0
        ):
            midnight_dates.append(info)
        else:
            other_dates.append(info)

    teams = sorted(set(home_count) | set(away_count))

    if len(all_matches) != EXPECTED_TOTAL:
        problems.append("Wrong match count")

    if len(ids) != EXPECTED_TOTAL:
        problems.append("Wrong unique ID count")

    if len(teams) != 20:
        problems.append(f"Expected 20 teams, got {len(teams)}")

    if len(pair_count) != EXPECTED_TOTAL:
        problems.append("Duplicate directed team pairs")

    for team in teams:

        if home_count[team] != 19:
            problems.append(
                f"Team {team}: home={home_count[team]}"
            )

        if away_count[team] != 19:
            problems.append(
                f"Team {team}: away={away_count[team]}"
            )

    for h in teams:
        for a in teams:
            if h == a:
                continue

            if pair_count[(h, a)] != 1:
                problems.append(
                    f"Invalid directed pair: {h} vs {a}"
                )

    print("Total matches:", len(all_matches))
    print("Unique match IDs:", len(ids))
    print("Teams:", len(teams))
    print("Directed pairs:", len(pair_count))

    print("\n=== SEASONS ===")
    print(dict(seasons))

    print("\n=== COMPETITIONS ===")
    print(dict(competitions))

    print("\n=== GAMEWEEKS ===")
    print("Unique gameweeks:", len(weeks))

    for week, count in sorted(weeks.items()):
        print(week, count)

    if len(weeks) != 38:
        problems.append(
            f"Expected 38 gameweeks, got {len(weeks)}"
        )

    for week, count in weeks.items():
        if count != 10:
            problems.append(
                f"Gameweek {week}: expected 10, got {count}"
            )

    print("\n=== MATCH STATUSES ===")

    for status, count in statuses.most_common():
        print(status, count)

    print("\n=== DATE QUALITY ===")

    print("Midnight UTC:", len(midnight_dates))
    print("Other times:", len(other_dates))

    print("\n=== SAMPLE MIDNIGHT FIXTURES ===")

    for item in midnight_dates[:10]:
        print(*item, sep=" | ")

    print("\n=== SAMPLE NON-MIDNIGHT FIXTURES ===")

    for item in other_dates[:15]:
        print(*item, sep=" | ")

    print("\n=== TEAM COUNTS ===")

    for team in teams:
        print(
            f"Team ID {team:>4}: "
            f"home={home_count[team]:>2}, "
            f"away={away_count[team]:>2}"
        )

    print("\n=== FINAL STATUS ===")

    if problems:

        for problem in problems[:50]:
            print("ERROR:", problem)

        print("STATUS: STRUCTURE INVALID")
        print("FORECAST RECORDING: BLOCKED")
        raise SystemExit(2)

    print("STATUS: STRUCTURE VALID")
    print("KICKOFF TIMES: NOT VERIFIED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
