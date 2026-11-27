#!/usr/bin/env python3
"""Bubus Europe Acceptance V2.

Read-only independent kickoff evidence audit.

Verifies archived HTML, timestamps, identity,
snapshot linkage and fixture consistency.

Does not authorize forecast recording.
"""

import csv
import hashlib
import json
import re
import sys

from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import football_europe_snapshots_manage_v1 as manager
import football_europe_kickoff_verify_v2 as verify


BASE = Path("state/football_live")

EVIDENCE = BASE / "verified/kickoffs_v2"

V1 = BASE / "verified/kickoffs_v1.json"

LONDON = ZoneInfo("Europe/London")

MIN_LEAD = timedelta(minutes=60)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def check_record(directory, fixture_rows, source):

    record = load_json(
        directory / "evidence.json"
    )

    raw = (
        directory / "official.html"
    ).read_bytes()

    if record.get("schema") != "bubus-europe-kickoff-v2":
        raise RuntimeError("Invalid evidence schema")

    if record.get("status") != "verified":
        raise RuntimeError("Evidence not verified")

    if sha(raw) != record["official_html_sha256"]:
        raise RuntimeError("Archived HTML SHA256 mismatch")

    if record["snapshot_id"] != source["snapshot_id"]:
        raise RuntimeError("Wrong snapshot")

    if record["fixtures_sha256"] != source["fixtures_sha256"]:
        raise RuntimeError("Wrong fixture SHA256")

    identity = (
        record["competition"],
        record["date"],
        record["home"],
        record["away"]
    )

    originals = [
        item for item in source["matches"]
        if (
            item["div"],
            item["date"],
            item["home"],
            item["away"]
        ) == identity
    ]

    if len(originals) != 1:
        raise RuntimeError("Unknown or duplicate evidence identity")

    original = originals[0]

    if record["official_url"] != original["url"]:
        raise RuntimeError("Official URL mismatch")

    if record["kickoff_utc"] != original["kickoff_utc"]:
        raise RuntimeError("Kickoff differs from original evidence")

    fixtures = [
        row for row in fixture_rows
        if (
            row["Div"],
            row["Date"],
            row["HomeTeam"],
            row["AwayTeam"]
        ) == identity
    ]

    if len(fixtures) != 1:
        raise RuntimeError("Fixture missing or duplicated")

    row = fixtures[0]

    kickoff = datetime.strptime(
        row["Date"] + " " + row["Time"],
        "%d/%m/%Y %H:%M"
    ).replace(
        tzinfo=LONDON
    ).astimezone(timezone.utc)

    recorded_kickoff = datetime.fromisoformat(
        record["kickoff_utc"]
    )

    if recorded_kickoff.tzinfo is None:
        raise RuntimeError("Naive kickoff timestamp")

    if kickoff != recorded_kickoff:
        raise RuntimeError("Fixture UTC mismatch")

    fetched = datetime.fromisoformat(
        record["fetched_utc"]
    )

    if fetched.tzinfo is None:
        raise RuntimeError("Naive verification timestamp")

    if fetched > datetime.now(timezone.utc):
        raise RuntimeError("Verification timestamp in future")

    lead = kickoff - fetched

    if lead <= MIN_LEAD:
        raise RuntimeError(
            "Evidence captured less than 60 minutes before kickoff"
        )

    rule_key = (
        record["competition"],
        record["home"],
        record["away"]
    )

    expected_rule = verify.RULES.get(rule_key)

    if not expected_rule:
        raise RuntimeError("No verification rule")

    if record["matched_pattern"] != expected_rule:
        raise RuntimeError("Verification rule mismatch")

    text = verify.normalized_text(raw)

    matches = list(
        re.finditer(
            expected_rule,
            text,
            flags=re.IGNORECASE
        )
    )

    if not matches:
        raise RuntimeError("Archived HTML does not confirm kickoff")

    if rule_key == ("I1", "Atalanta", "Inter"):
        if not (
            re.search(r"Atalanta\s+vs\.?\s+Inter", text, re.I)
            or re.search(
                r"Atalanta\s+against\s+Inter", text, re.I
            )
        ):
            raise RuntimeError("Teams not confirmed in Inter HTML")

    if rule_key == ("SP1", "Levante", "Elche"):
        if not re.search(
            r"Levante\s+UD\s+VS\s+Elche\s+CF",
            text,
            re.I
        ):
            raise RuntimeError("Teams not confirmed in LaLiga HTML")

    matched_contexts = [
        text[
            max(0, hit.start() - 100):
            min(len(text), hit.end() + 150)
        ]
        for hit in matches
    ]

    if record["matched_context"] not in matched_contexts:
        raise RuntimeError("Archived matched context differs")

    expected_id = sha(
        json.dumps(
            [
                record["snapshot_id"],
                record["competition"],
                record["date"],
                record["home"],
                record["away"],
                record["kickoff_utc"]
            ],
            ensure_ascii=False,
            separators=(",", ":")
        ).encode("utf-8")
    )

    if directory.name != expected_id:
        raise RuntimeError("Evidence directory identity mismatch")

    return record, lead


def main():

    print("=== BUBUS EUROPE ACCEPTANCE V2 ===")

    source = load_json(V1)

    snapshot = (
        BASE / "snapshots" / source["snapshot_id"]
    )

    manager.validate(snapshot)

    fixture_path = snapshot / "fixtures.csv"

    if sha(fixture_path.read_bytes()) != source["fixtures_sha256"]:
        raise RuntimeError("Fixture source mismatch")

    with fixture_path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    print("Snapshot:", source["snapshot_id"])
    print("Snapshot integrity: VERIFIED")

    directories = sorted(
        p for p in EVIDENCE.iterdir()
        if p.is_dir() and not p.name.startswith(".staging-")
    )

    expected = len(source["matches"])
    passed = 0
    failed = 0
    seen = set()

    print("\n=== ARCHIVED KICKOFF EVIDENCE ===")

    for directory in directories:

        try:
            record, lead = check_record(
                directory, rows, source
            )

            identity = (
                record["competition"],
                record["date"],
                record["home"],
                record["away"]
            )

            if identity in seen:
                raise RuntimeError("Duplicate verified match")

            seen.add(identity)

            passed += 1

            print(
                "VERIFIED:",
                record["home"],
                "—",
                record["away"],
                "|",
                round(lead.total_seconds() / 60),
                "minutes before kickoff"
            )

        except Exception as exc:

            failed += 1

            print(
                "BLOCK:",
                directory.name[:16],
                type(exc).__name__,
                str(exc)
            )

    print("\n=== FINAL STATUS ===")

    print("Expected:", expected)
    print("Verified:", passed)
    print("Failed:", failed)

    if failed or passed != expected:
        print("STATUS: NOT READY")
        return 2

    print("STATUS: EVIDENCE VALIDATED")
    print("FORECAST RECORDING: NOT AUTHORIZED")
    print("READ ONLY")

    return 0


if __name__ == "__main__":

    try:
        sys.exit(main())

    except Exception as exc:

        print(
            "STATUS: NOT READY",
            type(exc).__name__,
            str(exc)
        )

        sys.exit(2)
