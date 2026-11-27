#!/usr/bin/env python3
"""Bubus LaLiga API audit V1.

Check API pagination, season, competition and match structure.
READ ONLY. No files or database modifications.
"""

import json
import re
import urllib.request
from urllib.parse import urlencode

from football_europe_sources_v3 import fetch, SOURCES


BASE_URL = (
    "https://apim.laliga.com/"
    "public-service/api/v1/matches"
)

PAGE_SIZE = 2
MAX_RESPONSE = 500_000


def get_key():
    status, content_type, raw = fetch(SOURCES["LaLiga"])

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official LaLiga page unavailable")

    html = raw.decode("utf-8", errors="replace")

    match = re.search(
        r'<script\b(?=[^>]*\bid=["\']__NEXT_DATA__["\'])'
        r'[^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    )

    if not match:
        raise RuntimeError("NEXT_DATA missing")

    data = json.loads(match.group(1))

    config = data.get("runtimeConfig") or {}
    key = config.get("backendSubscription")

    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("Subscription key missing")

    return key


def request_page(key, offset):
    params = {
        "subscription": "laliga-easports-2026",
        "competition": "primera-division",
        "limit": PAGE_SIZE,
        "offset": offset,
    }

    url = BASE_URL + "?" + urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://www.laliga.com",
            "Referer": SOURCES["LaLiga"],
            "Ocp-Apim-Subscription-Key": key,
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("Unexpected HTTP status")

        content_type = response.headers.get("Content-Type", "")

        if "json" not in content_type.lower():
            raise RuntimeError("Unexpected response content type")

        raw = response.read(MAX_RESPONSE + 1)

    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("API response too large")

    result = json.loads(raw)

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected API root")

    if not isinstance(result.get("matches"), list):
        raise RuntimeError("Missing matches array")

    return result


def describe(value):
    if value is None:
        return None

    if isinstance(value, dict):
        allowed = (
            "id", "name", "shortname", "short_name",
            "nickname", "slug", "opta_id",
            "year", "name_en",
        )

        return {
            key: value[key]
            for key in allowed
            if key in value
            and isinstance(value[key], (str, int, float, bool, type(None)))
        }

    if isinstance(value, list):
        return f"LIST ({len(value)} elements)"

    if isinstance(value, (str, int, float, bool)):
        return value

    return type(value).__name__


def main():
    print("=== BUBUS LALIGA API AUDIT V1 ===")
    print("MODE: READ ONLY")

    key = get_key()

    print("Subscription key: PRESENT (hidden)")

    pages = []

    for offset in (0, 2):
        data = request_page(key, offset)

        print(f"\n=== OFFSET {offset} ===")
        print("Total:", data.get("total"))
        print("Returned:", len(data["matches"]))

        ids = []

        for match in data["matches"]:
            if not isinstance(match, dict):
                raise RuntimeError("Invalid match object")

            ids.append(match.get("id"))

            fields = (
                "id",
                "name",
                "date",
                "status",
                "season",
                "competition",
                "gameweek",
                "home_team",
                "away_team",
                "venue",
            )

            summary = {
                field: describe(match.get(field))
                for field in fields
            }

            print(
                json.dumps(
                    summary,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )

        print("Page IDs:", ids)
        pages.append((data.get("total"), ids))

    print("\n=== PAGINATION ===")

    totals = [item[0] for item in pages]

    if totals[0] != totals[1]:
        raise RuntimeError("Total differs between pages")

    first_ids = pages[0][1]
    second_ids = pages[1][1]

    if not first_ids or not second_ids:
        raise RuntimeError("Empty page")

    if any(value is None for value in first_ids + second_ids):
        raise RuntimeError("Missing match ID")

    if len(set(first_ids + second_ids)) != (
        len(first_ids) + len(second_ids)
    ):
        raise RuntimeError(
            "Pagination returned duplicate match IDs"
        )

    print("Different match IDs: YES")
    print("Total consistent: YES")
    print("Pagination: BASIC CHECK PASSED")

    print("\n=== FINAL STATUS ===")
    print("Full-season retrieval: NOT YET TESTED")
    print("Season filtering: NOT YET CONFIRMED")
    print("Timezone interpretation: NOT YET CONFIRMED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
