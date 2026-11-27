#!/usr/bin/env python3
"""Bubus Europe Adapter V4.

Add reconciled LaLiga provider results to V3.

STRICTLY READ ONLY / OFFLINE.

No network.
No database writes.
No forecast generation.
No Telegram publication.
"""

from collections import Counter

import football_europe_adapter_v3 as v3
import football_europe_laliga_scores_audit_v1 as audit


EXPECTED_TOTAL = 1752


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_all(source_dir=None):

    matches, _ = v3.load_all(source_dir=source_dir)

    provider, records, source_sha = (
        audit.load_provider()
    )

    result = [
        dict(match)
        for match in matches
    ]

    laliga = {
        match["match_id"]: match
        for match in result
        if match["competition"] == "LaLiga"
    }

    require(
        len(laliga) == 380,
        "LaLiga match count mismatch",
    )

    require(
        set(records) == set(laliga),
        "Provider/calendar IDs mismatch",
    )

    corroborated = 0
    provider_only = 0

    for match_id, match in laliga.items():

        score = provider.get(match_id)

        if match["state_status"] == "RESULT_REPORTED":

            require(
                score is not None,
                "CSV result missing in provider: "
                + match_id,
            )

            csv_score = (
                match["home_score"],
                match["away_score"],
            )

            require(
                csv_score == score,
                "Result conflict: "
                + match_id,
            )

            match["state_status"] = (
                "RESULT_CORROBORATED"
            )

            match["result_source"] = (
                "football_data_csv+laliga_provider"
            )

            match["provider_source_sha256"] = (
                source_sha
            )

            corroborated += 1

        elif score is not None:

            require(
                match["state_status"]
                == "STATE_UNAVAILABLE",
                "Unexpected previous result state",
            )

            match["state_status"] = (
                "RESULT_PROVIDER_ONLY"
            )

            match["finished"] = None

            match["home_score"] = score[0]
            match["away_score"] = score[1]

            match["result_source"] = (
                "laliga_provider"
            )

            match["provider_source_sha256"] = (
                source_sha
            )

            provider_only += 1

    require(
        corroborated + provider_only
        == len(provider),
        "Provider result coverage mismatch",
    )

    require(
        len(result) == EXPECTED_TOTAL,
        "Unexpected European match count",
    )

    identities = [
        (
            match["competition"],
            match["match_id"],
        )
        for match in result
    ]

    require(
        len(identities) == len(set(identities)),
        "Duplicate match identities",
    )

    counts = Counter(
        (
            match["competition"],
            match["state_status"],
        )
        for match in result
    )

    return result, counts


def main():

    print("=== BUBUS EUROPE ADAPTER V4 ===")
    print("MODE: READ ONLY / OFFLINE")

    matches, counts = load_all()

    statuses = (
        "FINISHED",
        "RESULT_CORROBORATED",
        "RESULT_PROVIDER_ONLY",
        "RESULT_REPORTED",
        "UPCOMING",
        "QUARANTINED",
        "STATE_UNAVAILABLE",
    )

    print("\n=== CHAMPIONSHIPS ===")

    for league in (
        "EPL",
        "LaLiga",
        "SerieA",
        "Bundesliga",
        "Ligue1",
    ):

        print()
        print(league)

        for status in statuses:

            count = counts[(league, status)]

            if count:
                print(
                    f"  {status:22s}",
                    count,
                )

    print("\n=== TOTALS ===")

    print("Matches:", len(matches))

    for status in statuses:

        total = sum(
            count
            for (league, current), count
            in counts.items()
            if current == status
        )

        print(
            f"{status:22s}",
            total,
        )

    with_scores = sum(
        match["home_score"] is not None
        and match["away_score"] is not None
        for match in matches
    )

    print("\nMatches with accepted scores:", with_scores)

    print("\n=== FINAL STATUS ===")

    print("CALENDAR INTEGRITY: PASSED")
    print("LALIGA RESULT RECONCILIATION: PASSED")
    print("MATCH IDENTITIES: UNIQUE")
    print("KICKOFF VERIFICATION: NOT PERFORMED")
    print("FORECAST RECORDING: BLOCKED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
