#!/usr/bin/env python3
"""Bubus Serie A Data Audit V1.

Inspect official Next.js HTML and embedded data.

READ ONLY.
No archive writes.
No forecast recording.
No additional API requests.
"""

import re
from collections import Counter
from html import unescape
from urllib.parse import urlsplit

from football_europe_sources_v3 import (
    fetch,
    SOURCES,
)


KEYWORDS = (
    "fixture",
    "match",
    "gameweek",
    "matchday",
    "calendar",
    "season",
    "competition",
    "round",
    "schedule",
)

API_HOSTS = (
    "api-sdp.legaseriea.it",
    "dapi.legaseriea.it",
)


def clean(text):
    return unescape(text).replace(
        "\\/", "/"
    ).replace(
        '\\"', '"'
    )


def safe_url(value):
    """Print URL paths, never query strings or credentials."""

    value = clean(value).strip()

    parts = urlsplit(value)

    if parts.hostname not in API_HOSTS:
        return None

    return (
        parts.scheme
        + "://"
        + parts.hostname
        + parts.path
    )


def main():

    print("=== BUBUS SERIE A DATA AUDIT V1 ===")
    print("MODE: READ ONLY")

    url = SOURCES["SerieA"]

    status, content_type, raw = fetch(url)

    if status != 200:
        raise RuntimeError(
            f"Official source returned HTTP {status}"
        )

    if "html" not in content_type.lower():
        raise RuntimeError("Unexpected content type")

    html = raw.decode(
        "utf-8",
        errors="replace",
    )

    normalized = clean(html)

    print("\n=== SOURCE ===")
    print("HTTP:", status)
    print("HTML bytes:", len(raw))

    print("\n=== NEXT.JS SERVER COMPONENTS ===")

    chunks = re.findall(
        r'self\.__next_f\.push\s*\(',
        html,
    )

    print("RSC push markers:", len(chunks))

    print("\n=== SPORTS DATA KEYWORDS ===")

    for word in KEYWORDS:

        count = len(re.findall(
            re.escape(word),
            normalized,
            re.I,
        ))

        print(f"{word:15} {count}")

    print("\n=== POSSIBLE DATA FIELD NAMES ===")

    field_patterns = (
        r'"([A-Za-z][A-Za-z0-9_]{2,60})"\s*:',
        r'\\"([A-Za-z][A-Za-z0-9_]{2,60})\\"\s*:',
    )

    fields = Counter()

    for pattern in field_patterns:

        for match in re.finditer(pattern, html):

            name = match.group(1)

            if any(
                word in name.lower()
                for word in KEYWORDS
            ):
                fields[name] += 1

    for name, count in fields.most_common(60):

        print(
            f"{name:45} {count}"
        )

    print("\n=== SEASON 2026/2027 TAGS ===")

    pattern = re.compile(
        r'https://dapi\.legaseriea\.it/'
        r'v2/content/[^"\\\s<>]*?'
        r'/tags/season-[a-f0-9]+',
        re.I,
    )

    season_urls = sorted(set(
        clean(match.group(0))
        for match in pattern.finditer(normalized)
    ))

    print("Season-tag URLs:", len(season_urls))

    for item in season_urls:
        print(item)

    print("\n=== API URLS IN HTML ===")

    url_pattern = re.compile(
        r'https?://[^\s"<>\\]+',
        re.I,
    )

    urls = set()

    for match in url_pattern.finditer(normalized):

        candidate = safe_url(match.group(0))

        if candidate:
            urls.add(candidate)

    print("Distinct API URLs:", len(urls))

    for item in sorted(urls):

        if any(
            word in item.lower()
            for word in (
                "fixture",
                "match",
                "calendar",
                "competition",
                "season",
                "round",
            )
        ):
            print(item[:250])

    print("\n=== PAGE STRUCTURE MARKERS ===")

    markers = (
        "fixtures-results",
        "api-sdp.legaseriea.it",
        "2026/2027",
        "2026-2027",
        "season-ed7fdc2a",
        "season-833a804d",
    )

    for marker in markers:

        count = normalized.lower().count(
            marker.lower()
        )

        print(
            f"{marker:35} {count}"
        )

    print("\n=== SCRIPT BUNDLES ===")

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)',
        html,
        re.I,
    )

    print("Total:", len(scripts))

    for item in scripts:

        if (
            "/app/" in item
            or "/layout-" in item
            or "/page-" in item
        ):
            print(item[:200])

    print("\n=== FINAL STATUS ===")
    print("HTML STRUCTURE INSPECTED")
    print("NO ADDITIONAL API REQUESTS")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
