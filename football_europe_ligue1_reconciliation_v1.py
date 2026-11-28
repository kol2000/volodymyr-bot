#!/usr/bin/env python3
"""Bubus Ligue 1 Calendar Reconciliation V1.

Compare official LFP 2026/27 calendar with
Open Matchday.

Two explicitly documented exceptions:
- Round 1: Rennes hosts PSG (independently confirmed).
- Round 23: PSG hosts Rennes (provider only, provisional).

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import csv
import io

from collections import Counter
from datetime import datetime

import football_europe_ligue1_lfp_validation_v1 as lfp
import football_europe_ligue1_season_audit_v1 as season
import football_europe_ligue1_sources_v1 as sources


PSG = "paris saint-germain"
RENNES = "stade rennais fc"


# (round, official home, official away)
# ->
# (provider home, provider away)

EXCEPTIONS = {
    (1, PSG, RENNES): (
        RENNES, PSG
    ),
    (23, RENNES, PSG): (
        PSG, RENNES
    ),
}


def require(condition, message):

    if not condition:
        raise RuntimeError(message)


def key(item):

    return (
        item["gameweek"],
        item["home_key"],
        item["away_key"],
    )


def load_official():

    text, size = lfp.download_pdf()

    matches, groups, problems = (
        lfp.parse_official_calendar(text)
    )

    require(
        not problems,
        "Official PDF parsing problems",
    )

    require(
        len(matches) == 306,
        "Expected 306 official matches",
    )

    keys = [key(m) for m in matches]

    require(
        len(set(keys)) == 306,
        "Duplicate official matches",
    )

    return matches


def load_provider():

    matches = lfp.load_provider_calendar()

    require(
        len(matches) == 306,
        "Expected 306 provider matches",
    )

    keys = [key(m) for m in matches]

    require(
        len(set(keys)) == 306,
        "Duplicate provider matches",
    )

    return matches


def check_football_data(first_round):

    print("\n=== INDEPENDENT RESULT CHECK ===")

    url = sources.SOURCES[
        "FOOTBALL_DATA_RESULTS"
    ]

    status, content_type, raw = (
        sources.download(url)
    )

    require(
        status == 200,
        "Football-Data HTTP error",
    )

    reader = csv.DictReader(
        io.StringIO(
            raw.decode("utf-8-sig")
        )
    )

    candidates = []

    for row in reader:

        home = row["HomeTeam"].strip().casefold()
        away = row["AwayTeam"].strip().casefold()

        if (
            home == "rennes"
            and away in {"paris sg", "psg", "paris saint-germain"}
        ):

            candidates.append(row)

    require(
        len(candidates) == 1,
        "Expected one completed Rennes-PSG match",
    )

    row = candidates[0]

    require(
        datetime.strptime(
            row["Date"],
            "%d/%m/%Y",
        ).date().isoformat()
        == first_round["date"],
        "Independent date mismatch",
    )

    require(
        int(row["FTHG"])
        == first_round["home_score"],
        "Independent home score mismatch",
    )

    require(
        int(row["FTAG"])
        == first_round["away_score"],
        "Independent away score mismatch",
    )

    require(
        first_round["home_key"] == RENNES
        and first_round["away_key"] == PSG
        and first_round["finished"],
        "Provider first-round result invalid",
    )

    print(
        "Rennes - PSG:",
        row["FTHG"],
        ":",
        row["FTAG"],
    )

    print(
        "Date:",
        row["Date"],
    )

    print(
        "Independent result: VERIFIED"
    )


def main():

    print(
        "=== BUBUS LIGUE 1 RECONCILIATION V1 ==="
    )

    print("MODE: READ ONLY")

    official = load_official()
    provider = load_provider()

    official_keys = {
        key(m)
        for m in official
    }

    provider_keys = {
        key(m)
        for m in provider
    }

    missing = official_keys - provider_keys
    unexpected = provider_keys - official_keys

    print("\n=== RAW COMPARISON ===")

    print(
        "Official matches:",
        len(official_keys),
    )

    print(
        "Provider matches:",
        len(provider_keys),
    )

    print(
        "Exact matches:",
        len(
            official_keys & provider_keys
        ),
    )

    print(
        "Official-only:",
        len(missing),
    )

    print(
        "Provider-only:",
        len(unexpected),
    )

    expected_missing = set(EXCEPTIONS)

    expected_unexpected = {
        (round_number, *pair)
        for (
            round_number,
            official_home,
            official_away,
        ), pair in EXCEPTIONS.items()
    }

    require(
        missing == expected_missing,
        "Unexpected official discrepancies",
    )

    require(
        unexpected == expected_unexpected,
        "Unexpected provider discrepancies",
    )

    print("\n=== EXPLICIT EXCEPTIONS ===")

    for original, replacement in sorted(
        EXCEPTIONS.items()
    ):

        round_number = original[0]

        print()
        print("Round:", round_number)

        print(
            "LFP:",
            original[1],
            "-",
            original[2],
        )

        print(
            "Provider:",
            replacement[0],
            "-",
            replacement[1],
        )

        print(
            "Status:",
            (
                "RESULT CONFIRMED"
                if round_number == 1
                else "PROVISIONAL / NEEDS LFP CONFIRMATION"
            ),
        )

    print("\n=== RECONCILED CALENDAR ===")

    reconciled = set()

    for item in official:

        original = key(item)

        if original in EXCEPTIONS:

            pair = EXCEPTIONS[original]

            updated = (
                original[0],
                pair[0],
                pair[1],
            )

        else:

            updated = original

        require(
            updated not in reconciled,
            "Reconciled duplicate",
        )

        reconciled.add(updated)

    require(
        reconciled == provider_keys,
        "Reconciled calendar differs from provider",
    )

    print(
        "Reconciled matches:",
        len(reconciled),
    )

    print(
        "Matches matching provider:",
        len(reconciled & provider_keys),
    )

    print("\n=== ROUND 1 RESULT ===")

    first_round = [
        item
        for item in provider
        if (
            item["gameweek"] == 1
            and item["home_key"] == RENNES
            and item["away_key"] == PSG
        )
    ]

    require(
        len(first_round) == 1,
        "Round 1 Rennes-PSG missing",
    )

    check_football_data(
        first_round[0]
    )

    print("\n=== ROUND 23 STATUS ===")

    round23 = [
        item
        for item in provider
        if (
            item["gameweek"] == 23
            and item["home_key"] == PSG
            and item["away_key"] == RENNES
        )
    ]

    require(
        len(round23) == 1,
        "Round 23 PSG-Rennes missing",
    )

    item = round23[0]

    require(
        not item["finished"],
        "Round 23 unexpectedly finished",
    )

    print(
        "Date:",
        item["date"],
    )

    print(
        "Time:",
        item["time"],
    )

    print(
        "Official current fixture:",
        "NOT YET INDEPENDENTLY VERIFIED",
    )

    print("\n=== FINAL STATUS ===")

    print("306 MATCHES: RECONCILED")
    print("304 EXACT OFFICIAL MATCHES")
    print("2 EXPLICIT HOME/AWAY EXCEPTIONS")
    print("ROUND 1 RESULT: INDEPENDENTLY VERIFIED")
    print("ROUND 23: PROVISIONAL")

    print("RECONCILIATION: PASSED")
    print("OFFICIAL CURRENT CALENDAR: NOT FULLY VERIFIED")
    print("NO ARCHIVES CREATED")
    print("NO PROJECT STATE WRITES")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
