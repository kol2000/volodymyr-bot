#!/usr/bin/env python3
"""Bubus Europe official league source discovery V3.

Read-only.
Does not modify forecasts, snapshots or evidence.
"""

import hashlib
import re
import urllib.request
from html.parser import HTMLParser


SOURCES = {
    "EPL": (
        "https://www.premierleague.com/en/news/"
        "4675097/all-380-fixtures-for-the-202627-premier-league-season"
    ),
    "LaLiga": (
        "https://www.laliga.com/en-GB/"
        "laliga-easports/calendar"
    ),
    "Bundesliga": (
        "https://www.bundesliga.com/en/"
        "bundesliga/matchday/schedule"
    ),
    "SerieA": (
        "https://en.legaseriea.it/"
        "serie-a/fixtures-results"
    ),
    "Ligue1": (
        "https://ligue1.com/en/"
        "articles/l1_article_5292-the-2026-27-"
        "ligue-1-mcdonald-s-calendar-is-released"
    ),
}

MAX_BYTES = 3_000_000


class Parser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


def fetch(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:

        status = response.status
        content_type = response.headers.get(
            "Content-Type", ""
        )

        raw = response.read(MAX_BYTES + 1)

        if len(raw) > MAX_BYTES:
            raise RuntimeError("Response too large")

        return status, content_type, raw


def analyze(league, url):

    print("\n" + "=" * 65)
    print("LEAGUE:", league)
    print("URL:", url)

    try:
        status, content_type, raw = fetch(url)

        print("HTTP:", status)
        print("Content-Type:", content_type)
        print("Bytes:", len(raw))
        print(
            "SHA256:",
            hashlib.sha256(raw).hexdigest()
        )

        if "html" not in content_type.lower():
            print("STATUS: NON-HTML RESPONSE")
            return

        parser = Parser()

        parser.feed(
            raw.decode("utf-8", errors="replace")
        )

        text = " ".join(parser.parts)

        print("Visible text length:", len(text))

        patterns = {
            "clock_times": r"\b\d{1,2}:\d{2}\b",
            "dates": (
                r"\b\d{1,2}[./]\d{1,2}[./]2026\b"
                r"|\b(?:November|December|November|"
                r"novembre|noviembre)\b"
            ),
            "timezone": (
                r"\b(?:GMT|BST|UTC|CET|CEST)\b"
            ),
        }

        for name, pattern in patterns.items():

            matches = list(
                re.finditer(
                    pattern,
                    text,
                    re.IGNORECASE
                )
            )

            print(
                name,
                "matches:",
                len(matches)
            )

            for match in matches[:3]:

                start = max(0, match.start() - 70)
                end = min(
                    len(text),
                    match.end() + 100
                )

                print(
                    " ",
                    text[start:end]
                )

        print("STATUS: SOURCE RETRIEVED")
        print("MATCH VERIFICATION: NOT PERFORMED")

    except Exception as exc:

        print(
            "STATUS: SOURCE ERROR",
            type(exc).__name__,
            str(exc)
        )


def main():

    print("=== BUBUS EUROPE SOURCES V3 ===")

    for league, url in SOURCES.items():
        analyze(league, url)

    print("\n=== FINAL STATUS ===")
    print("MODE: READ ONLY")
    print("FORECAST RECORDING: BLOCKED")
    print("EXISTING EVIDENCE: UNCHANGED")


if __name__ == "__main__":
    main()
