#!/usr/bin/env python3
"""Bubus V8 EPL result collector.

Read-only research version.

Reads immutable observations and checks final scores
against OpenLigaDB.

Does not modify forecasts, archives or databases.
"""

import hashlib
import json
import urllib.request

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "state/football_v8_epl_shadow"

OBSERVATIONS = DATA / "observations"

URL = "https://api.openligadb.de/getmatchdata/pl/2026"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_utc(value):
    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    require(
        dt.tzinfo is not None,
        "Timezone missing",
    )

    return dt.astimezone(timezone.utc)


def load_observations():

    files = sorted(OBSERVATIONS.glob("epl_*.json"))

    require(files, "No registered observations")

    observations = []

    seen_matches = set()
    seen_provider = set()

    for path in files:

        observation = json.loads(
            path.read_text(encoding="utf-8")
        )

        require(
            observation.get("schema")
            == "bubus_v8_epl_observation_v1",
            f"Invalid observation schema: {path}",
        )

        require(
            observation.get("competition") == "EPL",
            "Invalid competition",
        )

        require(
            observation.get("production_eligible") is False,
            "Unexpected production authorization",
        )

        require(
            observation.get("historical_results_verified")
            is False,
            "Unexpected historical verification flag",
        )

        match_id = str(observation["match_id"])

        provider_id = str(
            observation["provider_match_id"]
        )

        require(
            match_id not in seen_matches,
            "Duplicate match ID",
        )

        require(
            provider_id not in seen_provider,
            "Duplicate provider ID",
        )

        seen_matches.add(match_id)
        seen_provider.add(provider_id)

        # Verify that the original batch remains unchanged.
        batch_path = DATA / observation["batch_file"]

        require(
            batch_path.is_file()
            and sha(batch_path.read_bytes())
            == observation["batch_sha256"],
            f"Batch integrity failure: {match_id}",
        )

        # Verify that the kickoff audit remains unchanged.
        audit_path = DATA / observation["audit_file"]

        require(
            audit_path.is_file()
            and sha(audit_path.read_bytes())
            == observation["audit_sha256"],
            f"Audit integrity failure: {match_id}",
        )

        kickoff = parse_utc(
            observation["kickoff_utc"]
        )

        created = parse_utc(
            observation["forecast_created_utc"]
        )

        registered = parse_utc(
            observation["registered_utc"]
        )

        require(
            created < registered < kickoff,
            f"Invalid forecast timing: {match_id}",
        )

        observations.append(observation)

    return observations


def fetch_results():

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Bubus-V8-Research/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        raw = response.read(5_000_001)

    require(
        len(raw) <= 5_000_000,
        "Provider response too large",
    )

    matches = json.loads(raw)

    require(
        isinstance(matches, list)
        and len(matches) == 380,
        "Unexpected provider season",
    )

    indexed = {}

    for match in matches:

        key = str(match["matchID"])

        require(
            key not in indexed,
            "Duplicate provider match ID",
        )

        indexed[key] = match

    return indexed, sha(raw)


def final_score(match):

    if match.get("matchIsFinished") is not True:
        return None

    results = match.get("matchResults") or []

    finals = [
        item
        for item in results
        if item.get("resultTypeID") == 2
    ]

    if len(finals) != 1:
        return None

    result = finals[0]

    home = result.get("pointsTeam1")
    away = result.get("pointsTeam2")

    if not (
        type(home) is int
        and type(away) is int
        and 0 <= home <= 30
        and 0 <= away <= 30
    ):
        return None

    return home, away


def main():

    print("=== BUBUS V8 EPL RESULT COLLECTOR ===")
    print("MODE: READ ONLY")

    observations = load_observations()

    print("Registered observations:", len(observations))

    require(
        len(observations) == 30,
        "Unexpected observation count",
    )

    provider, provider_hash = fetch_results()

    print("Provider matches:", len(provider))
    print("Provider SHA256:", provider_hash)

    now = datetime.now(timezone.utc)

    stats = Counter()

    print()
    print("=== MATCH CHECK ===")

    for observation in observations:

        match_id = str(observation["match_id"])

        provider_id = str(
            observation["provider_match_id"]
        )

        kickoff = parse_utc(
            observation["kickoff_utc"]
        )

        match = provider.get(provider_id)

        require(
            match is not None,
            f"Provider match missing: {provider_id}",
        )

        home_id = (
            match.get("team1") or {}
        ).get("teamId")

        away_id = (
            match.get("team2") or {}
        ).get("teamId")

        require(
            home_id
            == observation["provider_home_team_id"]
            if "provider_home_team_id" in observation
            else True,
            "Home team identity changed",
        )

        require(
            away_id
            == observation["provider_away_team_id"]
            if "provider_away_team_id" in observation
            else True,
            "Away team identity changed",
        )

        print()
        print(
            observation["home"],
            "—",
            observation["away"],
        )

        if kickoff > now:

            stats["UPCOMING"] += 1

            print("Status: UPCOMING")
            continue

        score = final_score(match)

        if score is None:

            if match.get("matchIsFinished") is True:
                stats["FINISHED_NO_FINAL_SCORE"] += 1
                print("Status: FINAL SCORE UNAVAILABLE")
            else:
                stats["PENDING"] += 1
                print("Status: PENDING")

            continue

        print("OpenLigaDB score:", f"{score[0]}:{score[1]}")
        print("Status: PROVIDER RESULT CANDIDATE")

        stats["RESULT_CANDIDATE"] += 1

    print()
    print("=== SUMMARY ===")

    for key, value in sorted(stats.items()):
        print(key, value)

    print()
    print("Results independently corroborated: NO")
    print("Historical CSV modified: NO")
    print("Observations modified: NO")
    print("Telegram publication: DISABLED")


if __name__ == "__main__":
    main()
