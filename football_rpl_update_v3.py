#!/usr/bin/env python3
"""Bubus RPL Update V3.

Fetch and validate RPL results.
Default: read-only.
--write: create immutable, content-addressed snapshots.
Never modifies live V2, forecast databases, or UCL.
"""

import argparse
import csv
import hashlib
import io
import json
import sys

from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import football_rpl_source_v1 as source


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_expansion/rpl_live"

REFERENCE = BASE / "RPL_results_v2.csv"

SNAPSHOTS = BASE / "snapshots"

MSK = ZoneInfo("Europe/Moscow")

FIELDS = [
    "gw",
    "date",
    "home",
    "away",
    "ft_home",
    "ft_away",
    "source",
]


def read_csv(path):
    with path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        return list(csv.DictReader(f))


def key_of(row):
    return (
        row["date"],
        row["home"],
        row["away"],
    )


def score_of(row):
    return (
        int(row["ft_home"]),
        int(row["ft_away"]),
    )


def validate_rows(rows):
    seen = set()

    for row in rows:
        key = key_of(row)

        if key in seen:
            raise RuntimeError(
                f"Duplicate result: {key}"
            )

        seen.add(key)

        if row["home"] == row["away"]:
            raise RuntimeError(
                f"Invalid teams: {key}"
            )

        if not 1 <= int(row["gw"]) <= 30:
            raise RuntimeError(
                f"Invalid round: {key}"
            )

        datetime.fromisoformat(row["date"])

        gh, ga = score_of(row)

        if gh < 0 or ga < 0:
            raise RuntimeError(
                f"Invalid score: {key}"
            )

    return seen


def make_csv_bytes(rows):
    stream = io.StringIO(newline="")

    writer = csv.DictWriter(
        stream,
        fieldnames=FIELDS,
        lineterminator="\n",
    )

    writer.writeheader()
    writer.writerows(rows)

    return stream.getvalue().encode("utf-8")


def fetch_and_compare():
    now = datetime.now(timezone.utc)

    print("=== BUBUS RPL UPDATE V3 ===")
    print("Checked UTC:", now.isoformat())
    print("Source:", source.URL)

    if not REFERENCE.exists():
        raise RuntimeError(
            f"Reference missing: {REFERENCE}"
        )

    original = read_csv(REFERENCE)

    if len(original) != 117:
        raise RuntimeError(
            "Unexpected reference size"
        )

    validate_rows(original)

    existing = {
        key_of(row): dict(row)
        for row in original
    }

    print("Reference results:", len(existing))

    # Existing tested parser.
    matches = source.parse_matches(
        source.fetch()
    )

    if len(matches) < 120:
        raise RuntimeError(
            "Source returned unexpectedly few fixtures"
        )

    print("Source fixtures:", len(matches))

    source_keys = set()

    added = []
    confirmed = []
    conflicts = []
    upcoming = []
    finished = []

    for match in matches:

        day = match["kickoff_msk"].date()

        key = (
            day.isoformat(),
            match["home"],
            match["away"],
        )

        if key in source_keys:
            raise RuntimeError(
                f"Duplicate source fixture: {key}"
            )

        source_keys.add(key)

        if match["gh"] is None:
            upcoming.append(match)
            continue

        if match["ga"] is None:
            raise RuntimeError(
                f"Incomplete score: {key}"
            )

        kickoff_utc = (
            match["kickoff_msk"]
            .replace(tzinfo=MSK)
            .astimezone(timezone.utc)
        )

        # A result cannot already be final before kickoff.
        if kickoff_utc > now:
            raise RuntimeError(
                f"Future match has final score: {key}"
            )

        finished.append(match)

        new_score = (
            int(match["gh"]),
            int(match["ga"]),
        )

        old = existing.get(key)

        if old is None:

            row = {
                "gw": str(match["gw"]),
                "date": key[0],
                "home": key[1],
                "away": key[2],
                "ft_home": str(new_score[0]),
                "ft_away": str(new_score[1]),
                "source": "rplstat-provisional",
            }

            added.append(row)

        else:

            old_score = score_of(old)

            if old_score == new_score:

                confirmed.append(key)

            else:

                conflicts.append({
                    "match": key,
                    "old": old_score,
                    "new": new_score,
                })

    print("\n=== SOURCE STATUS ===")
    print("Finished:", len(finished))
    print("Upcoming/no result:", len(upcoming))
    print("Matching existing:", len(confirmed))
    print("New results:", len(added))
    print("Score conflicts:", len(conflicts))

    missing = sorted(
        set(existing) - source_keys
    )

    print("Reference matches absent from source:", len(missing))

    if missing:
        for key in missing[:20]:
            print("MISSING:", key)

        raise RuntimeError(
            "Source is missing reference matches"
        )

    if conflicts:

        print("\n=== SCORE CONFLICTS ===")

        for item in conflicts:
            print(
                "CONFLICT:",
                item["match"],
                "OLD:",
                item["old"],
                "NEW:",
                item["new"],
            )

        raise RuntimeError(
            "Conflicting scores: snapshot rejected"
        )

    for row in added:

        key = key_of(row)

        if key in existing:
            raise RuntimeError(
                f"Unexpected duplicate: {key}"
            )

        existing[key] = row

    rows = sorted(
        existing.values(),
        key=lambda r: (
            r["date"],
            r["home"],
            r["away"],
        )
    )

    validate_rows(rows)

    if len(rows) != len(original) + len(added):
        raise RuntimeError(
            "Unexpected final result count"
        )

    counts = Counter(
        int(row["gw"])
        for row in rows
    )

    for gw in range(1, 15):
        if counts[gw] != 8:
            raise RuntimeError(
                f"Round {gw}: expected 8 results"
            )

    if counts[15] > 8:
        raise RuntimeError(
            "Too many round 15 results"
        )

    print("\n=== NEW RESULTS ===")

    if not added:
        print("No new results")

    for row in added:

        print(
            f'GW{int(row["gw"]):02d}',
            row["date"],
            row["home"],
            f'{row["ft_home"]}:{row["ft_away"]}',
            row["away"],
        )

    print("\n=== TOTAL ===")

    print("Reference:", len(original))
    print("Added:", len(added))
    print("Final:", len(rows))
    print("Round 15:", counts[15], "/8")

    data = make_csv_bytes(rows)

    digest = hashlib.sha256(data).hexdigest()

    print("Snapshot SHA256:", digest)

    return {
        "rows": rows,
        "data": data,
        "sha256": digest,
        "added": added,
        "now": now,
        "counts": counts,
        "reference_sha256": hashlib.sha256(
            REFERENCE.read_bytes()
        ).hexdigest(),
    }


def save_snapshot(result):

    digest = result["sha256"]

    csv_path = SNAPSHOTS / f"{digest}.csv"

    json_path = SNAPSHOTS / f"{digest}.json"

    # Content-addressed snapshot: identical bytes = same path.
    if csv_path.exists():

        current_hash = hashlib.sha256(
            csv_path.read_bytes()
        ).hexdigest()

        if current_hash != digest:
            raise RuntimeError(
                "Existing snapshot hash mismatch"
            )

        if not json_path.exists():
            raise RuntimeError(
                "Existing snapshot manifest missing"
            )

        print("\nSNAPSHOT ALREADY EXISTS")
        print("CSV:", csv_path)

        return

    if json_path.exists():
        raise RuntimeError(
            "Manifest exists without CSV"
        )

    manifest = {
        "schema": "bubus-rpl-update-v3",
        "created_utc": result["now"].isoformat(),
        "source_url": source.URL,
        "reference_file": str(
            REFERENCE.relative_to(ROOT)
        ),
        "reference_sha256": result[
            "reference_sha256"
        ],
        "results_sha256": digest,
        "results_count": len(result["rows"]),
        "round15_results": result["counts"][15],
        "new_results": result["added"],
        "status": "provisional",
        "note": (
            "New results require independent confirmation. "
            "This snapshot is not automatically promoted "
            "to the forecasting input."
        ),
    }

    SNAPSHOTS.mkdir(
        parents=True,
        exist_ok=True
    )

    # Exclusive creation prevents overwriting.
    with csv_path.open("xb") as f:
        f.write(result["data"])

    try:
        with json_path.open(
            "x",
            encoding="utf-8"
        ) as f:

            json.dump(
                manifest,
                f,
                ensure_ascii=False,
                indent=2,
            )

    except Exception:
        # CSV is left untouched for forensic inspection.
        raise

    print("\n=== SNAPSHOT CREATED ===")
    print("CSV:", csv_path)
    print("Manifest:", json_path)
    print("SHA256:", digest)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Create a new versioned snapshot"
    )

    args = parser.parse_args()

    result = fetch_and_compare()

    if not args.write:

        print("\nREAD-ONLY PREVIEW")
        print("No files modified")

        return

    save_snapshot(result)

    print("\n=== SAFETY ===")
    print("Original results V2: untouched")
    print("Forecast databases: untouched")
    print("UCL databases: untouched")
    print("Telegram messages: not sent")
    print("Automatic forecast promotion: disabled")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            "\nRPL UPDATE FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )

        sys.exit(1)
