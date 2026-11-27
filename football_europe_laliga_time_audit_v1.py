#!/usr/bin/env python3
"""Bubus LaLiga Time Quality Audit V1.

Read-only analysis of match dates and statuses.
No files modified. No forecast recording.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone

from football_europe_laliga_api_audit_v1 import get_key
from football_europe_laliga_season_audit_v1 import fetch_page


def main():

    print("=== BUBUS LALIGA TIME QUALITY AUDIT ===")
    print("MODE: READ ONLY")

    key = get_key()

    matches = []
    total = None

    for offset in (0, 100, 200, 300):

        response = fetch_page(key, offset)

        if total is None:
            total = response["total"]

        if response["total"] != total:
            raise RuntimeError(
                "API total changed during pagination"
            )

        matches.extend(response["matches"])

    if total != 380 or len(matches) != 380:
        raise RuntimeError(
            "Incomplete season data"
        )

    ids = [m["id"] for m in matches]

    if len(set(ids)) != 380:
        raise RuntimeError(
            "Duplicate match IDs"
        )

    now = datetime.now(timezone.utc)

    combinations = Counter()
    weeks = defaultdict(Counter)

    upcoming_timed = []
    upcoming_midnight = []
    anomalies = []

    for match in matches:

        match_id = match["id"]
        status = match["status"]

        stamp = datetime.fromisoformat(
            match["date"]
        )

        if stamp.tzinfo is None:
            raise RuntimeError(
                f"Timezone missing for {match_id}"
            )

        stamp = stamp.astimezone(timezone.utc)

        midnight = (
            stamp.hour == 0
            and stamp.minute == 0
            and stamp.second == 0
            and stamp.microsecond == 0
        )

        time_class = (
            "MIDNIGHT"
            if midnight
            else "NON_MIDNIGHT"
        )

        period = (
            "PAST"
            if stamp < now
            else "FUTURE"
        )

        combinations[
            (status, time_class, period)
        ] += 1

        week = match["gameweek"]["shortname"]

        weeks[week][time_class] += 1

        home = (
            match["home_team"].get("nickname")
            or match["home_team"]["name"]
        )

        away = (
            match["away_team"].get("nickname")
            or match["away_team"]["name"]
        )

        info = {
            "id": match_id,
            "date": stamp.isoformat(),
            "home": home,
            "away": away,
            "status": status,
            "week": week,
        }

        if status == "PreMatch":

            if midnight:
                upcoming_midnight.append(info)
            else:
                upcoming_timed.append(info)

            if stamp < now:
                anomalies.append(info)

        if status in ("FullTime", "FirstHalf") and midnight:
            anomalies.append(info)

    print("\n=== STATUS / TIME / PERIOD ===")

    for key, count in sorted(
        combinations.items()
    ):
        print(
            f"{key[0]:12} "
            f"{key[1]:15} "
            f"{key[2]:8} "
            f"{count:3}"
        )

    print("\n=== TIME QUALITY BY GAMEWEEK ===")
    print("WEEK  MIDNIGHT  NON_MIDNIGHT")

    for week in sorted(
        weeks,
        key=lambda w: int(w[1:])
    ):

        print(
            f"{week:5} "
            f"{weeks[week]['MIDNIGHT']:8} "
            f"{weeks[week]['NON_MIDNIGHT']:13}"
        )

    print("\n=== UPCOMING WITH NON-MIDNIGHT TIME ===")

    upcoming_timed.sort(
        key=lambda m: m["date"]
    )

    for item in upcoming_timed[:20]:

        print(
            item["date"],
            "|",
            item["home"],
            "—",
            item["away"],
            "|",
            item["week"]
        )

    print("\n=== UPCOMING WITH MIDNIGHT TIME ===")

    upcoming_midnight.sort(
        key=lambda m: m["date"]
    )

    for item in upcoming_midnight[:15]:

        print(
            item["date"],
            "|",
            item["home"],
            "—",
            item["away"],
            "|",
            item["week"]
        )

    print("\n=== ANOMALIES FOR REVIEW ===")

    print("Count:", len(anomalies))

    for item in anomalies[:20]:
        print(item)

    print("\n=== SUMMARY ===")

    print(
        "PreMatch with non-midnight time:",
        len(upcoming_timed)
    )

    print(
        "PreMatch with midnight time:",
        len(upcoming_midnight)
    )

    print("Total matches:", len(matches))
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
