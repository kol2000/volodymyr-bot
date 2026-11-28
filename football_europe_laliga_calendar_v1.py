#!/usr/bin/env python3
"""Bubus LaLiga Calendar V1.

Official API -> normalized season calendar.

READ ONLY.
No archive writes.
No forecast recording.
"""

import hashlib
import json

from collections import Counter
from datetime import datetime, timezone

from football_europe_laliga_api_audit_v1 import get_key
from football_europe_laliga_season_audit_v1 import fetch_page


EXPECTED_TOTAL = 380
EXPECTED_SEASON = 102
EXPECTED_COMPETITION = 1


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def download_season():

    key = get_key()

    matches = []
    total = None

    for offset in (0, 100, 200, 300):

        response = fetch_page(key, offset)

        if total is None:
            total = response["total"]

        if total != EXPECTED_TOTAL:
            raise RuntimeError("Unexpected match total")

        if response["total"] != total:
            raise RuntimeError("Pagination total changed")

        page = response["matches"]

        expected_count = min(100, total - offset)

        if len(page) != expected_count:
            raise RuntimeError(
                f"Unexpected page size at offset {offset}"
            )

        matches.extend(page)

    if len(matches) != EXPECTED_TOTAL:
        raise RuntimeError("Incomplete calendar")

    ids = [m["id"] for m in matches]

    if len(set(ids)) != EXPECTED_TOTAL:
        raise RuntimeError("Duplicate match IDs")

    return matches


def normalize(matches):

    calendar = []
    status_records = []

    home_count = Counter()
    away_count = Counter()
    pair_count = Counter()
    week_count = Counter()

    now = datetime.now(timezone.utc)

    for match in matches:

        match_id = match["id"]

        if match["season"]["id"] != EXPECTED_SEASON:
            raise RuntimeError(
                f"Wrong season for match {match_id}"
            )

        if match["competition"]["id"] != EXPECTED_COMPETITION:
            raise RuntimeError(
                f"Wrong competition for match {match_id}"
            )

        home = match["home_team"]
        away = match["away_team"]

        home_id = home["id"]
        away_id = away["id"]

        if home_id == away_id:
            raise RuntimeError("Team playing itself")

        gameweek = match["gameweek"]["shortname"]

        if not (
            gameweek.startswith("J")
            and gameweek[1:].isdigit()
            and 1 <= int(gameweek[1:]) <= 38
        ):
            raise RuntimeError(
                f"Invalid gameweek: {gameweek}"
            )

        stamp = datetime.fromisoformat(
            match["date"]
        )

        if stamp.tzinfo is None:
            raise RuntimeError(
                f"Timezone missing: {match_id}"
            )

        stamp = stamp.astimezone(timezone.utc)

        midnight = (
            stamp.hour == 0
            and stamp.minute == 0
            and stamp.second == 0
            and stamp.microsecond == 0
        )

        match_status = match["status"]

        # Midnight on future PreMatch fixtures is treated
        # as a provisional calendar date, not a kickoff.

        placeholder = (
            match_status == "PreMatch"
            and midnight
            and stamp > now
        )

        if placeholder:

            kickoff_utc = None
            schedule_status = "DATE_ONLY"

        else:

            kickoff_utc = stamp.isoformat()
            schedule_status = "TIMED_UNVERIFIED"

        record = {
            "id": match_id,

            "home_id": home_id,
            "home": (
                home.get("nickname")
                or home["name"]
            ),

            "away_id": away_id,
            "away": (
                away.get("nickname")
                or away["name"]
            ),

            "gameweek": int(gameweek[1:]),

            "date": stamp.date().isoformat(),

            "kickoff_utc": kickoff_utc,

            "schedule_status": schedule_status,
        }

        calendar.append(record)

        status_records.append({
            "id": match_id,
            "match_status": match_status,
        })

        home_count[home_id] += 1
        away_count[away_id] += 1

        pair_count[(home_id, away_id)] += 1
        week_count[int(gameweek[1:])] += 1

    teams = set(home_count) | set(away_count)

    if len(teams) != 20:
        raise RuntimeError("Expected 20 teams")

    if len(pair_count) != 380:
        raise RuntimeError("Invalid directed pair count")

    for team in teams:

        if home_count[team] != 19:
            raise RuntimeError(
                f"Invalid home matches for {team}"
            )

        if away_count[team] != 19:
            raise RuntimeError(
                f"Invalid away matches for {team}"
            )

    for home in teams:
        for away in teams:

            if home == away:
                continue

            if pair_count[(home, away)] != 1:
                raise RuntimeError(
                    f"Invalid directed pair {home}-{away}"
                )

    for week in range(1, 39):

        if week_count[week] != 10:
            raise RuntimeError(
                f"Invalid gameweek J{week}"
            )

    calendar.sort(key=lambda x: x["id"])
    status_records.sort(key=lambda x: x["id"])

    return calendar, status_records


def build():

    matches = download_season()

    calendar, statuses = normalize(matches)

    content = {
        "schema": "bubus-laliga-calendar-v1",
        "competition": "SP1",
        "competition_id": EXPECTED_COMPETITION,
        "season": "2026-27",
        "season_id": EXPECTED_SEASON,
        "matches": calendar,
    }

    return content, statuses


def main():

    print("=== BUBUS LALIGA CALENDAR V1 ===")
    print("MODE: READ ONLY")

    content, statuses = build()

    content_id = sha256(canonical(content))

    print("\n=== CALENDAR ===")

    print("Matches:", len(content["matches"]))
    print("Content ID:", content_id)

    schedule_counts = Counter(
        m["schedule_status"]
        for m in content["matches"]
    )

    print("\n=== SCHEDULE QUALITY ===")

    for status, count in sorted(
        schedule_counts.items()
    ):
        print(status, count)

    match_counts = Counter(
        m["match_status"]
        for m in statuses
    )

    print("\n=== MATCH STATUS (SEPARATE) ===")

    for status, count in sorted(
        match_counts.items()
    ):
        print(status, count)

    print("\n=== UPCOMING TIMED MATCHES ===")

    status_by_id = {
        item["id"]: item["match_status"]
        for item in statuses
    }

    now = datetime.now(timezone.utc)

    upcoming = []

    for match in content["matches"]:

        if match["kickoff_utc"] is None:
            continue

        kickoff = datetime.fromisoformat(
            match["kickoff_utc"]
        )

        if (
            kickoff > now
            and status_by_id[match["id"]] == "PreMatch"
        ):
            upcoming.append(match)

    upcoming.sort(
        key=lambda m: m["kickoff_utc"]
    )

    for match in upcoming[:20]:

        print(
            match["kickoff_utc"],
            "|",
            match["home"],
            "—",
            match["away"],
            "| J" + str(match["gameweek"]),
            "| ID", match["id"],
        )

    print("\n=== FINAL STATUS ===")

    print("STRUCTURE: VALID")
    print("CONTENT ID: GENERATED")
    print("MATCH STATUSES: SEPARATED")
    print("KICKOFF VERIFICATION: NOT AUTHORIZED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
