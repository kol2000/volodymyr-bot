#!/usr/bin/env python3
"""Independent RPL round 16 kickoff comparison."""

import argparse
import csv
import hashlib
import json

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import football_rpl_se_calendar_v1 as se


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_expansion/rpl_live"

FIXTURES = BASE / "RPL_fixtures_v2.csv"

DESTINATION = BASE / "verified/round16_kickoffs_v1.json"

MSK = ZoneInfo("Europe/Moscow")

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_se_date(text):

    parts = text.split()

    return datetime(
        2026,
        MONTHS[parts[1]],
        int(parts[0]),
        *map(int, parts[2].split(":")),
        tzinfo=MSK,
    )


def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--write",
        action="store_true"
    )

    args = ap.parse_args()

    print("=== BUBUS RPL KICKOFF VERIFICATION ===")

    csv_raw = FIXTURES.read_bytes()

    fixtures = list(csv.DictReader(
        csv_raw.decode("utf-8-sig").splitlines()
    ))

    se_raw = se.fetch()

    parser = se.Parser()
    parser.feed(
        se_raw.decode("utf-8", errors="replace")
    )

    matches = se.parse_matches(
        se.extract_round(parser.tokens, 16)
    )

    if len(fixtures) != 8 or len(matches) != 8:
        raise RuntimeError("Expected exactly 8 fixtures")

    csv_index = {
        (r["home"], r["away"]): r
        for r in fixtures
    }

    se_index = {
        (r["home"], r["away"]): r
        for r in matches
    }

    if len(csv_index) != 8 or len(se_index) != 8:
        raise RuntimeError("Duplicate fixture detected")

    if set(csv_index) != set(se_index):
        raise RuntimeError("Fixture lists differ")

    verified = []

    now = datetime.now(timezone.utc)

    for key in sorted(csv_index):

        a = csv_index[key]
        b = se_index[key]

        if int(a["gw"]) != 16:
            raise RuntimeError("Unexpected round")

        csv_msk = datetime.fromisoformat(
            a["kickoff_msk"]
        )

        csv_utc = datetime.fromisoformat(
            a["kickoff_utc"]
        )

        se_msk = parse_se_date(b["date"])

        if csv_msk.tzinfo is None:
            raise RuntimeError("Naive Moscow timestamp")

        if csv_utc.tzinfo is None:
            raise RuntimeError("Naive UTC timestamp")

        csv_msk = csv_msk.astimezone(MSK)

        csv_utc = csv_utc.astimezone(timezone.utc)

        if not (
            csv_msk == se_msk
            and csv_utc == se_msk.astimezone(timezone.utc)
        ):
            raise RuntimeError(
                f"Kickoff conflict: {key}"
            )

        if csv_utc <= now:
            raise RuntimeError(
                f"Fixture already started: {key}"
            )

        verified.append({
            "home": key[0],
            "away": key[1],
            "kickoff_utc": csv_utc.isoformat(),
            "kickoff_msk": se_msk.isoformat(),
        })

        print(
            "MATCH:",
            key[0],
            "—",
            key[1],
            se_msk.strftime("%Y-%m-%d %H:%M MSK")
        )

    record = {
        "schema": "bubus-rpl-kickoff-verification-v1",
        "competition": "RPL",
        "season": "2026/2027",
        "round": 16,
        "checked_utc": now.isoformat(),
        "fixtures_sha256": sha(csv_raw),
        "secondary_source": se.URL,
        "secondary_page_sha256": sha(se_raw),
        "matches": verified,
        "status": "compared",
    }

    print("\nVerified fixtures:", len(verified))
    print("CSV SHA256:", record["fixtures_sha256"])
    print("SE SHA256:", record["secondary_page_sha256"])

    if not args.write:
        print("\nREAD ONLY")
        return

    DESTINATION.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if DESTINATION.exists():

        old = json.loads(
            DESTINATION.read_text(encoding="utf-8")
        )

        if (
            old.get("fixtures_sha256")
            != record["fixtures_sha256"]
            or old.get("matches") != record["matches"]
        ):
            raise RuntimeError(
                "Existing verification differs. "
                "Manual investigation required."
            )

        print("\nVerification already exists")

    else:

        with DESTINATION.open(
            "x",
            encoding="utf-8"
        ) as f:
            json.dump(
                record,
                f,
                indent=2,
                ensure_ascii=False
            )

        print("\nVerification saved")

    print("Evidence:", DESTINATION)
    print("Forecast journal: untouched")
    print("UCL journal: untouched")


if __name__ == "__main__":
    main()
