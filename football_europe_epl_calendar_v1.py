#!/usr/bin/env python3
"""Bubus EPL official calendar comparison V1.

Read only.

Only explicitly timed fixtures can MATCH.
Untimed fixtures remain UNVERIFIED.
"""

import csv
import hashlib
import re
import sys

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from football_europe_sources_v3 import (
    SOURCES,
    fetch,
    Parser,
)

ROOT = Path(__file__).resolve().parent

FIXTURES = (
    ROOT / "state/football_live/source/fixtures.csv"
)

LONDON = ZoneInfo("Europe/London")

ALIASES = {
    "Man United": "Manchester United",
    "Man City": "Manchester City",
    "Nott'm Forest": "Nottingham Forest",
    "Brighton": "Brighton & Hove Albion",
    "Tottenham": "Tottenham Hotspur",
    "Spurs": "Tottenham Hotspur",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
    "Bournemouth": "AFC Bournemouth",
    "Hull": "Hull City",
    "Coventry": "Coventry City",
    "Ipswich": "Ipswich Town",
    "Leeds": "Leeds United",
}

MONTHS = (
    "January February March April May June "
    "July August September October November December"
)

DATE_PATTERN = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|"
    r"Saturday|Sunday)\s+\d{1,2}\s+"
    r"(?:" + "|".join(MONTHS.split()) + r")"
    r"(?:\s+20\d{2})?\b",
    re.IGNORECASE,
)


def official_name(name):
    return ALIASES.get(name, name)


def read_fixtures():
    with FIXTURES.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        return [
            row for row in csv.DictReader(f)
            if row.get("Div") == "E0"
        ]


def download_calendar():
    status, content_type, raw = fetch(SOURCES["EPL"])

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Invalid official response")

    parser = Parser()
    parser.feed(raw.decode("utf-8", errors="replace"))

    text = " ".join(parser.parts)

    return text, hashlib.sha256(raw).hexdigest()


def find_section(text, day):
    heading = day.strftime("%A ") + str(day.day) + day.strftime(" %B")

    matches = [
        match
        for match in DATE_PATTERN.finditer(text)
        if match.group(0).lower() == heading.lower()
        or match.group(0).lower() == (
            heading + f" {day.year}"
        ).lower()
    ]

    if len(matches) != 1:
        return None, f"Date heading count: {len(matches)}"

    start = matches[0].end()

    next_heading = DATE_PATTERN.search(text, start)

    end = next_heading.start() if next_heading else len(text)

    return text[start:end], None


def compare_one(row, text):
    home = official_name(row["HomeTeam"])
    away = official_name(row["AwayTeam"])

    day = datetime.strptime(
        row["Date"], "%d/%m/%Y"
    ).date()

    section, error = find_section(text, day)

    if error:
        return "UNVERIFIED", error

    # Exact fixture identity, allowing an explicit UK kickoff.
    teams_pattern = (
        re.escape(home)
        + r"\s+v\s+"
        + re.escape(away)
        + r"\b"
    )

    team_hits = list(
        re.finditer(
            teams_pattern,
            section,
            re.IGNORECASE,
        )
    )

    if len(team_hits) != 1:
        return (
            "UNVERIFIED",
            f"Fixture occurrences: {len(team_hits)}"
        )

    # Only time immediately preceding this fixture is accepted.
    prefix = section[:team_hits[0].start()]

    time_match = re.search(
        r"(\d{1,2}:\d{2})"
        r"(?:\s+(?:GMT|BST))?\s*$",
        prefix,
        re.IGNORECASE,
    )

    if not time_match:
        return "UNVERIFIED", "No explicit kickoff time"

    official_time = time_match.group(1)

    try:
        official_dt = datetime.strptime(
            day.strftime("%d/%m/%Y") + " " + official_time,
            "%d/%m/%Y %H:%M",
        ).replace(tzinfo=LONDON)

        source_dt = datetime.strptime(
            row["Date"] + " " + row["Time"],
            "%d/%m/%Y %H:%M",
        ).replace(tzinfo=LONDON)

    except ValueError as exc:
        return "UNVERIFIED", str(exc)

    official_utc = official_dt.astimezone(timezone.utc)
    source_utc = source_dt.astimezone(timezone.utc)

    if official_utc != source_utc:
        return (
            "MISMATCH",
            f"official={official_utc.isoformat()} "
            f"football_data={source_utc.isoformat()}"
        )

    return "MATCH", official_utc.isoformat()


def main():
    print("=== BUBUS EPL CALENDAR V1 ===")
    print("MODE: READ ONLY")

    text, html_sha = download_calendar()

    print("Official HTML SHA256:", html_sha)

    rows = read_fixtures()

    print("EPL fixtures in CSV:", len(rows))

    now = datetime.now(timezone.utc)

    stats = {
        "MATCH": 0,
        "MISMATCH": 0,
        "UNVERIFIED": 0,
    }

    for row in sorted(
        rows,
        key=lambda r: (
            datetime.strptime(r["Date"], "%d/%m/%Y"),
            r.get("Time", ""),
        ),
    ):
        day = datetime.strptime(
            row["Date"], "%d/%m/%Y"
        ).date()

        kickoff = datetime.strptime(
            row["Date"] + " " + row["Time"],
            "%d/%m/%Y %H:%M",
        ).replace(tzinfo=LONDON).astimezone(timezone.utc)

        if kickoff <= now:
            continue

        status, detail = compare_one(row, text)

        stats[status] += 1

        print(
            f"{status:11}",
            day.isoformat(),
            row["HomeTeam"],
            "—",
            row["AwayTeam"],
            "|",
            detail,
        )

    print("\n=== SUMMARY ===")

    for status, count in stats.items():
        print(status + ":", count)

    print("\nIndependent archived evidence: NOT CREATED")
    print("Forecast recording: NOT AUTHORIZED")
    print("READ ONLY — NO FILES MODIFIED")

    if stats["MISMATCH"]:
        return 2

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "FAILED:",
            type(exc).__name__,
            str(exc)
        )
        sys.exit(2)
