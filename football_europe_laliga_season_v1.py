#!/usr/bin/env python3
"""Bubus LaLiga Season Archive V1.

Content-addressed season archive.

Default: READ ONLY.
--write: save new immutable calendar version.

Never modifies forecasts or existing archives.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile

from datetime import datetime, timezone
from pathlib import Path

from football_europe_laliga_api_audit_v1 import get_key
from football_europe_laliga_season_audit_v1 import fetch_page

import football_europe_laliga_calendar_v1 as v1
import football_europe_laliga_calendar_v2 as v2


ROOT = Path(__file__).resolve().parent

DEST = (
    ROOT
    / "state/football_live/calendars/laliga_season"
)

OFFSETS = (0, 100, 200, 300)


def encode(value):
    return v1.canonical(value)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def download():

    key = get_key()

    pages = []
    matches = []

    for offset in OFFSETS:

        response = fetch_page(key, offset)

        if response.get("total") != 380:
            raise RuntimeError(
                "Unexpected API total"
            )

        expected = min(100, 380 - offset)

        if len(response["matches"]) != expected:
            raise RuntimeError(
                f"Invalid page size at {offset}"
            )

        pages.append({
            "offset": offset,
            "response": response,
        })

        matches.extend(response["matches"])

    ids = [m["id"] for m in matches]

    if len(matches) != 380 or len(set(ids)) != 380:
        raise RuntimeError(
            "Incomplete or duplicated season"
        )

    return pages, matches


def reconstruct(pages):

    if not isinstance(pages, list):
        raise RuntimeError("Invalid source pages")

    if len(pages) != len(OFFSETS):
        raise RuntimeError("Wrong page count")

    matches = []

    for item, expected_offset in zip(pages, OFFSETS):

        if item["offset"] != expected_offset:
            raise RuntimeError("Page offset mismatch")

        response = item["response"]

        if response["total"] != 380:
            raise RuntimeError("Page total mismatch")

        expected_count = min(
            100,
            380 - expected_offset,
        )

        if len(response["matches"]) != expected_count:
            raise RuntimeError("Page length mismatch")

        matches.extend(response["matches"])

    ids = [m["id"] for m in matches]

    if len(matches) != 380 or len(set(ids)) != 380:
        raise RuntimeError("Invalid reconstructed season")

    content, statuses = v2.normalize_stable(matches)

    return content, statuses


def validate_archive(path):

    if not path.is_dir():
        raise RuntimeError(
            "Archive missing: " + str(path)
        )

    manifest_file = path / "manifest.json"
    calendar_file = path / "calendar.json"
    pages_file = path / "source_pages.json"

    for file in (
        manifest_file,
        calendar_file,
        pages_file,
    ):
        if not file.is_file():
            raise RuntimeError(
                "Incomplete archive: " + str(path)
            )

    manifest = json.loads(
        manifest_file.read_text(encoding="utf-8")
    )

    calendar_raw = calendar_file.read_bytes()
    pages_raw = pages_file.read_bytes()

    content_id = sha(calendar_raw)

    if content_id != path.name:
        raise RuntimeError(
            "Archive directory ID mismatch"
        )

    if manifest.get("content_id") != content_id:
        raise RuntimeError(
            "Manifest content ID mismatch"
        )

    if manifest.get("source_sha256") != sha(pages_raw):
        raise RuntimeError(
            "Source pages checksum mismatch"
        )

    calendar = json.loads(calendar_raw)
    pages = json.loads(pages_raw)

    if encode(calendar) != calendar_raw:
        raise RuntimeError(
            "Noncanonical calendar JSON"
        )

    if encode(pages) != pages_raw:
        raise RuntimeError(
            "Noncanonical source JSON"
        )

    reconstructed, statuses = reconstruct(pages)

    if encode(reconstructed) != calendar_raw:
        raise RuntimeError(
            "Source/calendar reconstruction mismatch"
        )

    if calendar.get("schema") != v2.SCHEMA:
        raise RuntimeError("Unexpected calendar schema")

    if manifest.get("matches") != len(calendar["matches"]):
        raise RuntimeError("Manifest count mismatch")

    if manifest.get("source_url") != (
        "https://apim.laliga.com/"
        "public-service/api/v1/matches"
    ):
        raise RuntimeError("Unexpected source URL")

    return manifest


def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--write",
        action="store_true",
    )

    args = ap.parse_args()

    print("=== BUBUS LALIGA SEASON ARCHIVE V1 ===")
    print(
        "MODE:",
        "WRITE" if args.write else "READ ONLY",
    )

    fetched = datetime.now(timezone.utc)

    pages, matches = download()

    content, statuses = reconstruct(pages)

    calendar_raw = encode(content)
    pages_raw = encode(pages)

    content_id = sha(calendar_raw)
    source_id = sha(pages_raw)

    print("\n=== SOURCE ===")

    print("API pages:", len(pages))
    print("Matches:", len(matches))

    print("\n=== CONTENT ===")

    print("Content ID:", content_id)
    print("Source SHA256:", source_id)

    target = DEST / content_id

    print("\n=== ARCHIVE STATUS ===")

    if target.exists():

        validate_archive(target)

        print("CALENDAR CONTENT UNCHANGED")
        print("Existing archive validated")
        print("No new archive required")
        print("FORECAST RECORDING: BLOCKED")

        return 0

    if not args.write:

        print("NEW CALENDAR VERSION")
        print("READ ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")

        return 0

    DEST.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest = {
        "schema": "bubus-laliga-season-archive-v1",
        "content_id": content_id,
        "source_sha256": source_id,
        "source_url": (
            "https://apim.laliga.com/"
            "public-service/api/v1/matches"
        ),
        "fetched_utc": fetched.isoformat(),
        "matches": len(content["matches"]),
        "pages": len(pages),
    }

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=DEST,
        )
    )

    try:

        (staging / "calendar.json").write_bytes(
            calendar_raw
        )

        (staging / "source_pages.json").write_bytes(
            pages_raw
        )

        (staging / "manifest.json").write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if sha(
            (staging / "calendar.json").read_bytes()
        ) != content_id:
            raise RuntimeError(
                "Staged calendar checksum mismatch"
            )

        if sha(
            (staging / "source_pages.json").read_bytes()
        ) != source_id:
            raise RuntimeError(
                "Staged source checksum mismatch"
            )

        reconstructed, _ = reconstruct(
            json.loads(
                (staging / "source_pages.json").read_bytes()
            )
        )

        if encode(reconstructed) != calendar_raw:
            raise RuntimeError(
                "Staged reconstruction mismatch"
            )

        if target.exists():
            validate_archive(target)
            print("Archive already exists")
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
