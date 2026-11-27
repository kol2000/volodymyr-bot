#!/usr/bin/env python3
"""RPL Acceptance V2: read-only verification gate."""

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import football_rpl_acceptance_v1 as v1

BASE = Path("state/football_expansion/rpl_live")
VERIFIED = BASE / "verified"

EXPECTED_ROUND = 15
EXPECTED_SEASON = "2026/2027"

SCORE_RE = re.compile(r"^\d{1,2}:\d{1,2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def check_evidence(snapshot):

    print("\n=== VERIFICATION EVIDENCE ===")

    if snapshot is None:
        v1.block("No valid result snapshot")
        return

    results = {
        (
            row["date"],
            row["home"],
            row["away"]
        ): f'{row["ft_home"]}:{row["ft_away"]}'
        for row in snapshot["rows"]
        if int(row["gw"]) == EXPECTED_ROUND
    }

    evidence = {}

    for path in sorted(VERIFIED.glob("*.v2.json")):

        try:
            record = json.loads(
                path.read_text(encoding="utf-8")
            )

            if record.get("schema") != "bubus-rpl-verification-v2":
                raise ValueError("Unexpected schema")

            if record.get("competition") != "RPL":
                raise ValueError("Unexpected competition")

            if record.get("season") != EXPECTED_SEASON:
                raise ValueError("Unexpected season")

            if record.get("round") != EXPECTED_ROUND:
                continue

            if record.get("status") != "verified":
                raise ValueError("Invalid verification status")

            key = (
                record["date"],
                record["home"],
                record["away"]
            )

            score = record["score"]

            if not SCORE_RE.fullmatch(score):
                raise ValueError("Invalid score")

            if key in evidence:
                raise ValueError("Duplicate verification")

            if key not in results:
                raise ValueError("Match absent from current snapshot")

            if results[key] != score:
                raise ValueError(
                    f"Score mismatch: CSV={results[key]}, "
                    f"evidence={score}"
                )

            if record.get("primary_source") != (
                "https://rplstat.ru/kalendar/"
            ):
                raise ValueError("Unexpected primary source")

            url = record.get("matchcenter_url", "")

            parsed = urlparse(url)

            if (
                parsed.scheme != "https"
                or parsed.hostname not in (
                    "sport-express.net",
                    "www.sport-express.net"
                )
                or not parsed.path.startswith(
                    "/football/n/russia/premier/match-"
                )
            ):
                raise ValueError("Unexpected match-center URL")

            digest = record.get("matchcenter_sha256", "")

            if not SHA_RE.fullmatch(digest):
                raise ValueError("Invalid page SHA256")

            checked = datetime.fromisoformat(
                record["checked_utc"]
            )

            if checked.tzinfo is None:
                raise ValueError("Naive verification timestamp")

            if checked > v1.NOW:
                raise ValueError("Verification from future")

            evidence[key] = record

        except Exception as exc:
            v1.block(
                f"Invalid evidence {path.name}: {exc}"
            )

    print("Current round results:", len(results))
    print("Valid V2 evidence:", len(evidence))

    for key in sorted(results):

        status = (
            "VERIFIED"
            if key in evidence
            else "MISSING EVIDENCE"
        )

        print(
            key[0],
            key[1],
            results[key],
            key[2],
            "|",
            status
        )

        if key not in evidence:
            v1.block(
                f"Missing verification: {key}"
            )

    if len(results) != 8:
        v1.block(
            f"Round 15 not complete: {len(results)}/8"
        )

    if len(evidence) != 8:
        v1.block(
            f"Not all results verified: {len(evidence)}/8"
        )

    print(
        "Note: evidence records are historical checks;"
        " source pages are not re-fetched here."
    )


def main():

    print("=== BUBUS RPL ACCEPTANCE V2 ===")
    print("Checked UTC:", v1.NOW.isoformat())

    snapshots = v1.load_snapshots()

    latest = v1.check_history(snapshots)

    v1.check_fixtures()

    check_evidence(latest)

    print("\n=== KICKOFF VERIFICATION ===")

    # Verify persisted two-source comparison evidence.
    import hashlib

    evidence_path = (
        BASE / "verified/round16_kickoffs_v1.json"
    )

    if not evidence_path.exists():
        v1.block("Round 16 kickoff evidence missing")

    else:
        try:
            record = json.loads(
                evidence_path.read_text(encoding="utf-8")
            )

            current_hash = hashlib.sha256(
                v1.FIXTURES.read_bytes()
            ).hexdigest()

            if record.get("schema") != (
                "bubus-rpl-kickoff-verification-v1"
            ):
                raise ValueError("Invalid evidence schema")

            if record.get("competition") != "RPL":
                raise ValueError("Wrong competition")

            if record.get("season") != EXPECTED_SEASON:
                raise ValueError("Wrong season")

            if record.get("round") != 16:
                raise ValueError("Wrong round")

            if record.get("status") != "compared":
                raise ValueError("Invalid evidence status")

            if record.get("fixtures_sha256") != current_hash:
                raise ValueError("Fixture SHA256 mismatch")

            if len(record.get("matches", [])) != 8:
                raise ValueError("Expected eight verified fixtures")

            checked = datetime.fromisoformat(
                record["checked_utc"]
            )

            if checked.tzinfo is None:
                raise ValueError("Naive verification time")

            if checked > v1.NOW:
                raise ValueError("Verification from future")

            import csv
            import io
            from datetime import timezone
            from zoneinfo import ZoneInfo

            rows = list(csv.DictReader(
                io.StringIO(
                    v1.FIXTURES.read_bytes().decode("utf-8-sig")
                )
            ))

            expected = {
                (
                    row["home"],
                    row["away"],
                    datetime.fromisoformat(
                        row["kickoff_utc"]
                    ).astimezone(timezone.utc).isoformat(),
                    datetime.fromisoformat(
                        row["kickoff_msk"]
                    ).astimezone(
                        ZoneInfo("Europe/Moscow")
                    ).isoformat()
                )
                for row in rows
            }

            actual = {
                (
                    row["home"],
                    row["away"],
                    row["kickoff_utc"],
                    row["kickoff_msk"]
                )
                for row in record["matches"]
            }

            if len(expected) != 8 or len(actual) != 8:
                raise ValueError("Duplicate or missing fixture")

            if actual != expected:
                raise ValueError("Kickoff evidence does not match CSV")

            print("Round 16 kickoffs: 8/8 VERIFIED")

        except Exception as exc:
            v1.block(
                f"Invalid kickoff evidence: {exc}"
            )

    print("\n=== FINAL STATUS ===")

    print("Blocking issues:", len(v1.issues))

    for number, issue in enumerate(v1.issues, 1):
        print(f"{number}. {issue}")

    if v1.issues:
        print("\nSTATUS: NOT READY")
        print("FINAL COMMIT: PROHIBITED")
        return 2

    print("\nSTATUS: READY")
    print("FINAL COMMIT: SEPARATE APPROVAL REQUIRED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "ACCEPTANCE V2 FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )
        sys.exit(2)
