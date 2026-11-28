#!/usr/bin/env python3
"""Bubus European football source update V1.

Download and compare five current-season CSV files
and the upcoming fixtures CSV.

READ ONLY. No existing files are modified.
"""

import csv
import hashlib
import io
import urllib.request
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "state/football_live/source"

SEASON = "2627"

LEAGUES = {
    "EPL": "E0",
    "LaLiga": "SP1",
    "Bundesliga": "D1",
    "SerieA": "I1",
    "Ligue1": "F1",
}

BASE_URL = (
    "https://www.football-data.co.uk"
)

MAX_BYTES = 3_000_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache",
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        raw = response.read(MAX_BYTES + 1)

    if len(raw) > MAX_BYTES:
        raise RuntimeError(
            "Downloaded CSV exceeds size limit"
        )

    return raw


def parse(raw):
    text = raw.decode(
        "utf-8-sig",
        errors="strict"
    )

    reader = csv.DictReader(
        io.StringIO(text)
    )

    rows = list(reader)

    if not reader.fieldnames:
        raise RuntimeError(
            "Missing CSV header"
        )

    return rows


def result_index(rows, code):

    result = {}

    for row in rows:

        if row.get("Div") != code:
            raise RuntimeError(
                f"Unexpected division: {row.get('Div')}"
            )

        day = row.get("Date", "").strip()

        home = row.get("HomeTeam", "").strip()
        away = row.get("AwayTeam", "").strip()

        gh = row.get("FTHG", "").strip()
        ga = row.get("FTAG", "").strip()

        if not day or not home or not away:
            raise RuntimeError(
                "Incomplete match identity"
            )

        datetime.strptime(day, "%d/%m/%Y")

        if not gh and not ga:
            continue

        if not (
            gh.isdigit()
            and ga.isdigit()
            and 0 <= int(gh) <= 15
            and 0 <= int(ga) <= 15
        ):
            raise RuntimeError(
                f"Invalid score: {home} {gh}:{ga} {away}"
            )

        expected = (
            "H" if int(gh) > int(ga)
            else "A" if int(gh) < int(ga)
            else "D"
        )

        actual = row.get("FTR", "").strip()

        if actual not in ("", expected):
            raise RuntimeError(
                f"Result conflict: {home} — {away}"
            )

        key = (day, home, away)

        if key in result:
            raise RuntimeError(
                f"Duplicate match: {key}"
            )

        result[key] = (int(gh), int(ga))

    return result


def compare_league(league, code):

    url = (
        f"{BASE_URL}/mmz4281/"
        f"{SEASON}/{code}.csv"
    )

    local_path = (
        SOURCE / f"{league}_2026-27.csv"
    )

    print("\n" + "=" * 65)
    print("LEAGUE:", league)
    print("URL:", url)

    if not local_path.exists():
        raise RuntimeError(
            f"Local file missing: {local_path}"
        )

    old_raw = local_path.read_bytes()
    new_raw = fetch(url)

    old_rows = parse(old_raw)
    new_rows = parse(new_raw)

    old = result_index(old_rows, code)
    new = result_index(new_rows, code)

    removed = set(old) - set(new)
    added = set(new) - set(old)

    changed = {
        key for key in set(old) & set(new)
        if old[key] != new[key]
    }

    print("Local results:", len(old))
    print("Remote results:", len(new))

    print("New matches:", len(added))
    print("Missing old matches:", len(removed))
    print("Changed scores:", len(changed))

    print("Local SHA256:", sha(old_raw))
    print("Remote SHA256:", sha(new_raw))

    for key in sorted(added)[:15]:
        print("NEW:", key, new[key])

    for key in sorted(changed):
        print(
            "CHANGED:",
            key,
            old[key],
            "=>",
            new[key]
        )

    for key in sorted(removed):
        print("REMOVED:", key)

    if removed or changed:
        status = "CONFLICT"
    elif added:
        status = "NEW RESULTS"
    elif sha(old_raw) == sha(new_raw):
        status = "IDENTICAL"
    else:
        status = "METADATA CHANGED"

    print("STATUS:", status)

    return status


def compare_fixtures():

    print("\n" + "=" * 65)
    print("FIXTURES")

    url = (
        f"{BASE_URL}/fixtures.csv"
    )

    local_path = SOURCE / "fixtures.csv"

    raw = fetch(url)
    # Reject unexpected HTML or empty responses.
    if not raw.strip():
        raise RuntimeError("Empty fixture response")

    if raw.lstrip()[:100].lower().startswith(
        (b"<!doctype", b"<html")
    ):
        raise RuntimeError(
            "Server returned HTML instead of CSV"
        )

    rows = parse(raw)

    required = {
        "Div", "Date", "Time",
        "HomeTeam", "AwayTeam"
    }

    fields = set(rows[0]) if rows else set()

    if not required.issubset(fields):
        raise RuntimeError(
            "Unexpected fixture CSV schema"
        )

    old_raw = local_path.read_bytes()

    print("Remote rows:", len(rows))
    print("Local SHA256:", sha(old_raw))
    print("Remote SHA256:", sha(raw))

    if sha(old_raw) == sha(raw):
        print("STATUS: IDENTICAL")
    else:
        print("STATUS: CHANGED")

    return rows


def main():

    print("=== BUBUS EUROPE UPDATE V1 ===")
    print("MODE: READ ONLY")

    statuses = {}

    for league, code in LEAGUES.items():

        try:
            statuses[league] = (
                compare_league(league, code)
            )

        except Exception as exc:
            statuses[league] = "ERROR"

            print(
                "ERROR:",
                league,
                type(exc).__name__,
                str(exc)
            )

    try:
        fixtures = compare_fixtures()
        fixtures_status = "OK"

    except Exception as exc:
        fixtures_status = "ERROR"
        fixtures = []

        print(
            "FIXTURE ERROR:",
            type(exc).__name__,
            str(exc)
        )

    print("\n=== SUMMARY ===")

    for league, status in statuses.items():
        print(f"{league}: {status}")

    print("Fixtures:", fixtures_status)
    print("Remote fixture rows:", len(fixtures))

    print("\nREAD ONLY")
    print("Existing CSV files: untouched")
    print("Forecast journal: untouched")

    if (
        "ERROR" in statuses.values()
        or "CONFLICT" in statuses.values()
        or fixtures_status == "ERROR"
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
