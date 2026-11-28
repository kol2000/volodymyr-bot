#!/usr/bin/env python3
"""Bubus Europe Adapter V2.

Unified European calendar + restored match states.

READ ONLY / OFFLINE.

No network.
No database writes.
No forecast registration.
No Telegram publication.
"""

import json
from collections import Counter

import football_europe_adapter_v1 as adapter
import football_europe_adapter_integrity_v1 as integrity

import football_europe_seriea_monitor_v2 as seriea
import football_europe_bundesliga_monitor_v2 as bundesliga
import football_europe_ligue1_monitor_v2 as ligue1
import football_europe_ligue1_observations_audit_v1 as ligue1_audit


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def valid_score(home, away):
    return (
        type(home) is int
        and type(away) is int
        and 0 <= home <= 30
        and 0 <= away <= 30
    )


def index_states(league, content, states):

    if league == "Ligue1":
        records = states["matches"]
        id_field = "match_id"
    else:
        records = states
        id_field = "id"

    require(
        isinstance(records, list),
        f"{league}: invalid States dataset",
    )

    result = {}

    for row in records:
        require(
            isinstance(row, dict),
            f"{league}: invalid State record",
        )

        identifier = str(row[id_field])

        require(
            identifier not in result,
            f"{league}: duplicate State ID",
        )

        result[identifier] = row

    calendar_ids = {
        str(row[id_field])
        for row in content["matches"]
    }

    require(
        set(result) == calendar_ids,
        f"{league}: Calendar/States ID mismatch",
    )

    return result


def classify(league, state):

    home = state.get("home_score")
    away = state.get("away_score")

    has_score = (
        home is not None
        or away is not None
    )

    complete = valid_score(home, away)

    if league == "SerieA":

        finished = (
            state.get("status") == "FINISHED"
        )

        upcoming = (
            state.get("status") == "UPCOMING"
        )

        if finished and complete:
            return "FINISHED"

        if upcoming and not has_score:
            return "UPCOMING"

        return "QUARANTINED"

    if league == "Bundesliga":

        finished = (
            state.get("finished") is True
            and state.get("status") == "FINISHED"
        )

        reported = (
            state.get("reported_home_score")
            is not None
            or state.get("reported_away_score")
            is not None
        )

        if finished and complete:
            return "FINISHED"

        if (
            state.get("finished") is False
            and state.get("status") == "NOT_FINISHED"
            and not has_score
            and not reported
        ):
            return "UPCOMING"

        return "QUARANTINED"

    if league == "Ligue1":

        finished = (
            state.get("finished") is True
        )

        if finished and complete:
            return "FINISHED"

        if (
            state.get("finished") is False
            and not has_score
        ):
            return "UPCOMING"

        return "QUARANTINED"

    raise RuntimeError(
        f"Unknown league: {league}"
    )


def restore():

    print("\n=== RESTORING CURRENT STATES ===")

    # Ligue 1: verify the complete observation chain.
    ligue1_audit.audit(verbose=False)

    result = {}

    content, states, observation = (
        seriea.load_previous_observation()
    )

    result["SerieA"] = index_states(
        "SerieA", content, states
    )

    print(
        "SerieA:",
        len(result["SerieA"]),
        "states",
    )

    content, states, provider, observation = (
        bundesliga.load_previous_observation()
    )

    result["Bundesliga"] = index_states(
        "Bundesliga", content, states
    )

    print(
        "Bundesliga:",
        len(result["Bundesliga"]),
        "states",
    )

    content, states, provider, observation = (
        ligue1.load_previous_observation()
    )

    result["Ligue1"] = index_states(
        "Ligue1", content, states
    )

    print(
        "Ligue1:",
        len(result["Ligue1"]),
        "states",
    )

    return result


def load_all():

    # First validate the five immutable archives.
    for league in adapter.EXPECTED:
        integrity.validate_league(league)

    matches, archives = adapter.load_all()

    restored = restore()

    result = []
    counts = Counter()

    for match in matches:

        item = dict(match)

        league = item["competition"]

        if league not in restored:

            item["state_status"] = "STATE_UNAVAILABLE"
            item["finished"] = None
            item["home_score"] = None
            item["away_score"] = None

        else:

            state = restored[league].get(
                item["match_id"]
            )

            require(
                state is not None,
                f"{league}: State missing for "
                + item["match_id"],
            )

            status = classify(
                league,
                state,
            )

            item["state_status"] = status

            item["finished"] = (
                status == "FINISHED"
            )

            if status == "FINISHED":

                item["home_score"] = (
                    state["home_score"]
                )

                item["away_score"] = (
                    state["away_score"]
                )

            else:

                # Never expose provisional scores
                # as accepted final results.
                item["home_score"] = None
                item["away_score"] = None

        counts[
            (league, item["state_status"])
        ] += 1

        result.append(item)

    require(
        len(result) == 1752,
        "Incorrect unified match count",
    )

    keys = [
        (m["competition"], m["match_id"])
        for m in result
    ]

    require(
        len(keys) == len(set(keys)),
        "Duplicate unified match identity",
    )

    return result, counts


def main():

    print("=== BUBUS EUROPE ADAPTER V2 ===")
    print("MODE: READ ONLY / OFFLINE")

    matches, counts = load_all()

    print("\n=== LEAGUE STATES ===")

    statuses = (
        "FINISHED",
        "UPCOMING",
        "QUARANTINED",
        "STATE_UNAVAILABLE",
    )

    for league in adapter.EXPECTED:

        print()
        print(league)

        total = 0

        for status in statuses:

            n = counts[(league, status)]

            total += n

            print(
                f"  {status:20s}",
                n,
            )

        require(
            total == adapter.EXPECTED[league],
            f"{league}: count mismatch",
        )

    print("\n=== TOTALS ===")

    print("Matches:", len(matches))

    for status in statuses:

        print(
            status,
            sum(
                counts[(league, status)]
                for league in adapter.EXPECTED
            ),
        )

    print("\n=== QUARANTINED MATCHES ===")

    for match in matches:

        if match["state_status"] != "QUARANTINED":
            continue

        print(
            match["competition"],
            match["home"],
            "—",
            match["away"],
            "|",
            match["match_id"],
        )

    print("\n=== LIGUE 1 RESULT CHECK ===")

    target = [
        m for m in matches
        if m["match_id"]
        == "ligue1-2026-27-r11-paris fc-angers sco"
        and m["competition"] == "Ligue1"
    ]

    require(
        len(target) == 1,
        "Paris FC - Angers identity mismatch",
    )

    match = target[0]

    require(
        match["state_status"] == "FINISHED"
        and match["home_score"] == 4
        and match["away_score"] == 0,
        "Paris FC - Angers result mismatch",
    )

    print("Paris FC — Angers: 4:0 VERIFIED")

    print("\n=== FINAL STATUS ===")

    print("ARCHIVE INTEGRITY: PASSED")
    print("STATE RESTORATION: PASSED")
    print("MATCH IDENTITIES: UNIQUE")
    print("TOTAL MATCHES: 1752")
    print("KICKOFF VERIFICATION: NOT PERFORMED")
    print("FORECAST RECORDING: BLOCKED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")


if __name__ == "__main__":
    main()
