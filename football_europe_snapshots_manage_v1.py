#!/usr/bin/env python3
"""Read-only European football snapshot manager."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "state/football_live"
SNAPSHOTS = BASE / "snapshots"

EXPECTED = {
    "EPL_2026-27.csv",
    "LaLiga_2026-27.csv",
    "Bundesliga_2026-27.csv",
    "SerieA_2026-27.csv",
    "Ligue1_2026-27.csv",
    "fixtures.csv",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate(path):

    manifest_path = path / "manifest.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest.get("schema") != "bubus-europe-snapshot-v1":
        raise RuntimeError("Unexpected schema")

    if manifest.get("season") != "2026/2027":
        raise RuntimeError("Unexpected season")

    if manifest.get("status") != "validated":
        raise RuntimeError("Unexpected status")

    files = manifest.get("files")

    if not isinstance(files, dict):
        raise RuntimeError("Invalid files manifest")

    if set(files) != EXPECTED:
        raise RuntimeError("Unexpected file list")

    actual_hashes = {}

    for name in sorted(EXPECTED):

        file_path = path / name

        raw = file_path.read_bytes()

        actual = sha(raw)

        info = files[name]

        if actual != info["sha256"]:
            raise RuntimeError(
                f"SHA256 mismatch: {name}"
            )

        if len(raw) != info["bytes"]:
            raise RuntimeError(
                f"Size mismatch: {name}"
            )

        actual_hashes[name] = actual

    identity = json.dumps(
        actual_hashes,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    calculated_id = sha(identity)

    if calculated_id != path.name:
        raise RuntimeError(
            "Snapshot directory name mismatch"
        )

    if manifest.get("snapshot_id") != calculated_id:
        raise RuntimeError(
            "Manifest snapshot ID mismatch"
        )

    created = datetime.fromisoformat(
        manifest["created_utc"]
    )

    if created.tzinfo is None:
        raise RuntimeError(
            "Snapshot creation time has no timezone"
        )

    if created > datetime.now(timezone.utc):
        raise RuntimeError(
            "Snapshot timestamp is in the future"
        )

    return created, manifest


def main():

    print("=== BUBUS EUROPE SNAPSHOT MANAGER ===")

    if not SNAPSHOTS.exists():
        raise RuntimeError("Snapshot directory missing")

    valid = []
    failures = []

    for path in sorted(SNAPSHOTS.iterdir()):

        if not path.is_dir():
            continue

        # Ignore temporary staging directories.
        if path.name.startswith(".staging-"):
            continue

        try:
            created, manifest = validate(path)

            valid.append((created, path, manifest))

            print(
                "VALID:",
                path.name[:16],
                "|",
                created.isoformat()
            )

        except Exception as exc:

            failures.append((path.name, str(exc)))

            print(
                "INVALID:",
                path.name,
                "|",
                str(exc)
            )

    print("\nValid snapshots:", len(valid))
    print("Invalid snapshots:", len(failures))

    if failures:
        raise RuntimeError(
            "Invalid snapshot detected. "
            "Automatic selection blocked."
        )

    if not valid:
        raise RuntimeError("No valid snapshots")

    latest = max(valid, key=lambda x: x[0])

    _, path, manifest = latest

    print("\n=== LATEST VALID SNAPSHOT ===")

    print("ID:", path.name)
    print("Created:", manifest["created_utc"])

    for name, info in sorted(manifest["files"].items()):
        print(name, "| rows:", info["rows"])

    print("\n=== PREVIEW COMMAND ===")

    print(
        "python3 football_forecast_live_v7.py "
        f"--source '{path}' "
        "--kickoff-zone Europe/London "
        "--lead-minutes 60"
    )

    print("\nSTATUS: SNAPSHOT INTEGRITY VERIFIED")
    print("ACCEPTANCE: NOT PERFORMED")
    print("READ ONLY — NO FILES MODIFIED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )
        sys.exit(2)
