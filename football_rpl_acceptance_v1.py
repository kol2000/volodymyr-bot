#!/usr/bin/env python3
"""Bubus RPL Acceptance V1. Strictly read-only."""

import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "state/football_expansion/rpl_live"

REFERENCE = BASE / "RPL_results_v2.csv"
FIXTURES = BASE / "RPL_fixtures_v2.csv"
SNAPSHOTS = BASE / "snapshots"

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime.now(timezone.utc)

issues = []


def block(message):
    issues.append(message)
    print("BLOCK:", message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_csv_bytes(data):
    import io
    return list(csv.DictReader(
        io.StringIO(data.decode("utf-8-sig"))
    ))


def result_key(row):
    return (
        row["date"],
        row["home"],
        row["away"]
    )


def result_score(row):
    return (
        int(row["ft_home"]),
        int(row["ft_away"])
    )


def validate_results(rows, label):
    result = {}

    for row in rows:
        key = result_key(row)

        if key in result:
            raise ValueError(
                f"{label}: duplicate {key}"
            )

        gw = int(row["gw"])
        gh, ga = result_score(row)

        datetime.fromisoformat(row["date"])

        if not 1 <= gw <= 30:
            raise ValueError(
                f"{label}: invalid round {gw}"
            )

        if gh < 0 or ga < 0:
            raise ValueError(
                f"{label}: invalid score {key}"
            )

        if row["home"] == row["away"]:
            raise ValueError(
                f"{label}: identical teams {key}"
            )

        result[key] = row

    return result


def load_snapshots():
    snapshots = []

    if not SNAPSHOTS.exists():
        block("Snapshot directory missing")
        return snapshots

    for csv_path in sorted(SNAPSHOTS.glob("*.csv")):

        digest = csv_path.stem
        json_path = csv_path.with_suffix(".json")

        if not json_path.exists():
            block(f"Missing manifest: {digest}")
            continue

        raw = csv_path.read_bytes()

        if sha256(raw) != digest:
            block(f"CSV hash mismatch: {digest}")
            continue

        try:
            manifest = json.loads(
                json_path.read_text(encoding="utf-8")
            )

            if manifest["results_sha256"] != digest:
                raise ValueError("Manifest hash mismatch")

            if manifest["status"] != "provisional":
                raise ValueError("Unexpected snapshot status")

            rows = read_csv_bytes(raw)

            index = validate_results(rows, digest)

            if manifest["results_count"] != len(rows):
                raise ValueError("Result count mismatch")

            r15 = sum(
                int(r["gw"]) == 15
                for r in rows
            )

            if manifest["round15_results"] != r15:
                raise ValueError("Round 15 count mismatch")

            created = datetime.fromisoformat(
                manifest["created_utc"]
            )

            if created.tzinfo is None:
                raise ValueError("Naive creation timestamp")

            if created.astimezone(timezone.utc) > NOW:
                raise ValueError("Snapshot created in future")

            snapshots.append({
                "path": csv_path,
                "digest": digest,
                "manifest": manifest,
                "rows": rows,
                "index": index,
                "created": created,
            })

        except Exception as exc:
            block(f"Invalid snapshot {digest}: {exc}")

    return snapshots


def check_history(snapshots):

    print("\n=== RESULT HISTORY ===")

    reference_raw = REFERENCE.read_bytes()
    reference = validate_results(
        read_csv_bytes(reference_raw),
        "Reference V2"
    )

    print("Reference matches:", len(reference))

    if len(reference) != 117:
        block("Reference must contain 117 results")

    if not snapshots:
        block("No valid snapshots")
        return None

    # Check every snapshot against the original reference.
    for snapshot in snapshots:
        index = snapshot["index"]
        digest = snapshot["digest"]

        for key, old in reference.items():
            new = index.get(key)

            if new is None:
                block(f"{digest}: reference match missing {key}")
                continue

            if result_score(new) != result_score(old):
                block(f"{digest}: reference score conflict {key}")

        manifest = snapshot["manifest"]

        if manifest["reference_sha256"] != sha256(
            reference_raw
        ):
            block(f"{digest}: reference hash mismatch")

    # Check score consistency across all saved versions.
    known_scores = {}

    for snapshot in snapshots:
        for key, row in snapshot["index"].items():
            score = result_score(row)

            if key in known_scores:
                if known_scores[key] != score:
                    block(
                        f"Cross-snapshot score conflict: {key}"
                    )
            else:
                known_scores[key] = score

    # Choose the most complete snapshot; creation time
    # breaks ties. Never select by filename.
    latest = max(
        snapshots,
        key=lambda s: (
            len(s["rows"]),
            s["created"]
        )
    )

    print("Valid snapshots:", len(snapshots))
    print("Selected:", latest["path"].name)
    print("Results:", len(latest["rows"]))

    counts = Counter(
        int(row["gw"])
        for row in latest["rows"]
    )

    for gw in range(1, 15):
        if counts[gw] != 8:
            block(
                f"Round {gw}: {counts[gw]}/8 results"
            )

    print("Round 15:", f"{counts[15]}/8")

    if counts[15] != 8:
        block(
            f"Round 15 incomplete: {counts[15]}/8"
        )

    return latest


def check_fixtures():

    print("\n=== ROUND 16 FIXTURES ===")

    rows = read_csv_bytes(FIXTURES.read_bytes())

    print("Fixtures:", len(rows))

    if len(rows) != 8:
        block(f"Expected 8 fixtures, got {len(rows)}")

    seen = set()
    teams = set()

    for row in rows:
        try:
            if int(row["gw"]) != 16:
                raise ValueError("Not round 16")

            home = row["home"]
            away = row["away"]

            key = (home, away)

            if key in seen:
                raise ValueError("Duplicate fixture")

            seen.add(key)

            if home == away:
                raise ValueError("Identical teams")

            if home in teams or away in teams:
                raise ValueError("Team plays twice")

            teams.update((home, away))

            kickoff = datetime.fromisoformat(
                row["kickoff_utc"]
            )

            if kickoff.tzinfo is None:
                raise ValueError("No UTC timezone")

            kickoff = kickoff.astimezone(
                timezone.utc
            )

            day_msk = kickoff.astimezone(
                MSK
            ).date().isoformat()

            if day_msk != row["date"]:
                raise ValueError("Moscow date mismatch")

            remaining = kickoff - NOW

            print(
                row["date"],
                home,
                "—",
                away,
                "| UTC",
                kickoff.isoformat(),
                "| hours remaining",
                round(
                    remaining.total_seconds() / 3600,
                    1
                )
            )

            if remaining <= timedelta(minutes=60):
                block(
                    f"Too late to commit: {home} — {away}"
                )

        except Exception as exc:
            block(
                f"Invalid fixture {row.get('home')} - "
                f"{row.get('away')}: {exc}"
            )

    if len(teams) != 16:
        block(
            f"Expected 16 unique teams, got {len(teams)}"
        )

    return rows


def main():

    print("=== BUBUS RPL ACCEPTANCE V1 ===")
    print("Checked UTC:", NOW.isoformat())

    snapshots = load_snapshots()

    latest = check_history(snapshots)

    fixtures = check_fixtures()

    print("\n=== INDEPENDENT VERIFICATION ===")

    # Deliberate fail-closed policy.
    # V1 does not have a signed/verified approval record.
    # Therefore it must never authorize final commits.
    block(
        "Independent result and kickoff verification "
        "not implemented yet"
    )

    print("\n=== STATUS ===")

    print(
        "Latest valid result snapshot:",
        latest["digest"] if latest else "NONE"
    )

    print("Round 16 fixtures:", len(fixtures))
    print("Blocking issues:", len(issues))

    for number, issue in enumerate(issues, 1):
        print(f"{number}. {issue}")

    if issues:
        print("\nSTATUS: NOT READY")
        print("FINAL COMMIT: PROHIBITED")
        return 2

    print("\nSTATUS: READY")
    print("FINAL COMMIT: MANUAL APPROVAL REQUIRED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "ACCEPTANCE FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )
        sys.exit(2)
