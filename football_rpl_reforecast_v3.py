#!/usr/bin/env python3
"""RPL V3 reforecast from accepted result snapshots."""

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

import football_rpl_acceptance_v1 as acceptance_base
import football_rpl_acceptance_v2 as acceptance
import football_rpl_preview_v2 as preview


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--write",
        action="store_true",
        help="Save immutable preview snapshots"
    )

    args = ap.parse_args()

    print("=== BUBUS RPL REFORECAST V3 ===")

    # Acceptance must succeed BEFORE reading model inputs
    # or opening the forecast journal for writing.
    status = acceptance.main()

    if status != 0:
        print("\nREFORECAST BLOCKED")
        print("Reason: RPL Acceptance V2 is NOT READY")
        print("Forecast journal: unchanged")
        return 2

    snapshots = acceptance_base.load_snapshots()

    if acceptance_base.issues:
        raise RuntimeError("Snapshot validation failed")

    selected = max(
        snapshots,
        key=lambda s: (
            len(s["rows"]),
            s["created"]
        )
    )

    result_counts = Counter(
        int(row["gw"])
        for row in selected["rows"]
    )

    if (
        len(selected["rows"]) != 120
        or result_counts[15] != 8
    ):
        raise RuntimeError(
            "Expected exactly 120 results and "
            "8 completed matches in round 15"
        )

    print("\n=== ACCEPTED INPUT ===")
    print("Snapshot:", selected["path"].name)
    print("Results:", len(selected["rows"]))
    print("Round 15:", result_counts[15], "/8")

    # Use accepted snapshot, not hardcoded RPL_results_v2.csv.
    old_current = preview.CURRENT
    preview.CURRENT = selected["path"]

    preview.INPUT_FILES = [
        selected["path"] if path == old_current else path
        for path in preview.INPUT_FILES
    ]

    # Include the orchestration code and verification
    # evidence in the input fingerprint.
    preview.INPUT_FILES.append(
        preview.ROOT / "football_rpl_reforecast_v3.py"
    )

    evidence_dir = preview.BASE / "rpl_live/verified"

    evidence_files = sorted(
        evidence_dir.glob("*.v2.json")
    )

    if len(evidence_files) != 8:
        raise RuntimeError(
            f"Expected 8 verification files, "
            f"found {len(evidence_files)}"
        )

    preview.INPUT_FILES.extend(evidence_files)

    preview.INPUT_FILES.append(
        evidence_dir / "round16_kickoffs_v1.json"
    )

    fixtures = preview.read_csv(preview.FIXTURES)
    games, current = preview.load_games()

    if len(fixtures) != 8 or len(current) != 120:
        raise RuntimeError("Unexpected input counts")

    forecasts = preview.calculate_predictions(
        games,
        fixtures
    )

    if len(forecasts) != 32:
        raise RuntimeError(
            f"Expected 32 forecasts, got {len(forecasts)}"
        )

    fingerprint, _ = preview.input_fingerprint()

    print("Calculated forecasts:", len(forecasts))
    print("Input SHA256:", fingerprint)

    print("\n=== UPDATED V3 FORECASTS ===")

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
        print("\nREAD-ONLY PREVIEW")
        print("No predictions saved")
        return 0

    # Re-check time immediately before writing.
    now = datetime.now(timezone.utc)

    for row in forecasts:
        kickoff = datetime.fromisoformat(
            row["kickoff_utc"]
        )

        if kickoff <= now:
            raise RuntimeError(
                "One or more fixtures already started"
            )

    # Existing V2 writer saves immutable preview snapshots.
    # It will recompute using the accepted input.
    print("\n=== SAVING PREVIEW SNAPSHOT ===")
    preview.main()

    print("\nFINAL COMMIT: NOT PERFORMED")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "REFORECAST FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )
        sys.exit(2)
