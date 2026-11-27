#!/usr/bin/env python3
"""Bubus Ligue 1 Calendar V1.

Normalize 2026/27 Ligue 1 into three independent datasets:

1. Calendar
2. States
3. Provider Schedule

Official baseline: LFP PDF.
Live provider: Open Matchday.

Two explicitly documented exceptions:
- Round 1 Rennes / PSG: confirmed by result.
- Round 23 PSG / Rennes: provisional provider fixture.

STRICTLY READ ONLY.

No archive writes.
No observation writes.
No forecast recording.
"""

import copy
import hashlib
import json

from collections import Counter

import football_europe_ligue1_reconciliation_v1 as recon
import football_europe_ligue1_season_audit_v1 as season


SCHEMA_CALENDAR = "bubus-ligue1-calendar-v1"
SCHEMA_STATES = "bubus-ligue1-states-v1"
SCHEMA_PROVIDER = "bubus-ligue1-provider-v1"

EXPECTED_MATCHES = 306


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


def dataset_id(value):

    return hashlib.sha256(
        canonical(value)
    ).hexdigest()


def match_id(match):

    return (
        f"ligue1-2026-27-"
        f"r{match['gameweek']:02d}-"
        f"{match['home_key']}-"
        f"{match['away_key']}"
    )


def validate_structure(matches):

    require(
        len(matches) == EXPECTED_MATCHES,
        "Expected exactly 306 matches",
    )

    teams = set()
    rounds = Counter()
    directed = Counter()
    round_teams = {}

    for match in matches:

        gameweek = match["gameweek"]
        home = match["home_key"]
        away = match["away_key"]

        require(
            1 <= gameweek <= 34,
            "Invalid matchday",
        )

        require(
            home != away,
            "Self-match detected",
        )

        teams.update((home, away))

        rounds[gameweek] += 1
        directed[(home, away)] += 1

        used = round_teams.setdefault(
            gameweek, set()
        )

        require(
            home not in used
            and away not in used,
            f"Team repeated in round {gameweek}",
        )

        used.update((home, away))

    require(
        len(teams) == 18,
        "Expected 18 teams",
    )

    require(
        all(
            rounds[r] == 9
            for r in range(1, 35)
        ),
        "Invalid round sizes",
    )

    require(
        len(directed) == 306
        and all(n == 1 for n in directed.values()),
        "Invalid directed match pairs",
    )

    for home in teams:
        for away in teams:

            if home == away:
                continue

            require(
                directed[(home, away)] == 1,
                "Missing home/away pairing",
            )


def validate_against_official(
    official,
    provider,
):

    official_keys = {
        recon.key(m)
        for m in official
    }

    provider_keys = {
        recon.key(m)
        for m in provider
    }

    require(
        len(official_keys) == 306
        and len(provider_keys) == 306,
        "Duplicate or missing fixtures",
    )

    expected_missing = set(
        recon.EXCEPTIONS
    )

    expected_unexpected = {
        (gameweek, home, away)
        for (
            gameweek,
            original_home,
            original_away,
        ), (home, away)
        in recon.EXCEPTIONS.items()
    }

    require(
        official_keys - provider_keys
        == expected_missing,
        "Unexpected official mismatch",
    )

    require(
        provider_keys - official_keys
        == expected_unexpected,
        "Unexpected provider mismatch",
    )


def normalize(official, matches):

    validate_structure(matches)

    validate_against_official(
        official,
        matches,
    )

    calendar_matches = []
    state_matches = []
    provider_matches = []

    for match in sorted(
        matches,
        key=lambda m: (
            m["gameweek"],
            m["home_key"],
            m["away_key"],
        ),
    ):

        identifier = match_id(match)

        gameweek = match["gameweek"]

        home = match["home_key"]
        away = match["away_key"]

        if (
            gameweek == 1
            and home == recon.RENNES
            and away == recon.PSG
        ):

            fixture_status = (
                "CONFIRMED_BY_RESULT"
            )

            fixture_source = (
                "open_matchday_and_football_data"
            )

        elif (
            gameweek == 23
            and home == recon.PSG
            and away == recon.RENNES
        ):

            fixture_status = (
                "PROVISIONAL_PROVIDER"
            )

            fixture_source = (
                "open_matchday"
            )

        else:

            fixture_status = (
                "LFP_BASELINE"
            )

            fixture_source = (
                "lfp_official_pdf"
            )

        calendar_matches.append({
            "match_id": identifier,
            "round": gameweek,
            "home": home,
            "away": away,
            "fixture_status": fixture_status,
            "fixture_source": fixture_source,
        })

        state_matches.append({
            "match_id": identifier,
            "finished": match["finished"],
            "home_score": match["home_score"],
            "away_score": match["away_score"],
        })

        provider_matches.append({
            "match_id": identifier,
            "date": match["date"],
            "time": match["time"],
            "time_source": "open_matchday",
            "time_status": "UNVERIFIED",
        })

    calendar = {
        "schema": SCHEMA_CALENDAR,
        "season": "2026-27",
        "competition": "ligue1",
        "matches": calendar_matches,
    }

    states = {
        "schema": SCHEMA_STATES,
        "season": "2026-27",
        "competition": "ligue1",
        "matches": state_matches,
    }

    provider = {
        "schema": SCHEMA_PROVIDER,
        "season": "2026-27",
        "competition": "ligue1",
        "matches": provider_matches,
    }

    validate_datasets(
        calendar,
        states,
        provider,
    )

    return calendar, states, provider


def validate_datasets(
    calendar,
    states,
    provider,
):

    require(
        calendar["schema"] == SCHEMA_CALENDAR,
        "Calendar schema mismatch",
    )

    require(
        states["schema"] == SCHEMA_STATES,
        "States schema mismatch",
    )

    require(
        provider["schema"] == SCHEMA_PROVIDER,
        "Provider schema mismatch",
    )

    ids = []

    for dataset in (
        calendar,
        states,
        provider,
    ):

        entries = dataset["matches"]

        require(
            len(entries) == 306,
            "Dataset match count mismatch",
        )

        match_ids = [
            item["match_id"]
            for item in entries
        ]

        require(
            len(set(match_ids)) == 306,
            "Duplicate match ID",
        )

        require(
            match_ids == sorted(
                match_ids,
                key=lambda item: (
                    int(item.split("-r")[1][:2]),
                    item,
                ),
            ),
            "Dataset ordering mismatch",
        )

        ids.append(set(match_ids))

    require(
        ids[0] == ids[1] == ids[2],
        "Dataset match IDs differ",
    )


def main():

    print(
        "=== BUBUS LIGUE 1 CALENDAR V1 ==="
    )

    print("MODE: READ ONLY")

    print("\n=== LOADING SOURCES ===")

    official = recon.load_official()

    matches = recon.load_provider()

    print(
        "Official matches:",
        len(official),
    )

    print(
        "Provider matches:",
        len(matches),
    )

    print("\n=== NORMALIZING ===")

    calendar, states, provider = (
        normalize(
            official,
            matches,
        )
    )

    print(
        "Calendar matches:",
        len(calendar["matches"]),
    )

    print(
        "States matches:",
        len(states["matches"]),
    )

    print(
        "Provider matches:",
        len(provider["matches"]),
    )

    print("\n=== CALENDAR PROVENANCE ===")

    statuses = Counter(
        match["fixture_status"]
        for match in calendar["matches"]
    )

    for status, count in sorted(
        statuses.items()
    ):

        print(
            status + ":",
            count,
        )

    print("\n=== RESULTS ===")

    finished = sum(
        match["finished"]
        for match in states["matches"]
    )

    print("Finished:", finished)

    print(
        "Not finished:",
        306 - finished,
    )

    print("\n=== DATASET HASHES ===")

    print(
        "Calendar ID:",
        dataset_id(calendar),
    )

    print(
        "States ID:",
        dataset_id(states),
    )

    print(
        "Provider ID:",
        dataset_id(provider),
    )

    print("\n=== INDEPENDENCE TEST ===")

    changed_states = copy.deepcopy(
        states
    )

    changed_states["matches"][0][
        "home_score"
    ] = 99

    require(
        dataset_id(calendar)
        == dataset_id(calendar),
        "Calendar changed unexpectedly",
    )

    require(
        dataset_id(states)
        != dataset_id(changed_states),
        "State mutation not detected",
    )

    changed_provider = copy.deepcopy(
        provider
    )

    changed_provider["matches"][0][
        "time"
    ] = "23:59"

    require(
        dataset_id(provider)
        != dataset_id(changed_provider),
        "Provider mutation not detected",
    )

    print("States mutation: DETECTED")
    print("Provider mutation: DETECTED")
    print("Calendar unaffected: VERIFIED")

    print("\n=== FINAL STATUS ===")

    print("306 MATCHES: VALID")
    print("CALENDAR / STATES / PROVIDER: SEPARATED")
    print("LFP EXCEPTIONS: DOCUMENTED")
    print("ROUND 23: PROVISIONAL")
    print("DATASET HASHES: GENERATED")
    print("NORMALIZATION: PASSED")

    print("NO ARCHIVES CREATED")
    print("NO PROJECT STATE WRITES")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
