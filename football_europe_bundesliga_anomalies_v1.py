#!/usr/bin/env python3
"""Bubus Bundesliga Anomalies V1.

Investigate OpenLigaDB result/status differences
and provisional kickoff times.

READ ONLY.
"""

from collections import Counter, defaultdict

import football_europe_bundesliga_season_audit_v1 as season


def show_match(m):
    home = m["team1"]["teamName"]
    away = m["team2"]["teamName"]

    print(
        f"ID={m['matchID']} | "
        f"Round={m['group']['groupOrderID']} | "
        f"{home} - {away}"
    )

    print(
        "  Finished:",
        m["matchIsFinished"]
    )

    print(
        "  Date UTC:",
        m.get("matchDateTimeUTC")
    )

    print(
        "  Local:",
        m.get("matchDateTime")
    )

    print(
        "  Timezone:",
        m.get("timeZoneID")
    )

    print(
        "  Updated:",
        m.get("lastUpdateDateTime")
    )

    for r in m.get("matchResults") or []:

        print(
            "  Result:",
            r.get("resultTypeKind"),
            r.get("pointsTeam1"),
            "-",
            r.get("pointsTeam2"),
        )


def main():

    print("=== BUBUS BUNDESLIGA ANOMALIES V1 ===")
    print("MODE: READ ONLY")

    matches = season.fetch_season()

    print("\n=== RESULTS WITHOUT FINISHED FLAG ===")

    anomalies = []

    for m in matches:

        results = m.get("matchResults") or []

        if results and not m["matchIsFinished"]:
            anomalies.append(m)

    print("Matches:", len(anomalies))

    for m in anomalies:
        show_match(m)

    print("\n=== FINISHED WITHOUT FINAL RESULT ===")

    anomalies = []

    for m in matches:

        if not m["matchIsFinished"]:
            continue

        final = [
            r for r in m.get("matchResults") or []
            if r.get("resultTypeKind")
            == "After90Minutes"
        ]

        if len(final) != 1:
            anomalies.append(m)

    print("Matches:", len(anomalies))

    for m in anomalies[:10]:
        show_match(m)

    print("\n=== TIMEZONE COVERAGE BY ROUND ===")

    timezone_by_round = defaultdict(Counter)

    for m in matches:

        round_number = m["group"]["groupOrderID"]

        tz = str(m.get("timeZoneID"))

        timezone_by_round[round_number][tz] += 1

    for round_number in sorted(timezone_by_round):

        print(
            f"Round {round_number:02d}:",
            dict(timezone_by_round[round_number]),
        )

    print("\n=== RESULTS IN ROUNDS 19-34 ===")

    future_results = []

    for m in matches:

        round_number = m["group"]["groupOrderID"]

        if (
            round_number >= 19
            and m.get("matchResults")
        ):
            future_results.append(m)

    print("Matches:", len(future_results))

    for m in future_results[:10]:
        show_match(m)

    print("\n=== ROUND 19 SAMPLE ===")

    for m in matches:

        if m["group"]["groupOrderID"] == 19:
            show_match(m)

    print("\n=== ROUND 34 SAMPLE ===")

    for m in matches:

        if m["group"]["groupOrderID"] == 34:
            show_match(m)

    print("\n=== FINAL STATUS ===")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
