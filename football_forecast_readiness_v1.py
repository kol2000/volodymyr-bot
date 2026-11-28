#!/usr/bin/env python3
"""Bubus Forecast V8 league readiness audit.

Offline, read-only preliminary league eligibility.

READY_CANDIDATE does NOT authorize production
forecasting. Source freshness and result availability
require separate verification.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo
from collections import Counter

import football_forecast_data_live_v1 as loader
import football_europe_ligue1_monitor_v2 as ligue_monitor


LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

ACCEPTED = {
    "FINISHED",
    "RESULT_REPORTED",
    "RESULT_CORROBORATED",
    "RESULT_PROVIDER_ONLY",
}

MOSCOW = ZoneInfo("Europe/Moscow")


def parse_date(value):
    if value is None:
        return None

    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def get_ligue_dates():

    _, _, provider, _ = (
        ligue_monitor.load_previous_observation()
    )

    result = {}

    for row in provider["matches"]:

        match_id = str(row["match_id"])

        if match_id in result:
            raise RuntimeError(
                f"Duplicate Ligue1 ID: {match_id}"
            )

        result[match_id] = parse_date(
            row.get("date")
        )

    if len(result) != 306:
        raise RuntimeError(
            "Unexpected Ligue1 provider coverage"
        )

    return result


def evaluate(as_of=None):

    if as_of is None:
        as_of = datetime.now(MOSCOW).date()

    if not isinstance(as_of, date) or isinstance(
        as_of, datetime
    ):
        raise TypeError("as_of must be a date")

    season = loader.load_current_matches()

    matches = season["matches"]

    ligue_dates = get_ligue_dates()

    report = {}

    for league in LEAGUES:

        rows = [
            m for m in matches
            if m["competition"] == league
        ]

        if not rows:
            raise RuntimeError(
                f"No calendar for {league}"
            )

        unresolved = []
        quarantined = []
        undated = []

        states = Counter()

        for match in rows:

            state = match["state_status"]
            states[state] += 1

            match_id = str(match["match_id"])

            if league == "Ligue1":

                if match_id not in ligue_dates:
                    raise RuntimeError(
                        "Ligue1 provider ID missing: "
                        + match_id
                    )

                day = ligue_dates[match_id]

            else:
                day = parse_date(
                    match.get("date")
                )

            if day is None:
                undated.append(match)
                continue

            # Matches dated today are not assessed here:
            # without verified kickoff/end timestamps
            # their result may legitimately be pending.
            if day >= as_of:
                continue

            if state == "QUARANTINED":
                quarantined.append(match)

            elif state not in ACCEPTED:
                unresolved.append(match)

        blockers = (
            len(unresolved)
            + len(quarantined)
            + len(undated)
        )

        report[league] = {
            "calendar": len(rows),
            "accepted": sum(
                states[s] for s in ACCEPTED
            ),
            "unresolved": unresolved,
            "quarantined": quarantined,
            "undated": undated,
            "states": dict(states),
            "status": (
                "READY_CANDIDATE"
                if blockers == 0
                else "BLOCKED"
            ),
        }

    return {
        "as_of": as_of,
        "snapshot_id": season["snapshot_id"],
        "snapshot_created_utc": (
            season["snapshot_created_utc"]
        ),
        "leagues": report,
    }


def main():

    print("=== BUBUS V8 LEAGUE READINESS V1 ===")
    print("MODE: STRICTLY READ ONLY / OFFLINE")

    result = evaluate()

    print("\nAs of:", result["as_of"])
    print("Snapshot:", result["snapshot_id"])

    for league, info in result["leagues"].items():

        print("\n" + "=" * 60)
        print(league)
        print("=" * 60)

        print("Calendar:", info["calendar"])
        print("Accepted results:", info["accepted"])
        print(
            "Unresolved past:",
            len(info["unresolved"])
        )
        print(
            "Quarantined past:",
            len(info["quarantined"])
        )
        print(
            "Undated:",
            len(info["undated"])
        )

        print("STATUS:", info["status"])

        for category in (
            "unresolved",
            "quarantined",
            "undated",
        ):

            for match in info[category]:

                print(
                    " REVIEW:",
                    category,
                    match.get("date"),
                    match.get("home"),
                    "—",
                    match.get("away"),
                    "|",
                    match["state_status"],
                )

    print("\n=== SUMMARY ===")

    counts = Counter(
        info["status"]
        for info in result["leagues"].values()
    )

    print("Ready candidates:",
          counts["READY_CANDIDATE"])

    print("Blocked:", counts["BLOCKED"])

    print("\n=== SAFETY ===")
    print("PRODUCTION AUTHORIZATION: NOT GRANTED")
    print("SOURCE FRESHNESS: NOT VERIFIED")
    print("RESULT AVAILABILITY: NOT VERIFIED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
