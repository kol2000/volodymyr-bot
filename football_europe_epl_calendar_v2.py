#!/usr/bin/env python3
"""Bubus EPL Calendar V2.

Discover upcoming EPL fixtures from the official calendar.

READ ONLY.
No forecast recording.
No modification of existing evidence or snapshots.

Only explicitly published kickoff times are extracted.
"""

import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from football_europe_sources_v3 import SOURCES, fetch, Parser

YEAR = 2026
LONDON = ZoneInfo("Europe/London")

MONTHS = (
    "January February March April May June "
    "July August September October November December"
).split()

DAYS = (
    "Monday Tuesday Wednesday Thursday "
    "Friday Saturday Sunday"
).split()

# Canonical names and commonly used abbreviations on the PL site.
TEAMS = [
    "Arsenal",
    "Aston Villa",
    "AFC Bournemouth",
    "Bournemouth",
    "Brentford",
    "Brighton & Hove Albion",
    "Brighton",
    "Burnley",
    "Chelsea",
    "Coventry City",
    "Coventry",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Hull City",
    "Hull",
    "Ipswich Town",
    "Ipswich",
    "Leeds United",
    "Leeds",
    "Leicester City",
    "Liverpool",
    "Manchester City",
    "Man City",
    "Manchester United",
    "Man Utd",
    "Newcastle United",
    "Newcastle",
    "Nottingham Forest",
    "Nott'm Forest",
    "Sunderland",
    "Tottenham Hotspur",
    "Tottenham",
    "Spurs",
    "West Ham United",
    "West Ham",
    "Wolverhampton Wanderers",
    "Wolves",
]

ALIASES = {
    "Bournemouth": "AFC Bournemouth",
    "Brighton": "Brighton & Hove Albion",
    "Coventry": "Coventry City",
    "Hull": "Hull City",
    "Ipswich": "Ipswich Town",
    "Leeds": "Leeds United",
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Spurs": "Tottenham Hotspur",
    "Tottenham": "Tottenham Hotspur",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
}

DATE_RE = re.compile(
    r"\b(?P<weekday>" + "|".join(DAYS) + r")"
    r"\s+(?P<day>\d{1,2})"
    r"\s+(?P<month>" + "|".join(MONTHS) + r")"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.I,
)

TEAM_RE = (
    "(?:" +
    "|".join(
        re.escape(t)
        for t in sorted(TEAMS, key=len, reverse=True)
    ) +
    ")"
)

MATCH_RE = re.compile(
    r"(?P<home>" + TEAM_RE + r")"
    r"\s+v\s+"
    r"(?P<away>" + TEAM_RE + r")"
    r"(?=\s|$)",
    re.I,
)

TIME_RE = re.compile(
    r"(?P<time>\d{1,2}:\d{2})"
    r"(?:\s+(?:GMT|BST))?\s*$",
    re.I,
)

def canonical(name):
    for item in TEAMS:
        if item.casefold() == name.casefold():
            return ALIASES.get(item, item)
    return name


def download():
    status, content_type, raw = fetch(SOURCES["EPL"])

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official EPL calendar unavailable")

    parser = Parser()
    parser.feed(raw.decode("utf-8", errors="replace"))

    return " ".join(parser.parts)


def parse_day(heading):
    day = int(heading.group("day"))
    month = MONTHS.index(heading.group("month").title()) + 1

    # Premier League season 2026/27.
    year = (
        int(heading.group("year"))
        if heading.group("year")
        else 2026 if month >= 8 else 2027
    )

    date = datetime(year, month, day).date()

    if date.strftime("%A").casefold() != (
        heading.group("weekday").casefold()
    ):
        raise ValueError("Date and weekday disagree")

    return date


def extract(text, now):
    headings = list(DATE_RE.finditer(text))
    records = []
    skipped = []

    for index, heading in enumerate(headings):
        try:
            date = parse_day(heading)
        except ValueError:
            continue

        start = heading.end()
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(text)
        )

        section = text[start:end]
        matches = list(MATCH_RE.finditer(section))

        for match in matches:
            home = canonical(match.group("home"))
            away = canonical(match.group("away"))

            # Use the text after the preceding fixture, not the
            # entire day. This prevents inheriting another game's time.
            position = section.rfind(
                ")", 0, match.start()
            )
            before = section[
                max(0, position + 1):match.start()
            ]

            # Reject if another fixture occurs in the prefix.
            if MATCH_RE.search(before):
                skipped.append((date, home, away, "no explicit time"))
                continue

            found = TIME_RE.search(before)

            if not found:
                skipped.append((date, home, away, "no explicit time"))
                continue

            clock = found.group("time")

            try:
                hours, minutes = map(int, clock.split(":"))

                local = datetime(
                    date.year, date.month, date.day,
                    hours, minutes,
                    tzinfo=LONDON,
                )

                utc = local.astimezone(timezone.utc)
            except ValueError:
                skipped.append((date, home, away, "invalid time"))
                continue

            if utc <= now:
                continue

            records.append({
                "date": date.isoformat(),
                "home": home,
                "away": away,
                "kickoff_utc": utc.isoformat(),
            })

    # Fail closed on duplicate identities.
    keys = [
        (r["date"], r["home"], r["away"])
        for r in records
    ]

    if len(keys) != len(set(keys)):
        raise RuntimeError("Duplicate official fixtures")

    return sorted(
        records,
        key=lambda r: (
            r["kickoff_utc"],
            r["home"],
            r["away"],
        )
    ), skipped


def main():
    print("=== BUBUS EPL CALENDAR V2 ===")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=14)

    text = download()

    records, skipped = extract(text, now)

    upcoming = [
        r for r in records
        if datetime.fromisoformat(r["kickoff_utc"]) <= horizon
    ]

    print("Current UTC:", now.isoformat())
    print("Horizon: 14 days")
    print("Official timed fixtures:", len(records))
    print("Within 14 days:", len(upcoming))

    print("\n=== UPCOMING OFFICIAL EPL MATCHES ===")

    for item in upcoming:
        print(
            item["kickoff_utc"],
            "|",
            item["home"],
            "—",
            item["away"],
        )

    print("\n=== UNTIMED FIXTURES (SAMPLE) ===")

    for date, home, away, reason in skipped[:12]:
        if date >= now.date():
            print(date, home, "—", away, "|", reason)

    print("\n=== STATUS ===")
    print("Calendar discovery: COMPLETED")
    print("Official timing: NOT YET ACCEPTED")
    print("Independent evidence: NOT CREATED")
    print("Forecast recording: BLOCKED")
    print("READ ONLY")


if __name__ == "__main__":
    main()
