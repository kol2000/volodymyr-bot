#!/usr/bin/env python3
"""Bubus EPL calendar V3 — strict kickoff extraction.

READ ONLY. No writes to forecasts, snapshots or evidence.
"""

import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import football_europe_epl_calendar_v2 as v2

LONDON = ZoneInfo("Europe/London")

# Only a clock (and optional timezone) can precede the team name.
# Remove a single broadcaster annotation left after the prior game.
PREFIX_RE = re.compile(
    r"^\s*(?:\([^()]*\)\s*)?"
    r"(?P<time>(?:[01]?\d|2[0-3]):[0-5]\d)"
    r"(?:\s+(?P<zone>GMT|BST))?\s*$",
    re.I,
)

UNTIMED_PREFIX_RE = re.compile(
    r"^\s*(?:\([^()]*\)\s*)?$"
)


def extract(text, now):
    headings = list(v2.DATE_RE.finditer(text))
    records = []
    skipped = []

    for i, heading in enumerate(headings):
        try:
            date = v2.parse_day(heading)
        except ValueError:
            continue

        start = heading.end()
        end = (
            headings[i + 1].start()
            if i + 1 < len(headings)
            else len(text)
        )

        section = text[start:end]
        matches = list(v2.MATCH_RE.finditer(section))

        previous_end = 0

        for match in matches:
            home = v2.canonical(match.group("home"))
            away = v2.canonical(match.group("away"))

            # Only text between the previous fixture and this fixture.
            prefix = section[previous_end:match.start()]
            previous_end = match.end()

            timed = PREFIX_RE.fullmatch(prefix)

            if timed is None:
                if UNTIMED_PREFIX_RE.fullmatch(prefix):
                    reason = "no explicit time"
                else:
                    reason = "ambiguous prefix"

                if date >= now.date():
                    skipped.append((date, home, away, reason))

                continue

            clock = timed.group("time")
            zone = timed.group("zone")

            hours, minutes = map(int, clock.split(":"))

            local = datetime(
                date.year,
                date.month,
                date.day,
                hours,
                minutes,
                tzinfo=LONDON,
            )

            # Check any explicitly printed GMT/BST label.
            if zone:
                actual = local.tzname()
                if zone.upper() != actual:
                    skipped.append((
                        date, home, away,
                        f"timezone conflict: {zone} vs {actual}"
                    ))
                    continue

            utc = local.astimezone(timezone.utc)

            if utc <= now:
                continue

            records.append({
                "date": date.isoformat(),
                "home": home,
                "away": away,
                "kickoff_utc": utc.isoformat(),
            })

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
        ),
    ), skipped


def main():
    print("=== BUBUS EPL CALENDAR V3 ===")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=14)

    text = v2.download()

    records, skipped = extract(text, now)

    upcoming = [
        r for r in records
        if datetime.fromisoformat(r["kickoff_utc"]) <= horizon
    ]

    untimed = [
        s for s in skipped
        if now.date() <= s[0] <= horizon.date()
    ]

    print("Timed fixtures:", len(upcoming))
    print("Untimed / ambiguous:", len(untimed))

    print("\n=== TIMED MATCHES ===")

    for r in upcoming:
        print(
            r["kickoff_utc"],
            "|",
            r["home"],
            "—",
            r["away"],
        )

    print("\n=== UNTIMED / AMBIGUOUS ===")

    for date, home, away, reason in untimed:
        print(
            date,
            "|",
            home,
            "—",
            away,
            "|",
            reason,
        )

    print("\n=== NOVEMBER 28 CHECK ===")

    targets = [
        r for r in upcoming
        if r["date"] == "2026-11-28"
    ]

    for r in targets:
        print(
            "TIMED:",
            r["home"],
            "—",
            r["away"],
            "|",
            r["kickoff_utc"],
        )

    for date, home, away, reason in untimed:
        if date.isoformat() == "2026-11-28":
            print("UNTIMED:", home, "—", away)

    print("\nREAD ONLY")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
