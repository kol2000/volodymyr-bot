#!/usr/bin/env python3
"""Bubus EPL Calendar Snapshot V1.

Immutable official-calendar archive.

Default: READ ONLY.
--write: create a new immutable calendar snapshot.

Does not authorize forecasts.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile

from datetime import datetime, timezone, timedelta
from pathlib import Path

import football_europe_epl_calendar_v2 as v2
import football_europe_epl_calendar_v3 as v3
from football_europe_sources_v3 import (
    SOURCES,
    fetch,
    Parser,
)

ROOT = Path(__file__).resolve().parent

DEST = (
    ROOT / "state/football_live/calendars/epl"
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_html(raw, now):
    parser = Parser()
    parser.feed(raw.decode("utf-8", errors="replace"))

    text = " ".join(parser.parts)

    timed, skipped = v3.extract(text, now)

    horizon = now + timedelta(days=14)

    records = []

    for item in timed:
        kickoff = datetime.fromisoformat(
            item["kickoff_utc"]
        )

        if kickoff > horizon:
            continue

        records.append({
            "date": item["date"],
            "home": item["home"],
            "away": item["away"],
            "kickoff_utc": item["kickoff_utc"],
            "status": "TIMED",
        })

    for date, home, away, reason in skipped:

        if not now.date() <= date <= horizon.date():
            continue

        records.append({
            "date": date.isoformat(),
            "home": home,
            "away": away,
            "kickoff_utc": None,
            "status": "DISCOVERED",
            "reason": reason,
        })

    keys = [
        (r["date"], r["home"], r["away"])
        for r in records
    ]

    if len(keys) != len(set(keys)):
        raise RuntimeError(
            "Duplicate calendar match identities"
        )

    records.sort(
        key=lambda r: (
            r["date"],
            r["kickoff_utc"] or "",
            r["home"],
            r["away"],
        )
    )

    return records


def validate_existing(path, snapshot_id):

    manifest_path = path / "manifest.json"
    calendar_path = path / "calendar.json"
    html_path = path / "official.html"

    if not all(
        p.is_file()
        for p in (
            manifest_path,
            calendar_path,
            html_path,
        )
    ):
        raise RuntimeError(
            "Existing snapshot is incomplete"
        )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    html = html_path.read_bytes()
    calendar = calendar_path.read_bytes()

    if manifest["snapshot_id"] != snapshot_id:
        raise RuntimeError("Snapshot ID mismatch")

    if sha(html) != manifest["html_sha256"]:
        raise RuntimeError("Archived HTML mismatch")

    if sha(calendar) != manifest["calendar_sha256"]:
        raise RuntimeError("Archived calendar mismatch")

    actual_id = sha(
        (
            manifest["html_sha256"]
            + manifest["calendar_sha256"]
        ).encode("ascii")
    )

    if actual_id != snapshot_id:
        raise RuntimeError("Snapshot identity mismatch")


def main():

    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    print("=== BUBUS EPL CALENDAR SNAPSHOT V1 ===")

    mode = "WRITE" if args.write else "READ ONLY"

    print("Mode:", mode)

    response_started = datetime.now(timezone.utc)

    status, content_type, raw = fetch(SOURCES["EPL"])

    fetched = datetime.now(timezone.utc)

    if status != 200:
        raise RuntimeError(
            f"Official source returned HTTP {status}"
        )

    if "html" not in content_type.lower():
        raise RuntimeError("Unexpected content type")

    if fetched < response_started:
        raise RuntimeError("System clock moved backwards")

    records = parse_html(raw, fetched)

    if not records:
        raise RuntimeError("Empty EPL calendar")

    calendar = {
        "schema": "bubus-epl-calendar-v1",
        "competition": "E0",
        "source_url": SOURCES["EPL"],
        "window_start_utc": fetched.isoformat(),
        "window_end_utc": (
            fetched + timedelta(days=14)
        ).isoformat(),
        "matches": records,
    }

    calendar_raw = encode(calendar)

    html_hash = sha(raw)
    calendar_hash = sha(calendar_raw)

    snapshot_id = sha(
        (html_hash + calendar_hash).encode("ascii")
    )

    manifest = {
        "schema": "bubus-epl-snapshot-v1",
        "snapshot_id": snapshot_id,
        "source_url": SOURCES["EPL"],
        "fetched_utc": fetched.isoformat(),
        "html_sha256": html_hash,
        "calendar_sha256": calendar_hash,
        "matches": len(records),
    }

    timed = sum(
        r["status"] == "TIMED"
        for r in records
    )

    discovered = sum(
        r["status"] == "DISCOVERED"
        for r in records
    )

    print("Snapshot:", snapshot_id)
    print("HTML SHA256:", html_hash)
    print("Matches:", len(records))
    print("Timed:", timed)
    print("Without confirmed time:", discovered)

    print("\n=== CALENDAR ===")

    for item in records:
        print(
            item["date"],
            item["status"],
            item["home"],
            "—",
            item["away"],
            "|",
            item["kickoff_utc"] or "UNKNOWN",
        )

    print("\n=== ARCHIVE ===")

    if not args.write:
        print("READ ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return

    DEST.mkdir(parents=True, exist_ok=True)

    target = DEST / snapshot_id

    if target.exists():
        validate_existing(target, snapshot_id)
        print("Existing snapshot validated")
        return

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=DEST,
        )
    )

    try:
        (staging / "official.html").write_bytes(raw)

        (staging / "calendar.json").write_bytes(
            calendar_raw
        )

        (staging / "manifest.json").write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Validate the staged archive before publishing it.
        validate_existing(staging, snapshot_id)

        os.rename(staging, target)

        print("SNAPSHOT SAVED:", target)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
