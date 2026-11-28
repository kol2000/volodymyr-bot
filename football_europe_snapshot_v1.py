#!/usr/bin/env python3
"""Bubus Europe snapshots V1.

Default: read-only.
--write: persist a versioned six-file source snapshot.

Never modifies V7's live source files or SQLite.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import football_europe_update_v1 as update


ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "state/football_live/source"

SNAPSHOTS = (
    ROOT / "state/football_live/snapshots"
)

FILES = {
    f"{league}_2026-27.csv": (
        f"{update.BASE_URL}/mmz4281/"
        f"{update.SEASON}/{code}.csv",
        code,
    )
    for league, code in update.LEAGUES.items()
}

FILES["fixtures.csv"] = (
    f"{update.BASE_URL}/fixtures.csv",
    None,
)

FIXTURE_REQUIRED = {
    "Div",
    "Date",
    "Time",
    "HomeTeam",
    "AwayTeam",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode_csv(raw):

    if not raw.strip():
        raise RuntimeError("Empty response")

    if raw.lstrip()[:100].lower().startswith(
        (b"<html", b"<!doctype")
    ):
        raise RuntimeError("HTML instead of CSV")

    reader = csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"))
    )

    if not reader.fieldnames:
        raise RuntimeError("Missing CSV header")

    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise RuntimeError("Duplicate CSV columns")

    rows = list(reader)

    if not rows:
        raise RuntimeError("Empty CSV")

    if any(None in row for row in rows):
        raise RuntimeError("Malformed CSV row")

    return rows


def validate_fixture_rows(rows):

    if not FIXTURE_REQUIRED.issubset(rows[0]):
        raise RuntimeError("Unexpected fixtures schema")

    seen = set()

    for row in rows:

        div = row["Div"].strip()
        day = row["Date"].strip()
        clock = row["Time"].strip()
        home = row["HomeTeam"].strip()
        away = row["AwayTeam"].strip()

        if not all((div, day, clock, home, away)):
            raise RuntimeError("Incomplete fixture")

        if home == away:
            raise RuntimeError("Team plays itself")

        datetime.strptime(day, "%d/%m/%Y")
        datetime.strptime(clock, "%H:%M")

        key = (div, day, home, away)

        if key in seen:
            raise RuntimeError(
                f"Duplicate fixture: {key}"
            )

        seen.add(key)


def validate_all(raw_files):

    counts = {}

    for name, (url, code) in FILES.items():

        raw = raw_files[name]
        rows = decode_csv(raw)

        if code is None:

            validate_fixture_rows(rows)
            counts[name] = len(rows)

            continue

        remote = update.result_index(rows, code)

        local_raw = (SOURCE / name).read_bytes()

        local_rows = decode_csv(local_raw)

        local = update.result_index(
            local_rows, code
        )

        removed = set(local) - set(remote)

        changed = {
            key
            for key in set(local) & set(remote)
            if local[key] != remote[key]
        }

        if removed or changed:
            raise RuntimeError(
                f"{name}: historical conflict; "
                f"removed={len(removed)}, "
                f"changed={len(changed)}"
            )

        counts[name] = len(remote)

        print(
            f"{name}: "
            f"local={len(local)}, "
            f"remote={len(remote)}, "
            f"new={len(set(remote) - set(local))}"
        )

    return counts


def build_manifest(raw_files, counts):

    hashes = {
        name: digest(raw)
        for name, raw in sorted(raw_files.items())
    }

    identity = json.dumps(
        hashes,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    snapshot_id = digest(identity)

    return {
        "schema": "bubus-europe-snapshot-v1",
        "snapshot_id": snapshot_id,
        "created_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "season": "2026/2027",
        "status": "validated",
        "files": {
            name: {
                "source_url": FILES[name][0],
                "sha256": hashes[name],
                "bytes": len(raw_files[name]),
                "rows": counts[name],
            }
            for name in sorted(raw_files)
        },
    }


def save_snapshot(raw_files, manifest):

    destination = (
        SNAPSHOTS / manifest["snapshot_id"]
    )

    if destination.exists():

        old = json.loads(
            (destination / "manifest.json").read_text(
                encoding="utf-8"
            )
        )

        if (
            old.get("snapshot_id")
            != manifest["snapshot_id"]
            or old.get("files") != manifest["files"]
        ):
            raise RuntimeError(
                "Existing snapshot manifest mismatch"
            )

        for name, raw in raw_files.items():

            saved = (destination / name).read_bytes()

            if saved != raw:
                raise RuntimeError(
                    f"Existing snapshot corrupted: {name}"
                )

        print("Snapshot already exists")
        return

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=SNAPSHOTS
        )
    )

    try:

        for name, raw in raw_files.items():

            with (temp_dir / name).open("xb") as f:
                f.write(raw)

        with (temp_dir / "manifest.json").open(
            "x",
            encoding="utf-8"
        ) as f:

            json.dump(
                manifest,
                f,
                indent=2,
                ensure_ascii=False
            )

        # Publish only after every file was saved.
        os.rename(temp_dir, destination)

        print("NEW SNAPSHOT SAVED")

    finally:

        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true"
    )

    args = parser.parse_args()

    print("=== BUBUS EUROPE SNAPSHOT V1 ===")

    raw_files = {}

    for name, (url, code) in FILES.items():

        print("Downloading:", name)

        raw_files[name] = update.fetch(url)

    print("\n=== VALIDATION ===")

    counts = validate_all(raw_files)

    manifest = build_manifest(
        raw_files,
        counts
    )

    print("\n=== SNAPSHOT ===")

    print("ID:", manifest["snapshot_id"])

    for name, info in manifest["files"].items():
        print(
            name,
            "| rows:", info["rows"],
            "| SHA256:", info["sha256"][:16]
        )

    if not args.write:

        print("\nREAD ONLY")
        print("No files modified")
        return

    save_snapshot(
        raw_files,
        manifest
    )

    print("\nLive V7 files: untouched")
    print("Forecast database: untouched")


if __name__ == "__main__":
    main()
