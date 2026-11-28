#!/usr/bin/env python3
"""Bubus Bundesliga Sources V2.

Check alternative fixture sources and JSON structure.

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import json
import urllib.request
import urllib.error
from collections import Counter

SOURCES = {
    "openligadb_season":
        "https://api.openligadb.de/getmatchdata/bl1/2026",

    "openligadb_matchday_1":
        "https://api.openligadb.de/getmatchdata/bl1/2026/1",

    "openligadb_matchday_34":
        "https://api.openligadb.de/getmatchdata/bl1/2026/34",

    "dfb_datencenter":
        "https://datencenter.dfb.de/competitions/bundesliga/seasons/2026-2027",

    "dfl_official_pdf":
        "https://media.dfl.de/sites/2/2026/07/DE_s73GnueV_Bundesliga_Spielplan_2026_27.pdf",
}

MAX_JSON = 5_000_000
MAX_HTML = 100_000


def fetch(url, limit):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        status = response.status

        content_type = response.headers.get(
            "Content-Type", ""
        )

        raw = response.read(limit + 1)

        truncated = len(raw) > limit

        if truncated:
            raw = raw[:limit]

        return (
            status,
            content_type,
            raw,
            truncated,
        )


def inspect_matches(data):

    print("\n=== JSON STRUCTURE ===")

    print("Root type:", type(data).__name__)

    if isinstance(data, dict):

        print("Root keys:", list(data)[:30])

        possible = [
            value
            for value in data.values()
            if isinstance(value, list)
        ]

        if len(possible) == 1:
            data = possible[0]

    if not isinstance(data, list):
        print("No direct match list")
        return

    print("Records:", len(data))

    if not data:
        return

    first = data[0]

    if not isinstance(first, dict):
        print("First record is not an object")
        return

    print("First record keys:")
    print(sorted(first.keys()))

    for field in (
        "matchID",
        "matchDateTime",
        "matchDateTimeUTC",
        "group",
        "team1",
        "team2",
        "matchIsFinished",
        "matchResults",
    ):

        if field not in first:
            continue

        value = first[field]

        print(
            field + ":",
            json.dumps(
                value,
                ensure_ascii=False,
            )[:400],
        )

    print("\n=== DATA SUMMARY ===")

    groups = Counter()
    teams = set()
    ids = []

    for match in data:

        if not isinstance(match, dict):
            continue

        match_id = match.get("matchID")

        if match_id is not None:
            ids.append(match_id)

        group = match.get("group") or {}

        if isinstance(group, dict):
            number = group.get("groupOrderID")

            if number is not None:
                groups[number] += 1

        for field in ("team1", "team2"):

            team = match.get(field) or {}

            if isinstance(team, dict):

                team_id = team.get("teamId")

                if team_id is None:
                    team_id = team.get("teamID")

                if team_id is not None:
                    teams.add(team_id)

    print("Unique match IDs:", len(set(ids)))
    print("Match IDs present:", len(ids))
    print("Unique teams:", len(teams))
    print("Matchdays:", len(groups))
    print("Games per matchday:", dict(sorted(groups.items())))


def main():

    print("=== BUBUS BUNDESLIGA SOURCES V2 ===")
    print("MODE: READ ONLY")

    for name, url in SOURCES.items():

        print("\n" + "=" * 60)
        print("SOURCE:", name)
        print("URL:", url)

        try:

            is_json = name.startswith("openligadb")
            is_pdf = name.endswith("_pdf")

            limit = (
                MAX_JSON
                if is_json
                else 4096 if is_pdf else MAX_HTML
            )

            status, ctype, raw, truncated = fetch(
                url,
                limit,
            )

            print("HTTP:", status)
            print("Content-Type:", ctype)
            print("Bytes read:", len(raw))
            print("Truncated:", truncated)

            if is_pdf:

                print(
                    "PDF signature:",
                    raw[:8],
                )

                print(
                    "PDF detected:",
                    raw.startswith(b"%PDF-"),
                )

                continue

            if not is_json:

                html = raw.decode(
                    "utf-8",
                    errors="replace",
                )

                print(
                    "Contains 'Bundesliga':",
                    "bundesliga" in html.lower(),
                )

                print(
                    "Contains '2026':",
                    "2026" in html,
                )

                continue

            if truncated:
                print("JSON exceeds size limit")
                continue

            data = json.loads(raw)

            inspect_matches(data)

        except Exception as exc:

            print(
                "ERROR:",
                type(exc).__name__,
                str(exc)[:300],
            )

    print("\n=== FINAL STATUS ===")
    print("READ ONLY")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
