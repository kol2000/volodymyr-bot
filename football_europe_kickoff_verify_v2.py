#!/usr/bin/env python3
"""Bubus Europe kickoff verification V2.

Verify four selected fixtures against official HTML pages.
Save immutable HTML evidence only before kickoff.

Does not modify forecasts or existing V1 evidence.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from football_europe_sources_probe_v1 import fetch, TextParser
import football_europe_snapshots_manage_v1 as manager

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "state/football_live"
V1 = BASE / "verified/kickoffs_v1.json"
DEST = BASE / "verified/kickoffs_v2"

LONDON = ZoneInfo("Europe/London")
MIN_LEAD = timedelta(minutes=60)

# Patterns are specific to the four inspected official pages.
# A generic time anywhere on a page is NOT sufficient.
RULES = {
    ("E0", "Liverpool", "Man United"):
        r"16:30\s*/\s*SUN\s+22\s+NOV\s+Liverpool\s+Manchester\s+United",

    ("I1", "Atalanta", "Inter"):
        r"22\s+November\s+at\s+8:45\s+PM\s+CET",

    ("E0", "Brentford", "Everton"):
        r"Monday\s+23\s+November\s+20:00\s+Brentford\s+v\s+Everton",

    ("SP1", "Levante", "Elche"):
        r"LUN\s+23\.11\.2026\s+21:00\s*h",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized_text(raw):
    parser = TextParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())


def fixture_lookup(rows, item):
    selected = [
        row for row in rows
        if row.get("Div") == item["div"]
        and row.get("Date") == item["date"]
        and row.get("HomeTeam") == item["home"]
        and row.get("AwayTeam") == item["away"]
    ]

    if len(selected) != 1:
        raise RuntimeError(
            f"Fixture identity ambiguous: {len(selected)} matches"
        )

    return selected[0]


def check_one(item, rows, snapshot_id, fixture_sha, write):
    label = f"{item['home']} — {item['away']}"
    print("\nMATCH:", label)

    row = fixture_lookup(rows, item)

    kickoff = datetime.strptime(
        row["Date"] + " " + row["Time"],
        "%d/%m/%Y %H:%M"
    ).replace(tzinfo=LONDON).astimezone(timezone.utc)

    expected = datetime.fromisoformat(
        item["kickoff_utc"]
    ).astimezone(timezone.utc)

    if kickoff != expected:
        raise RuntimeError("Fixture kickoff mismatch")

    key = (
        item["div"],
        item["home"],
        item["away"]
    )

    pattern = RULES.get(key)

    if not pattern:
        raise RuntimeError("No official-page verification rule")

    now = datetime.now(timezone.utc)

    if kickoff <= now + MIN_LEAD:
        raise RuntimeError("Too late for new pre-match verification")

    print("Official source:", item["url"])

    raw = fetch(item["url"])
    fetched = datetime.now(timezone.utc)

    if kickoff <= fetched + MIN_LEAD:
        raise RuntimeError("Verification finished too close to kickoff")

    text = normalized_text(raw)

    matches = list(
        re.finditer(pattern, text, flags=re.IGNORECASE)
    )

    if not matches:
        raise RuntimeError("Official kickoff evidence not found")

    hit = matches[0]

    context = text[
        max(0, hit.start() - 100):
        min(len(text), hit.end() + 150)
    ]

    # Additional team check for pages with a separate time block.
    if key == ("I1", "Atalanta", "Inter"):
        if not re.search(
            r"Atalanta\s+vs\.?\s+Inter",
            text,
            re.IGNORECASE
        ) and not re.search(
            r"Atalanta\s+against\s+Inter",
            text,
            re.IGNORECASE
        ):
            raise RuntimeError("Inter fixture identity not confirmed")

    if key == ("SP1", "Levante", "Elche"):
        if not re.search(
            r"Levante\s+UD\s+VS\s+Elche\s+CF",
            text,
            re.IGNORECASE
        ):
            raise RuntimeError("LaLiga fixture identity not confirmed")

    html_sha = sha(raw)

    print("STATUS: VERIFIED")
    print("Kickoff UTC:", kickoff.isoformat())
    print("HTML SHA256:", html_sha)
    print("Evidence:", context)

    payload = {
        "schema": "bubus-europe-kickoff-v2",
        "competition": item["div"],
        "date": item["date"],
        "home": item["home"],
        "away": item["away"],
        "kickoff_utc": kickoff.isoformat(),
        "snapshot_id": snapshot_id,
        "fixtures_sha256": fixture_sha,
        "official_url": item["url"],
        "official_html_sha256": html_sha,
        "fetched_utc": fetched.isoformat(),
        "source_timezone": "Europe/London",
        "matched_pattern": pattern,
        "matched_context": context,
        "status": "verified",
    }

    if not write:
        print("MODE: READ ONLY")
        return

    identity = sha(
        json.dumps(
            [
                snapshot_id,
                item["div"],
                item["date"],
                item["home"],
                item["away"],
                item["kickoff_utc"]
            ],
            ensure_ascii=False,
            separators=(",", ":")
        ).encode("utf-8")
    )

    destination = DEST / identity

    if destination.exists():
        existing = json.loads(
            (destination / "evidence.json").read_text(
                encoding="utf-8"
            )
        )
        stored = (destination / "official.html").read_bytes()

        if (
            existing.get("schema") != payload["schema"]
            or existing.get("snapshot_id") != snapshot_id
            or existing.get("kickoff_utc") != kickoff.isoformat()
            or existing.get("official_html_sha256") != sha(stored)
        ):
            raise RuntimeError("Existing evidence is inconsistent")

        print("Evidence already exists")
        return

    DEST.mkdir(parents=True, exist_ok=True)

    temporary = Path(
        tempfile.mkdtemp(prefix=".staging-", dir=DEST)
    )

    try:
        (temporary / "official.html").write_bytes(raw)

        (temporary / "evidence.json").write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        os.rename(temporary, destination)
        print("EVIDENCE SAVED:", destination)

    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    data = json.loads(V1.read_text(encoding="utf-8"))
    snapshot_id = data["snapshot_id"]
    snapshot = BASE / "snapshots" / snapshot_id

    manager.validate(snapshot)

    fixture_path = snapshot / "fixtures.csv"
    fixture_sha = sha(fixture_path.read_bytes())

    if fixture_sha != data["fixtures_sha256"]:
        raise RuntimeError("Fixture SHA256 mismatch")

    with fixture_path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    print("=== BUBUS EUROPE KICKOFF VERIFY V2 ===")
    print("Mode:", "WRITE" if args.write else "READ ONLY")

    passed = 0
    failed = 0

    for item in data["matches"]:
        try:
            check_one(
                item, rows, snapshot_id, fixture_sha, args.write
            )
            passed += 1
        except Exception as exc:
            failed += 1
            print(
                "UNVERIFIED:",
                item["home"], "—", item["away"],
                "|", type(exc).__name__, str(exc)
            )

    print("\n=== SUMMARY ===")
    print("Verified this run:", passed)
    print("Unverified:", failed)
    print("Forecast recording: NOT AUTHORIZED")

    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
