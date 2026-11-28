#!/usr/bin/env python3
"""Bubus V8 automatic Bundesliga result collector.

Research only. Read-only by default.
Writes only confirmed results when --write is supplied.
"""

import argparse
import json
import math
import re

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from football_forecast_v8_results import (
    fetch_matches,
    get_final_score,
)

from football_soccer365_v8_mapping_v1 import (
    load_mapping,
    soccer,
)


JOURNAL = Path(
    "state/football_v8_shadow/observations.jsonl"
)

COMPETITION = "Чемпионат Германии"


def resolve_team(name, mapping):
    """Resolve a Russian team name using the existing explicit bridge."""

    normalized = soccer.identity(name)

    if normalized in mapping:
        return mapping[normalized]

    candidates = []

    for alias, provider_name in mapping.items():

        # Accept an explicit alias at the beginning of a longer name,
        # or as a complete sequence of words inside the name.
        pattern = (
            r"(?<!\w)"
            + re.escape(alias)
            + r"(?!\w)"
        )

        if re.search(pattern, normalized):
            candidates.append(provider_name)

    candidates = set(candidates)

    if len(candidates) != 1:
        raise ValueError(
            f"Ambiguous or unknown team: {name!r}; "
            f"candidates={sorted(candidates)}"
        )

    return next(iter(candidates))


def parse_time(value):

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if dt.tzinfo is None:
        raise ValueError("Naive kickoff timestamp")

    return dt.astimezone(timezone.utc)


def read_observations():

    rows = []

    for line in JOURNAL.read_text(
        encoding="utf-8"
    ).splitlines():

        if not line.strip():
            continue

        row = json.loads(line)

        if row.get("schema") != "bubus_v8_observation_v2":
            continue

        rows.append(row)

    return rows


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print("=== BUBUS V8 AUTOMATIC RESULT COLLECTOR ===")
    print("MODE:", "WRITE" if args.write else "READ ONLY")

    now = datetime.now(timezone.utc)

    mapping = load_mapping()["Bundesliga"]

    observations = read_observations()

    groups = defaultdict(list)

    for row in observations:
        groups[row["match_id"]].append(row)

    print("Observation records:", len(observations))
    print("Unique matches:", len(groups))

    provider_data = fetch_matches()

    print("Provider matches:", len(provider_data))

    verified_results = []

    pending = 0
    finished = 0
    blocked = 0

    for match_id, records in sorted(groups.items()):

        print("\n" + "-" * 60)
        print("Match:", match_id)

        try:

            # All historical records for one match must agree
            # on the identities of the teams and kickoff.
            identities = {
                (
                    row["competition"],
                    row["home"],
                    row["away"],
                    parse_time(row["kickoff_utc"]),
                )
                for row in records
            }

            if len(identities) != 1:
                raise ValueError(
                    "Conflicting observation identities"
                )

            competition, home, away, kickoff = (
                next(iter(identities))
            )

            if competition != COMPETITION:
                raise ValueError(
                    f"Unsupported competition: {competition}"
                )

            # Every candidate observation must predate kickoff.
            for row in records:

                created = parse_time(
                    row["created_utc"]
                )

                if created >= kickoff:
                    raise ValueError(
                        "Observation created after kickoff"
                    )

            provider_home = resolve_team(home, mapping)
            provider_away = resolve_team(away, mapping)

            if provider_home == provider_away:
                raise ValueError(
                    "Home and away resolve to same team"
                )

            print("Home:", home, "=>", provider_home)
            print("Away:", away, "=>", provider_away)

            candidates = []

            for match in provider_data:

                team1 = (
                    match.get("team1") or {}
                ).get("teamName")

                team2 = (
                    match.get("team2") or {}
                ).get("teamName")

                if (team1, team2) != (
                    provider_home,
                    provider_away,
                ):
                    continue

                provider_kickoff = parse_time(
                    match["matchDateTimeUTC"]
                )

                difference = abs(
                    (
                        kickoff - provider_kickoff
                    ).total_seconds()
                )

                if difference <= 60:
                    candidates.append(match)

            if len(candidates) != 1:
                raise ValueError(
                    f"Provider match count: {len(candidates)}"
                )

            match = candidates[0]

            provider_id = match["matchID"]

            print("Provider ID:", provider_id)
            print("Identity: VERIFIED")
            print("Kickoff: VERIFIED")

            if now <= kickoff:

                print("STATUS: UPCOMING")
                pending += 1
                continue

            if match.get("matchIsFinished") is not True:

                print("STATUS: NOT_FINISHED")
                pending += 1
                continue

            score = get_final_score(match)

            if score is None:
                raise ValueError(
                    "Final score not verified"
                )

            home_goals, away_goals = score

            if home_goals > away_goals:
                outcome = "HOME"
            elif home_goals < away_goals:
                outcome = "AWAY"
            else:
                outcome = "DRAW"

            result = {
                "schema": "bubus_v8_result_v1",
                "match_id": match_id,
                "provider": "OpenLigaDB",
                "provider_id": provider_id,
                "home": provider_home,
                "away": provider_away,
                "kickoff_utc": kickoff.isoformat(),
                "home_goals": home_goals,
                "away_goals": away_goals,
                "outcome": outcome,
                "collected_utc": now.isoformat(),
            }

            verified_results.append(result)

            print(
                "STATUS: VERIFIED_FINISHED"
            )

            print(
                f"Score: {home_goals}:{away_goals}"
            )

            print("Outcome:", outcome)

            finished += 1

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:

            print(
                "STATUS: BLOCKED",
                type(exc).__name__,
                str(exc),
            )

            blocked += 1

    print("\n=== SUMMARY ===")

    print("Pending:", pending)
    print("Verified finished:", finished)
    print("Blocked:", blocked)

    if args.write:

        from football_forecast_v8_result_store import (
            save_results,
        )

        # Do not persist a partial batch when any identity
        # failed validation.
        if blocked:
            raise RuntimeError(
                "Blocked matches detected; no results saved"
            )

        added, skipped = save_results(
            verified_results
        )

        print("\n=== STORAGE ===")

        print("New results:", added)
        print("Already stored:", skipped)

    else:

        print("\nRead-only mode.")
        print("No journal writes.")

    print("No Telegram messages.")
    print("No forecast modifications.")


if __name__ == "__main__":
    main()
