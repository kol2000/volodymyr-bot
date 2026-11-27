#!/usr/bin/env python3
"""Bubus EPL Season Archive V1.

Content-addressed full-season calendar archive.
Default READ ONLY. --write saves a new calendar version.

Forecast recording is never authorized here.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile

from pathlib import Path
from datetime import datetime, timezone

import football_europe_epl_calendar_v4 as v4
import football_europe_epl_calendar_v3 as v3

from football_europe_sources_v3 import (
    SOURCES,
    fetch,
    Parser,
)


ROOT = Path(__file__).resolve().parent

DEST = ROOT / "state/football_live/calendars/epl_season"


def encode(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(raw):
    parser = Parser()

    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    text = " ".join(parser.parts)

    timed, skipped = v3.extract(
        text,
        v4.SEASON_START,
    )

    original = []

    for item in timed:
        original.append({
            "date": item["date"],
            "home": item["home"],
            "away": item["away"],
            "kickoff_utc": item["kickoff_utc"],
            "status": "TIMED",
        })

    for date, home, away, reason in skipped:
        original.append({
            "date": date.isoformat(),
            "home": home,
            "away": away,
            "kickoff_utc": None,
            "status": "DISCOVERED",
            "reason": reason,
        })

    records, conflicts, unresolved = v4.normalize(original)

    problems = v4.validate(records)

    if problems or unresolved:
        raise RuntimeError(
            "Calendar structure invalid: "
            + repr(problems or unresolved)
        )

    # Keep only the actual calendar content in its identity.
    # No download timestamp or rolling observation window.
    content = {
        "schema": "bubus-epl-season-v1",
        "competition": "E0",
        "season": "2026-27",
        "source_url": SOURCES["EPL"],
        "matches": records,
        "conflicts": conflicts,
    }

    return content, len(original)


def validate_archive(path):
    manifest_path = path / "manifest.json"
    content_path = path / "calendar.json"
    html_path = path / "official.html"

    for p in (manifest_path, content_path, html_path):
        if not p.is_file():
            raise RuntimeError("Incomplete archive: " + str(path))

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    calendar_raw = content_path.read_bytes()
    html_raw = html_path.read_bytes()

    content_id = sha(calendar_raw)

    if content_id != path.name:
        raise RuntimeError("Directory/content ID mismatch")

    if content_id != manifest.get("content_id"):
        raise RuntimeError("Manifest content ID mismatch")

    if sha(html_raw) != manifest.get("html_sha256"):
        raise RuntimeError("HTML SHA mismatch")

    calendar = json.loads(calendar_raw)

    if (
        calendar.get("schema") != "bubus-epl-season-v1"
        or calendar.get("competition") != "E0"
        or calendar.get("season") != "2026-27"
    ):
        raise RuntimeError("Invalid calendar metadata")

    if calendar.get("source_url") != manifest.get("source_url"):
        raise RuntimeError("Source URL mismatch")

    if len(calendar["matches"]) != 380:
        raise RuntimeError("Invalid match count")

    problems = v4.validate(calendar["matches"])

    if problems:
        raise RuntimeError("Invalid stored structure")

    if sha(encode(calendar)) != content_id:
        raise RuntimeError("Non-canonical calendar JSON")

    # Verify the archived HTML reproduces the stored calendar.
    reconstructed, original_count = build(html_raw)

    if encode(reconstructed) != calendar_raw:
        raise RuntimeError("HTML/calendar mismatch")

    if original_count != manifest.get("original_records"):
        raise RuntimeError("Original count mismatch")

    return manifest


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--write",
        action="store_true"
    )

    args = ap.parse_args()

    print("=== BUBUS EPL SEASON V1 ===")
    print("Mode:", "WRITE" if args.write else "READ ONLY")

    status, content_type, raw = fetch(SOURCES["EPL"])

    fetched = datetime.now(timezone.utc)

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official source unavailable")

    content, original_count = build(raw)

    calendar_raw = encode(content)
    content_id = sha(calendar_raw)
    html_sha = sha(raw)

    records = content["matches"]
    conflicts = content["conflicts"]

    print("Original records:", original_count)
    print("Season matches:", len(records))
    print("Conflicts:", len(conflicts))
    print("Content ID:", content_id)
    print("Official HTML SHA256:", html_sha)

    target = DEST / content_id

    print("\n=== STATUS ===")

    if target.exists():
        existing = validate_archive(target)

        if existing["html_sha256"] != html_sha:
            print(
                "WARNING: Source HTML changed "
                "without calendar content changing."
            )

        print("CALENDAR CONTENT UNCHANGED")
        print("Existing archive validated")
        print("No new archive required")

        print("FORECAST RECORDING: BLOCKED")
        return 0

    if not args.write:
        print("NEW CALENDAR CONTENT DETECTED")
        print("READ ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return 0

    DEST.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "bubus-epl-season-archive-v1",
        "content_id": content_id,
        "source_url": SOURCES["EPL"],
        "fetched_utc": fetched.isoformat(),
        "html_sha256": html_sha,
        "original_records": original_count,
        "matches": len(records),
        "conflicts": len(conflicts),
    }

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

        # Staging directory is not named by content ID,
        # so validate its files before publication.
        if sha((staging / "calendar.json").read_bytes()) != content_id:
            raise RuntimeError("Staged content mismatch")

        if sha((staging / "official.html").read_bytes()) != html_sha:
            raise RuntimeError("Staged HTML mismatch")

        reconstructed, _ = build(
            (staging / "official.html").read_bytes()
        )

        if encode(reconstructed) != calendar_raw:
            raise RuntimeError("Staged calendar reconstruction failed")

        if target.exists():
            validate_archive(target)
            print("Archive created concurrently")
        else:
            os.rename(staging, target)
            validate_archive(target)
            print("ARCHIVE SAVED:", target)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    print("FORECAST RECORDING: BLOCKED")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
