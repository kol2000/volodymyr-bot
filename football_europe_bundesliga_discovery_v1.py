#!/usr/bin/env python3
"""Bubus Bundesliga Source Discovery V1.

Inspect official Bundesliga fixture pages.

STRICTLY READ ONLY.
No archive or forecast writes.
"""

import re
import urllib.request
from html import unescape
from collections import Counter

URLS = {
    "schedule":
        "https://www.bundesliga.com/en/bundesliga/matchday/schedule",

    "matchday_1":
        "https://www.bundesliga.com/en/bundesliga/matchday/2026-2027/1",

    "matchday_18":
        "https://www.bundesliga.com/en/bundesliga/matchday/2026-2027/18",

    "matchday_34":
        "https://www.bundesliga.com/en/bundesliga/matchday/2026-2027/34",
}

PATTERNS = {
    "NEXT_DATA": r"__NEXT_DATA__",
    "NUXT": r"__NUXT__",
    "REACT_FLIGHT": r"self\.__next_f",
    "JSON_LD": r"application/ld\+json",
    "MATCHES": r'"matches"\s*:',
    "FIXTURES": r'"fixtures"\s*:',
    "MATCHDAY": r"matchday",
    "API": r"/api/",
    "GRAPHQL": r"graphql",
    "2026_2027": r"2026.{0,3}2027",
}

MAX_SIZE = 5_000_000


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
        },
    )

    with urllib.request.urlopen(
        req,
        timeout=35,
    ) as response:

        content_type = response.headers.get(
            "Content-Type", ""
        )

        raw = response.read(MAX_SIZE + 1)

        if len(raw) > MAX_SIZE:
            raise RuntimeError(
                "Response exceeds size limit"
            )

        return response.status, content_type, raw


def main():
    print("=== BUBUS BUNDESLIGA DISCOVERY V1 ===")
    print("MODE: READ ONLY")

    for label, url in URLS.items():

        print("\n=== SOURCE:", label, "===")
        print("URL:", url)

        try:
            status, ctype, raw = fetch(url)

        except Exception as exc:
            print(
                "ERROR:",
                type(exc).__name__,
                str(exc)[:200],
            )
            continue

        print("HTTP:", status)
        print("Content-Type:", ctype)
        print("Bytes:", len(raw))

        html = raw.decode(
            "utf-8",
            errors="replace",
        )

        print("\nDATA MARKERS:")

        for name, pattern in PATTERNS.items():
            count = len(re.findall(
                pattern,
                html,
                re.IGNORECASE,
            ))

            print(f"  {name:15} {count}")

        print("\nSCRIPT BUNDLES:")

        scripts = re.findall(
            r'<script\b[^>]*src=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )

        print("Total:", len(scripts))

        for item in scripts[:12]:
            print(" ", item[:180])

        print("\nPOSSIBLE MATCH FIELD NAMES:")

        fields = Counter(
            re.findall(
                r'"([A-Za-z][A-Za-z0-9_]{2,60})"\s*:',
                html,
            )
        )

        keywords = (
            "match",
            "fixture",
            "season",
            "team",
            "kickoff",
            "date",
            "round",
            "score",
        )

        selected = [
            (name, count)
            for name, count in fields.items()
            if any(
                word in name.lower()
                for word in keywords
            )
        ]

        for name, count in sorted(
            selected,
            key=lambda x: -x[1],
        )[:25]:
            print(f"  {name:40} {count}")

    print("\n=== FINAL STATUS ===")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
