#!/usr/bin/env python3
"""Bubus RPL Reforecast V4.

Accepted snapshot -> 32 forecast previews.

Default: read-only.
--write: save immutable preview snapshots.

Does not commit predictions.
"""

import argparse
import hashlib
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import football_rpl_compare_sources_v1 as compare
import football_rpl_preview_v2 as preview


ROOT = Path(__file__).resolve().parent

EVIDENCE = (
    ROOT /
    "state/football_expansion/rpl_live/verified"
)


def digest(path):
    return hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest()


def check_acceptance():

    print("=== ACCEPTANCE CHECK ===", flush=True)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "football_rpl_acceptance_v2.py"),
        ],
        cwd=ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Acceptance V2 is NOT READY"
        )


def select_snapshot():

    count, created, path, rows = (
        compare.latest_snapshot()
    )

    rounds = Counter(
        int(row["gw"])
        for row in rows
    )

    if len(rows) != 120:
        raise RuntimeError(
            f"Expected 120 results, got {len(rows)}"
        )

    if rounds[15] != 8:
        raise RuntimeError(
            f"Round 15 incomplete: {rounds[15]}/8"
        )

    return path, rows


def configure_preview(snapshot):

    original = preview.CURRENT

    preview.CURRENT = snapshot

    files = [
        snapshot if path == original else path
        for path in preview.INPUT_FILES
    ]

    additional = [
        ROOT / "football_rpl_reforecast_v4.py",
        EVIDENCE / "round16_kickoffs_v1.json",
    ]

    additional.extend(
        sorted(EVIDENCE.glob("*.v2.json"))
    )

    evidence_count = sum(
        path.name.endswith(".v2.json")
        for path in additional
    )

    if evidence_count != 8:
        raise RuntimeError(
            f"Expected 8 match confirmations, "
            f"found {evidence_count}"
        )

    files.extend(additional)

    # Preserve order and remove duplicate paths.
    preview.INPUT_FILES = list(
        dict.fromkeys(files)
    )


def calculate():

    fixtures = preview.read_csv(
        preview.FIXTURES
    )

    games, current = preview.load_games()

    if len(fixtures) != 8:
        raise RuntimeError(
            "Expected 8 fixtures"
        )

    if len(current) != 120:
        raise RuntimeError(
            "Expected 120 current-season matches"
        )

    if len(games) != 3520:
        raise RuntimeError(
            f"Unexpected history: {len(games)}"
        )

    forecasts = preview.calculate_predictions(
        games,
        fixtures
    )

    keys = {
        (
            row["competition"],
            row["season"],
            row["round"],
            row["home"],
            row["away"],
            row["kickoff_utc"],
            row["model"],
        )
        for row in forecasts
    }

    if len(forecasts) != 32 or len(keys) != 32:
        raise RuntimeError(
            "Expected 32 unique forecasts"
        )

    return forecasts


def check_kickoffs(forecasts):

    now = datetime.now(timezone.utc)

    for row in forecasts:

        kickoff = datetime.fromisoformat(
            row["kickoff_utc"]
        )

        if kickoff.tzinfo is None:
            raise RuntimeError(
                "Kickoff timezone missing"
            )

        # Keep at least one hour of safety margin.
        seconds = (
            kickoff.astimezone(timezone.utc) - now
        ).total_seconds()

        if seconds <= 3600:
            raise RuntimeError(
                "Less than 60 minutes before kickoff"
            )


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "=== BUBUS RPL REFORECAST V4 ===",
        flush=True
    )

    check_acceptance()

    snapshot, rows = select_snapshot()

    print("\n=== ACCEPTED SNAPSHOT ===")
    print("File:", snapshot.name)
    print("Results:", len(rows))

    configure_preview(snapshot)

    fingerprint, hashes = (
        preview.input_fingerprint()
    )

    print("Input SHA256:", fingerprint)

    forecasts = calculate()

    check_kickoffs(forecasts)

    print("\nCalculated forecasts:", len(forecasts))

    print("\n=== V3 PROBABILITIES ===")

    for row in forecasts:

        if row["model"] != "V3":
            continue

        print(
            f'{row["home"]} — {row["away"]}: '
            f'П1={row["p_home"]:.1%} '
            f'Х={row["p_draw"]:.1%} '
            f'П2={row["p_away"]:.1%}'
        )

    if not args.write:

        print("\nREAD ONLY")
        print("Forecast journal unchanged")
        return 0

    print("\n=== PRE-WRITE VALIDATION ===")

    # Re-run acceptance before writing.
    check_acceptance()

    # Ensure the latest selected snapshot
    # has not changed.
    current_snapshot, _ = select_snapshot()

    if current_snapshot != snapshot:
        raise RuntimeError(
            "Accepted snapshot changed"
        )

    # Recheck all model and evidence inputs.
    current_fingerprint, current_hashes = (
        preview.input_fingerprint()
    )

    if current_fingerprint != fingerprint:
        raise RuntimeError(
            "Input fingerprint changed"
        )

    if current_hashes != hashes:
        raise RuntimeError(
            "Input hashes changed"
        )

    check_kickoffs(forecasts)

    print("Acceptance: PASS")
    print("Snapshot: UNCHANGED")
    print("Input SHA256: UNCHANGED")
    print("Kickoffs: SAFE")

    print("\n=== WRITING PREVIEW ===")

    # Existing preview writer handles
    # immutable snapshot insertion.
    preview.main()

    print("\nREFORECAST V4 COMPLETED")
    print("Final commit: NOT PERFORMED")

    return 0


if __name__ == "__main__":

    try:
        sys.exit(main())

    except Exception as exc:

        print(
            "REFORECAST V4 BLOCKED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr,
        )

        sys.exit(2)
