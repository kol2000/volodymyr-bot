#!/usr/bin/env python3
"""Independent kickoff evidence for selected European matches."""

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import football_europe_snapshots_manage_v1 as manager

ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live"

SNAPSHOT_ID = (
    "5195fe85aca59c6fc56a8c9ac7943f764033d29cbb73b31cb3e7f7ebb4d85f96"
)

SNAPSHOT = BASE / "snapshots" / SNAPSHOT_ID

DEST = BASE / "verified" / "kickoffs_v1.json"

# Independently checked official sources.
EVIDENCE = [
    {
        "div": "E0",
        "date": "22/11/2026",
        "home": "Liverpool",
        "away": "Man United",
        "kickoff_utc": "2026-11-22T16:30:00+00:00",
        "url": "https://www.liverpoolfc.com/tickets/match/liverpool-v-manchester-united-english-premier-league-20261122",
    },
    {
        "div": "I1",
        "date": "22/11/2026",
        "home": "Atalanta",
        "away": "Inter",
        "kickoff_utc": "2026-11-22T19:45:00+00:00",
        "url": "https://www.inter.it/en/news/guide-atalanta-inter-serie-a-2026-27",
    },
    {
        "div": "E0",
        "date": "23/11/2026",
        "home": "Brentford",
        "away": "Everton",
        "kickoff_utc": "2026-11-23T20:00:00+00:00",
        "url": "https://www.premierleague.com/en/news/4725574/fixture-amendments-for-november",
    },
    {
        "div": "SP1",
        "date": "23/11/2026",
        "home": "Levante",
        "away": "Elche",
        "kickoff_utc": "2026-11-23T20:00:00+00:00",
        "url": "https://iaas-public-front-pro.laliga.com/partido/temporada-2026-2027-laliga-ea-sports-levante-ud-elche-cf-13",
    },
]


def main():
    print("=== EUROPE KICKOFF EVIDENCE V1 ===")

    # Verify snapshot integrity before using it.
    manager.validate(SNAPSHOT)

    fixture_path = SNAPSHOT / "fixtures.csv"
    raw = fixture_path.read_bytes()

    fixture_sha = hashlib.sha256(raw).hexdigest()

    with fixture_path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    london = ZoneInfo("Europe/London")

    verified = []

    for evidence in EVIDENCE:

        matches = [
            r for r in rows
            if r["Div"] == evidence["div"]
            and r["Date"] == evidence["date"]
            and r["HomeTeam"] == evidence["home"]
            and r["AwayTeam"] == evidence["away"]
        ]

        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one fixture: {evidence['home']} - {evidence['away']}; "
                f"found {len(matches)}"
            )

        row = matches[0]

        source_dt = datetime.strptime(
            row["Date"] + " " + row["Time"],
            "%d/%m/%Y %H:%M"
        ).replace(tzinfo=london)

        actual_utc = source_dt.astimezone(timezone.utc)

        expected_utc = datetime.fromisoformat(
            evidence["kickoff_utc"]
        )

        if actual_utc != expected_utc:
            raise RuntimeError(
                f"Kickoff mismatch: {evidence['home']} - {evidence['away']}"
            )

        verified.append(evidence)

        print(
            "VERIFIED:",
            evidence["home"],
            "-",
            evidence["away"],
            "| UTC",
            actual_utc.isoformat()
        )

    payload = {
        "schema": "bubus-europe-kickoffs-v1",
        "snapshot_id": SNAPSHOT_ID,
        "fixtures_sha256": fixture_sha,
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "source_timezone": "Europe/London",
        "verified_count": len(verified),
        "matches": verified,
    }

    DEST.parent.mkdir(parents=True, exist_ok=True)

    if DEST.exists():
        existing = json.loads(
            DEST.read_text(encoding="utf-8")
        )

        for field in (
            "schema",
            "snapshot_id",
            "fixtures_sha256",
            "source_timezone",
            "verified_count",
            "matches",
        ):
            if existing.get(field) != payload[field]:
                raise RuntimeError(
                    f"Existing evidence differs: {field}"
                )

        print("Evidence already exists")

    else:
        with DEST.open("x", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2
            )

        print("Evidence saved:", DEST)

    print("VERIFIED:", len(verified), "matches")
    print("FORECAST RECORDING: NOT AUTHORIZED")


if __name__ == "__main__":
    main()
