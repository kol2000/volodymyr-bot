#!/usr/bin/env python3
"""Bubus Europe Adapter V1.

Unified read-only calendar adapter for:

- EPL
- LaLiga
- SerieA
- Bundesliga
- Ligue1

Season: 2026/27

STRICTLY READ ONLY / OFFLINE.

This version reads immutable season calendars only.

No state reconstruction.
No forecast calculations.
No database writes.
No Telegram messages.

All kickoff times remain UNVERIFIED.
"""

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent

BASE = (
    ROOT
    / "state"
    / "football_live"
    / "calendars"
)

EXPECTED = {
    "EPL": 380,
    "LaLiga": 380,
    "SerieA": 380,
    "Bundesliga": 306,
    "Ligue1": 306,
}

DIRECTORIES = {
    "EPL": "epl",
    "LaLiga": "laliga",
    "SerieA": "seriea",
    "Bundesliga": "bundesliga",
    "Ligue1": "ligue1",
}

EXPECTED_SCHEMAS = {
    "EPL": "bubus-epl-season-v1",
    "LaLiga": "bubus-laliga-calendar-v2",
    "SerieA": "bubus-seriea-calendar-v1",
    "Bundesliga": "bubus-bundesliga-calendar-v3",
    "Ligue1": "bubus-ligue1-calendar-v1",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(path):

    require(
        path.is_file(),
        "Missing file: " + str(path),
    )

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def load_archive(league):

    directory = (
        BASE
        / (DIRECTORIES[league] + "_season")
    )

    require(
        directory.is_dir(),
        f"{league}: archive directory missing",
    )

    archives = sorted(
        path
        for path in directory.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
    )

    require(
        bool(archives),
        f"{league}: no calendar archives",
    )

    if league == "LaLiga":

        from datetime import timezone

        candidates = []

        seen_timestamps = set()

        for path in archives:

            manifest = read_json(
                path / "manifest.json"
            )

            calendar = read_json(
                path / "calendar.json"
            )

            require(
                manifest.get("schema")
                == "bubus-laliga-season-archive-v1",
                "LaLiga: invalid archive manifest",
            )

            require(
                manifest.get("content_id") == path.name,
                "LaLiga: archive content ID mismatch",
            )

            require(
                calendar.get("schema")
                == EXPECTED_SCHEMAS[league],
                "LaLiga: invalid calendar schema",
            )

            require(
                isinstance(calendar.get("matches"), list)
                and len(calendar["matches"]) == EXPECTED[league],
                "LaLiga: invalid match count",
            )

            fetched = datetime.fromisoformat(
                manifest["fetched_utc"].replace(
                    "Z", "+00:00"
                )
            )

            require(
                fetched.tzinfo is not None,
                "LaLiga: timezone missing",
            )

            fetched = fetched.astimezone(timezone.utc)

            require(
                fetched not in seen_timestamps,
                "LaLiga: duplicate archive timestamps",
            )

            seen_timestamps.add(fetched)

            candidates.append((
                fetched,
                path,
                manifest,
                calendar,
            ))

        _, archive, manifest, calendar = max(
            candidates,
            key=lambda item: item[0],
        )

        return archive, manifest, calendar

    # Existing strict behavior for other leagues.
    require(
        len(archives) == 1,
        f"{league}: expected exactly one archive",
    )

    archive = archives[0]

    calendar = read_json(
        archive / "calendar.json"
    )

    manifest = read_json(
        archive / "manifest.json"
    )

    require(
        isinstance(calendar, dict),
        f"{league}: invalid calendar root",
    )

    require(
        calendar.get("schema")
        == EXPECTED_SCHEMAS[league],
        f"{league}: unexpected schema",
    )

    matches = calendar.get("matches")

    require(
        isinstance(matches, list),
        f"{league}: matches must be a list",
    )

    require(
        len(matches) == EXPECTED[league],
        f"{league}: unexpected match count",
    )

    return archive, manifest, calendar

def normalize_match(league, row):

    require(
        isinstance(row, dict),
        f"{league}: invalid match record",
    )

    home = row.get("home")
    away = row.get("away")

    require(
        isinstance(home, str)
        and isinstance(away, str)
        and bool(home.strip())
        and bool(away.strip())
        and home != away,
        f"{league}: invalid teams",
    )

    if league == "Ligue1":

        match_id = row.get("match_id")
        round_number = row.get("round")

        # Ligue 1 calendar identities are separate
        # from the provider schedule.
        #
        # This adapter does not infer a kickoff.

        match_date = None

        kickoff_candidate = None

        schedule_status = (
            row.get("fixture_status")
        )

    else:

        round_number = row.get("gameweek")

        match_date = row.get("date")

        kickoff_candidate = row.get(
            "kickoff_utc"
        )

        schedule_status = row.get(
            "schedule_status",
            row.get("status"),
        )

        if league == "EPL":

            # The official EPL calendar has no
            # permanent provider fixture ID.
            #
            # The directed home/away pair is
            # unique within a double round-robin.
            #
            # This is an adapter-local identity,
            # not an official fixture identifier.

            match_id = (
                "epl-2026-27:"
                + home
                + ":"
                + away
            )

        else:

            match_id = row.get("id")

    require(
        match_id is not None
        and bool(str(match_id).strip()),
        f"{league}: missing match identity",
    )

    if match_date is not None:

        require(
            isinstance(match_date, str),
            f"{league}: invalid date type",
        )

        date.fromisoformat(match_date)

    if kickoff_candidate is not None:

        require(
            isinstance(kickoff_candidate, str),
            f"{league}: invalid kickoff type",
        )

        parsed = datetime.fromisoformat(
            kickoff_candidate.replace(
                "Z",
                "+00:00",
            )
        )

        require(
            parsed.tzinfo is not None,
            f"{league}: kickoff has no timezone",
        )

    return {
        "competition": league,
        "season": "2026/27",
        "match_id": str(match_id),
        "home": home,
        "away": away,
        "round": round_number,
        "date": match_date,
        "kickoff_candidate_utc": (
            kickoff_candidate
        ),
        "kickoff_verified": False,
        "schedule_status": schedule_status,
    }


def load_all():

    all_matches = []

    archives = {}

    for league in EXPECTED:

        archive, manifest, calendar = (
            load_archive(league)
        )

        normalized = [
            normalize_match(league, row)
            for row in calendar["matches"]
        ]

        require(
            len(normalized) == EXPECTED[league],
            f"{league}: normalization count mismatch",
        )

        keys = [
            match["match_id"]
            for match in normalized
        ]

        require(
            len(keys) == len(set(keys)),
            f"{league}: duplicate match IDs",
        )

        all_matches.extend(normalized)

        archives[league] = {
            "archive": archive.name,
            "schema": calendar["schema"],
            "matches": len(normalized),
        }

    global_keys = [
        (
            match["competition"],
            match["match_id"],
        )
        for match in all_matches
    ]

    require(
        len(global_keys) == len(set(global_keys)),
        "Global match identities are not unique",
    )

    require(
        len(all_matches) == 1752,
        "Unexpected European match total",
    )

    return all_matches, archives


def main():

    print("=== BUBUS EUROPE ADAPTER V1 ===")

    print("MODE: READ ONLY / OFFLINE")

    matches, archives = load_all()

    print("\n=== CHAMPIONSHIPS ===")

    for league, info in archives.items():

        print(
            f"{league:12s}",
            "matches:",
            info["matches"],
        )

        print(
            "  Archive:",
            info["archive"],
        )

        print(
            "  Schema:",
            info["schema"],
        )

    print("\n=== TOTALS ===")

    counts = Counter(
        match["competition"]
        for match in matches
    )

    print("Total matches:", len(matches))

    print(
        "League counts:",
        dict(counts),
    )

    print(
        "Unique match identities:",
        len(set(
            (
                match["competition"],
                match["match_id"],
            )
            for match in matches
        )),
    )

    print(
        "Kickoffs verified:",
        sum(
            match["kickoff_verified"]
            for match in matches
        ),
    )

    print("\n=== SAMPLE MATCHES ===")

    for league in EXPECTED:

        sample = next(
            match
            for match in matches
            if match["competition"] == league
        )

        print()
        print(league)

        print(
            json.dumps(
                sample,
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\n=== FINAL STATUS ===")

    print("CALENDAR NORMALIZATION: PASSED")
    print("MATCH COUNTS: PASSED")
    print("MATCH IDENTITIES: UNIQUE")
    print("KICKOFF VERIFICATION: NOT PERFORMED")
    print("ARCHIVE INTEGRITY: NOT AUDITED IN V1")
    print("STATES: NOT LOADED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
