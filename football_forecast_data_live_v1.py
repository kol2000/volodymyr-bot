#!/usr/bin/env python3
"""Bubus Forecast V8 snapshot data loader V1.

Offline snapshot selection and adapter loading.

Does not update source files.
Does not modify databases.
Does not publish Telegram messages.

Research-stage component, not yet wired into production.
"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import football_europe_adapter_v3 as v3
import football_europe_adapter_v4 as v4
import football_europe_update_v1 as update
import football_europe_snapshots_manage_v1 as manager


ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "state/football_live/source"
SNAPSHOTS = ROOT / "state/football_live/snapshots"

EXPECTED_MATCHES = 1752



def latest_snapshot():
    """Select newest integrity-validated snapshot."""

    if not SNAPSHOTS.is_dir():
        raise RuntimeError("Snapshot directory missing")

    candidates = []

    for path in SNAPSHOTS.iterdir():

        if not path.is_dir():
            continue

        if path.name.startswith("."):
            continue

        created, manifest = manager.validate(path)

        candidates.append(
            (created, path, manifest)
        )

    if not candidates:
        raise RuntimeError("No valid snapshots")

    return max(
        candidates,
        key=lambda item: item[0],
    )


def compare_source_results(snapshot):
    """Reject removed or changed historical CSV scores."""

    import csv

    def read_rows(path):

        with path.open(
            encoding="utf-8-sig",
            newline="",
        ) as f:
            return list(csv.DictReader(f))

    report = {}

    for league, code in update.LEAGUES.items():

        filename = f"{league}_2026-27.csv"

        old = update.result_index(
            read_rows(SOURCE / filename),
            code,
        )

        new = update.result_index(
            read_rows(snapshot / filename),
            code,
        )

        removed = set(old) - set(new)

        changed = {
            key
            for key in set(old) & set(new)
            if old[key] != new[key]
        }

        added = set(new) - set(old)

        if removed or changed:
            raise RuntimeError(
                f"{league}: historical result conflict: "
                f"removed={len(removed)}, "
                f"changed={len(changed)}"
            )

        report[league] = {
            "source_results": len(old),
            "snapshot_results": len(new),
            "added": len(added),
        }

    return report




def load_current_matches():
    """Return reconciled European season matches.

    The result is based on a validated snapshot and
    the existing V4 reconciliation rules.
    """

    created, snapshot, manifest = latest_snapshot()

    comparison = compare_source_results(snapshot)

    matches, counts = v4.load_all(source_dir=snapshot)

    if len(matches) != EXPECTED_MATCHES:
        raise RuntimeError(
            f"Unexpected match count: {len(matches)}"
        )

    seen = set()

    for match in matches:

        key = (
            match["competition"],
            str(match["match_id"]),
        )

        if key in seen:
            raise RuntimeError(
                f"Duplicate match identity: {key}"
            )

        seen.add(key)

    return {
        "snapshot_id": manifest["snapshot_id"],
        "snapshot_created_utc": created,
        "matches": matches,
        "counts": counts,
        "source_comparison": comparison,
    }


def main():

    print("=== BUBUS FORECAST DATA LIVE V1 ===")
    print("MODE: OFFLINE / READ ONLY")

    context = load_current_matches()

    created = context["snapshot_created_utc"]

    age_hours = (
        datetime.now(timezone.utc) - created
    ).total_seconds() / 3600

    print()
    print("=== SNAPSHOT ===")
    print("ID:", context["snapshot_id"])
    print("Created UTC:", created.isoformat())
    print("Content age hours:", round(age_hours, 2))

    print()
    print("=== RESULT COMPARISON ===")

    for league, info in context[
        "source_comparison"
    ].items():

        print(
            league,
            "| source:", info["source_results"],
            "| snapshot:", info["snapshot_results"],
            "| new:", info["added"],
        )

    print()
    print("=== MATCH STATUS ===")

    counts = Counter(
        (
            row["competition"],
            row["state_status"],
        )
        for row in context["matches"]
    )

    for key, count in sorted(counts.items()):
        print(key, count)

    print()
    print("=== SUMMARY ===")

    print("Matches:", len(context["matches"]))

    new_results = sum(
        item["added"]
        for item in context["source_comparison"].values()
    )

    print("New CSV results:", new_results)

    print()
    print("DATA LOADER: PASSED")
    print("SNAPSHOT INTEGRITY: VERIFIED")
    print("ADAPTER V4: PASSED")

    print("PRODUCTION READINESS: NOT YET VERIFIED")
    print("SOURCE CHECK FRESHNESS: NOT VERIFIED")
    print("ADDITIONAL PROVIDER FRESHNESS: NOT VERIFIED")
    print("RESULT AVAILABILITY TIMES: NOT VERIFIED")
    print("FUTURE RESULT TRANSITIONS: NOT TESTED")

    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
