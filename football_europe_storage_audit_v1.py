#!/usr/bin/env python3
"""Bubus European Storage Architecture Audit.

STRICTLY READ ONLY / OFFLINE.

Inspect actual JSON structures without assuming
all leagues use the same schema.

No writes.
No network.
No forecasts.
"""

import json
from pathlib import Path
from collections import Counter


ROOT = Path(
    "state/football_live/calendars"
)

LEAGUES = (
    "epl",
    "laliga",
    "seriea",
    "bundesliga",
    "ligue1",
)


def describe_json(path):

    print("FILE:", path.name)

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )

    except Exception as exc:
        print(
            "JSON ERROR:",
            type(exc).__name__,
            str(exc),
        )
        return

    print("Root type:", type(data).__name__)

    if isinstance(data, dict):

        print("Root keys:", sorted(data))

        print("Schema:", data.get("schema"))

        matches = data.get("matches")

        if isinstance(matches, list):

            print("Matches:", len(matches))

            if matches:

                first = matches[0]

                if isinstance(first, dict):
                    print(
                        "Match fields:",
                        sorted(first),
                    )

                    print(
                        "First match:",
                        json.dumps(
                            first,
                            ensure_ascii=False,
                        )[:1000],
                    )

            for field in (
                "status",
                "schedule_status",
                "fixture_status",
                "time_status",
                "finished",
            ):

                counts = Counter(
                    str(item[field])
                    for item in matches
                    if isinstance(item, dict)
                    and field in item
                )

                if counts:
                    print(
                        field,
                        dict(counts),
                    )

    elif isinstance(data, list):

        print("Items:", len(data))

        if data:

            first = data[0]

            print(
                "First item type:",
                type(first).__name__,
            )

            if isinstance(first, dict):

                print(
                    "Item fields:",
                    sorted(first),
                )

                print(
                    "First item:",
                    json.dumps(
                        first,
                        ensure_ascii=False,
                    )[:1000],
                )

                for field in (
                    "status",
                    "schedule_status",
                    "fixture_status",
                    "time_status",
                    "finished",
                ):

                    counts = Counter(
                        str(item[field])
                        for item in data
                        if isinstance(item, dict)
                        and field in item
                    )

                    if counts:
                        print(
                            field,
                            dict(counts),
                        )

    else:
        print(
            "Unexpected root type:",
            type(data).__name__,
        )


def inspect_league(league):

    print()
    print("=" * 70)
    print("LEAGUE:", league)
    print("=" * 70)

    season = ROOT / f"{league}_season"

    if season.is_dir():

        archives = sorted(
            p for p in season.iterdir()
            if p.is_dir()
        )

        print("Season archives:", len(archives))

        for archive in archives:

            print()
            print(
                "ARCHIVE:",
                archive.name,
            )

            files = sorted(
                p for p in archive.iterdir()
                if p.is_file()
            )

            print(
                "Archive files:",
                [p.name for p in files],
            )

            for filename in (
                "calendar.json",
                "states.json",
                "provider_schedule.json",
            ):

                path = archive / filename

                if path.is_file():

                    print()
                    describe_json(path)

    else:
        print("Season directory: NOT FOUND")

    print()
    print("--- RELATED STORAGE ---")

    if ROOT.is_dir():

        for path in sorted(ROOT.iterdir()):

            if not path.name.startswith(league):
                continue

            if path.name == f"{league}_season":
                continue

            if path.is_dir():

                entries = list(path.iterdir())

                print(
                    path.name,
                    "| DIRECTORY",
                    "| entries:",
                    len(entries),
                )

                examples = sorted(
                    entries,
                    key=lambda p: p.name,
                )[-3:]

                for item in examples:

                    print(
                        "   ",
                        item.name,
                        "|",
                        "DIR" if item.is_dir()
                        else "FILE",
                    )

            elif path.is_file():

                print(
                    path.name,
                    "| FILE",
                    "| bytes:",
                    path.stat().st_size,
                )


def main():

    print(
        "=== EUROPE STORAGE AUDIT V1 ==="
    )

    print("MODE: READ ONLY / OFFLINE")

    for league in LEAGUES:

        inspect_league(league)

    print()
    print("=== FINAL STATUS ===")
    print("INSPECTION COMPLETED")
    print("NO NETWORK REQUESTS")
    print("NO FILES MODIFIED")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
