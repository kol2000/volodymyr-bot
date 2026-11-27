#!/usr/bin/env python3
"""Bubus Serie A Season Probe V1.

Identify 2026/27 competition-season relationships.

READ ONLY.
No archive writes.
No forecast recording.
"""

import re
import json
from html import unescape

from football_europe_sources_v3 import (
    fetch,
    SOURCES,
)


def normalize(text):
    return unescape(text).replace(
        '\\"', '"'
    ).replace(
        "\\/", "/"
    )


def excerpt(text, position, before=400, after=500):

    start = max(0, position - before)
    end = min(len(text), position + after)

    fragment = text[start:end]

    # Hide possible credentials.
    fragment = re.sub(
        r'(?i)(api[_-]?key|token|secret|password|'
        r'subscription[_-]?key)"?\s*:\s*"[^"]+"',
        r'\1":"[REDACTED]"',
        fragment,
    )

    return fragment.replace("\n", " ")


def main():

    print("=== BUBUS SERIE A SEASON PROBE V1 ===")
    print("MODE: READ ONLY")

    status, content_type, raw = fetch(
        SOURCES["SerieA"]
    )

    if status != 200:
        raise RuntimeError("Official source unavailable")

    text = normalize(
        raw.decode("utf-8", errors="replace")
    )

    print("\n=== SEASON 2026/2027 CONTEXT ===")

    for index, match in enumerate(
        re.finditer(
            r'2026/2027',
            text,
            re.I,
        ),
        1,
    ):
        print(f"\n--- SEASON OCCURRENCE {index} ---")

        print(
            excerpt(
                text,
                match.start(),
                before=550,
                after=600,
            )
        )

    print("\n=== SEASON IDENTIFIERS ===")

    ids = (
        "season-ed7fdc2a3e7b408b942ec177b7b956b5",
        "season-833a804da44f489a8b12fe256d01d025",
    )

    for season_id in ids:

        matches = list(
            re.finditer(
                re.escape(season_id),
                text,
            )
        )

        print(
            "\nSEASON:",
            season_id,
        )

        print(
            "Occurrences:",
            len(matches),
        )

        for match in matches[:2]:
            print(
                excerpt(
                    text,
                    match.start(),
                    before=250,
                    after=300,
                )
            )

    print("\n=== CALENDAR COMPONENT CONTEXT ===")

    markers = (
        '"competitionSelection"',
        '"seasonIds"',
        '"matchdayId"',
        '"competitionId"',
        '"canonicalCompetitionId"',
    )

    for marker in markers:

        matches = list(
            re.finditer(
                re.escape(marker),
                text,
                re.I,
            )
        )

        print(
            f"\n{marker}: {len(matches)}"
        )

        for match in matches[:2]:

            print(
                excerpt(
                    text,
                    match.start(),
                    before=180,
                    after=350,
                )
            )

    print("\n=== SPORT API CONFIGURATION ===")

    for marker in (
        "api-sdp.legaseriea.it",
        "API_SDP",
        "SDP_API",
    ):

        matches = list(
            re.finditer(
                re.escape(marker),
                text,
                re.I,
            )
        )

        print(
            marker,
            "occurrences:",
            len(matches),
        )

        # Do not print neighboring configuration secrets.

    print("\n=== FINAL STATUS ===")
    print("SEASON CONTEXT INSPECTED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
