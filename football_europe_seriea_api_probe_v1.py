#!/usr/bin/env python3
"""Bubus Serie A Official API Probe V1.

Inspect full-season matches endpoint.

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import json
import urllib.request
import urllib.error

from collections import Counter
from urllib.parse import quote


BASE = (
    "https://api-sdp.legaseriea.it/"
    "v1/serie-a/football"
)

SEASON_ID = (
    "serie-a::Football_Season::"
    "ed7fdc2a3e7b408b942ec177b7b956b5"
)

MAX_RESPONSE = 8_000_000


def describe(value):

    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": list(value.keys())[:30],
        }

    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
        }

    return {
        "type": type(value).__name__,
        "value": str(value)[:150],
    }


def main():

    print("=== BUBUS SERIE A API PROBE V1 ===")
    print("MODE: READ ONLY")

    encoded_season = quote(
        SEASON_ID,
        safe="",
    )

    url = (
        BASE
        + "/seasons/"
        + encoded_season
        + "/matches?locale=en-GB"
    )

    print("\n=== REQUEST ===")

    print("Season: 2026/2027")
    print("Endpoint: /seasons/{seasonId}/matches")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=40,
        ) as response:

            status = response.status

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            raw = response.read(
                MAX_RESPONSE + 1
            )

    except urllib.error.HTTPError as exc:

        print("HTTP ERROR:", exc.code)
        print("STATUS: API REQUEST FAILED")
        raise SystemExit(2)

    except Exception as exc:

        print(
            "CONNECTION ERROR:",
            type(exc).__name__,
            str(exc),
        )

        raise SystemExit(2)

    print("\n=== RESPONSE ===")

    print("HTTP:", status)
    print("Content-Type:", content_type)
    print("Bytes:", len(raw))

    if status != 200:
        raise RuntimeError(
            "Unexpected HTTP status"
        )

    if len(raw) > MAX_RESPONSE:
        raise RuntimeError(
            "API response exceeds size limit"
        )

    if "json" not in content_type.lower():
        raise RuntimeError(
            "Unexpected response type"
        )

    data = json.loads(raw)

    print("\n=== ROOT STRUCTURE ===")

    print("Type:", type(data).__name__)

    if isinstance(data, dict):

        print("Keys:", list(data.keys()))

        for key, value in data.items():

            print(
                key,
                ":",
                describe(value),
            )

    elif isinstance(data, list):

        print("Array length:", len(data))

    else:
        raise RuntimeError(
            "Unexpected JSON root"
        )

    print("\n=== MATCH COLLECTION DISCOVERY ===")

    candidates = []

    if isinstance(data, list):
        candidates.append(
            ("ROOT", data)
        )

    elif isinstance(data, dict):

        for key, value in data.items():

            if isinstance(value, list):
                candidates.append(
                    (key, value)
                )

            elif isinstance(value, dict):

                for subkey, subvalue in value.items():

                    if isinstance(subvalue, list):
                        candidates.append(
                            (
                                key + "." + subkey,
                                subvalue,
                            )
                        )

    match_candidates = []

    for name, items in candidates:

        if not items:
            continue

        if not isinstance(items[0], dict):
            continue

        keys = set(items[0].keys())

        if (
            {"home", "away"} <= keys
            or "matchDateUtc" in keys
        ):

            match_candidates.append(
                (name, items)
            )

    for name, matches in match_candidates:

        print("\nCollection:", name)
        print("Count:", len(matches))

        print("\n=== FIRST MATCH STRUCTURE ===")

        sample = matches[0]

        for key, value in sample.items():

            if key in (
                "home",
                "away",
                "matchSet",
                "season",
                "competition",
            ):

                print(
                    key,
                    ":",
                    describe(value),
                )

            elif isinstance(
                value,
                (str, int, float, bool, type(None)),
            ):

                print(
                    key,
                    ":",
                    str(value)[:150],
                )

            else:

                print(
                    key,
                    ":",
                    type(value).__name__,
                )

        print("\n=== DATE AND STATUS QUALITY ===")

        statuses = Counter()
        missing_dates = 0
        missing_ids = 0

        for match in matches:

            statuses[
                str(match.get("status"))
            ] += 1

            if not match.get("matchDateUtc"):
                missing_dates += 1

            if not match.get("id"):
                missing_ids += 1

        print("Matches:", len(matches))
        print("Missing dates:", missing_dates)
        print("Missing IDs:", missing_ids)

        print("\nStatuses:")

        for status, count in statuses.most_common():
            print(status, count)

        print("\n=== FIRST 5 MATCHES ===")

        for match in matches[:5]:

            home = match.get("home") or {}
            away = match.get("away") or {}

            print(
                "ID:", match.get("id"),
                "| Date:", match.get("matchDateUtc"),
                "| Status:", match.get("status"),
            )

            if isinstance(home, dict):
                print(
                    " HOME:",
                    home.get("displayName")
                    or home.get("name")
                    or home.get("shortName")
                )

            if isinstance(away, dict):
                print(
                    " AWAY:",
                    away.get("displayName")
                    or away.get("name")
                    or away.get("shortName")
                )

    print("\n=== FINAL STATUS ===")

    if not match_candidates:

        print("MATCH COLLECTION: NOT IDENTIFIED")
        print("Further JSON inspection required")

    else:

        total = max(
            len(items)
            for _, items in match_candidates
        )

        print("Largest match collection:", total)

        if total == 380:
            print("FULL SEASON: CANDIDATE FOUND")
        else:
            print("FULL SEASON: NOT YET CONFIRMED")

    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
