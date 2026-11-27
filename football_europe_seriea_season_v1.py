#!/usr/bin/env python3
"""Bubus Serie A Season Archive V1.

Official API -> immutable season archive.

Default: READ ONLY.
--write: create archive if content ID is new.

Never modifies existing archives or forecasts.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import football_europe_seriea_calendar_v1 as calendar

from football_europe_seriea_api_probe_v1 import (
    BASE,
    SEASON_ID,
    MAX_RESPONSE,
)


ROOT = Path(__file__).resolve().parent

ARCHIVES = (
    ROOT
    / "state/football_live/calendars/seriea_season"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return calendar.canonical(value)


def source_url():
    return (
        BASE
        + "/seasons/"
        + quote(SEASON_ID, safe="")
        + "/matches?locale=en-GB"
    )


def download():
    request = urllib.request.Request(
        source_url(),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        if response.status != 200:
            raise RuntimeError("Unexpected HTTP status")

        content_type = response.headers.get(
            "Content-Type", ""
        )

        if "json" not in content_type.lower():
            raise RuntimeError("Unexpected content type")

        raw = response.read(MAX_RESPONSE + 1)

    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("API response too large")

    data = json.loads(raw)

    return raw, data


def validate_archive(path):
    """Verify checksums and reconstruct the calendar."""

    if not path.is_dir():
        raise RuntimeError("Archive directory missing")

    if not HASH_RE.fullmatch(path.name):
        raise RuntimeError("Invalid archive directory name")

    manifest_path = path / "manifest.json"
    calendar_path = path / "calendar.json"
    source_path = path / "source.json"

    manifest = json.loads(
        manifest_path.read_bytes()
    )

    calendar_raw = calendar_path.read_bytes()
    source_raw = source_path.read_bytes()

    if manifest.get("schema") != (
        "bubus-seriea-season-archive-v1"
    ):
        raise RuntimeError("Invalid manifest schema")

    if sha(calendar_raw) != manifest["calendar_sha256"]:
        raise RuntimeError("Calendar checksum mismatch")

    if sha(source_raw) != manifest["source_sha256"]:
        raise RuntimeError("Source checksum mismatch")

    content = json.loads(calendar_raw)
    source = json.loads(source_raw)

    if canonical(content) != calendar_raw:
        raise RuntimeError("Noncanonical calendar")

    reconstructed, states = calendar.normalize(source)

    if canonical(reconstructed) != calendar_raw:
        raise RuntimeError(
            "Calendar reconstruction mismatch"
        )

    content_id = calendar.content_id(reconstructed)

    if content_id != path.name:
        raise RuntimeError("Directory Content ID mismatch")

    if manifest["content_id"] != content_id:
        raise RuntimeError("Manifest Content ID mismatch")

    if manifest["match_count"] != 380:
        raise RuntimeError("Invalid match count")

    if manifest["source_url"] != source_url():
        raise RuntimeError("Source URL mismatch")

    if len(states) != 380:
        raise RuntimeError("Invalid state count")

    return manifest


def publish_archive(path, files):
    """Stage files and publish without replacing existing data."""

    ARCHIVES.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raise FileExistsError(path)

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=ARCHIVES,
        )
    )

    try:
        for name, raw in files.items():

            if (
                Path(name).name != name
                or name in ("", ".", "..")
            ):
                raise RuntimeError("Unsafe filename")

            target = staging / name

            with target.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())

            if target.read_bytes() != raw:
                raise RuntimeError(
                    "Staging verification failed"
                )

        fd = os.open(staging, os.O_RDONLY)

        try:
            os.fsync(fd)
        finally:
            os.close(fd)

        # This script currently has a single writer.
        # The future automatic monitor will need a
        # shared exclusive lock for all archive writes.
        if path.exists():
            raise FileExistsError(path)

        os.rename(staging, path)

        fd = os.open(ARCHIVES, os.O_RDONLY)

        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print("=== BUBUS SERIE A SEASON ARCHIVE V1 ===")

    print(
        "MODE:",
        "WRITE" if args.write else "READ ONLY",
    )

    fetched = datetime.now(timezone.utc).isoformat()

    source_raw, data = download()

    content, states = calendar.normalize(data)

    content_raw = canonical(content)

    content_id = calendar.content_id(content)

    archive = ARCHIVES / content_id

    print("\n=== OFFICIAL API ===")
    print("Season:", content["season"])
    print("Matches:", len(content["matches"]))
    print("States:", len(states))

    print("\n=== CHECKSUMS ===")
    print("Content ID:", content_id)
    print("Source SHA256:", sha(source_raw))
    print("Calendar SHA256:", sha(content_raw))

    if sha(content_raw) != content_id:
        raise RuntimeError("Content ID mismatch")

    manifest = {
        "schema": "bubus-seriea-season-archive-v1",
        "competition": "IT1",
        "season": "2026-27",
        "content_id": content_id,
        "calendar_sha256": sha(content_raw),
        "source_sha256": sha(source_raw),
        "source_url": source_url(),
        "fetched_utc": fetched,
        "match_count": 380,
    }

    if archive.exists():

        print("\n=== EXISTING ARCHIVE ===")

        validate_archive(archive)

        print("Archive: VALID")
        print("New archive: NOT REQUIRED")

    elif args.write:

        print("\n=== CREATING ARCHIVE ===")

        publish_archive(
            archive,
            {
                "calendar.json": content_raw,
                "source.json": source_raw,
                "manifest.json": canonical(manifest),
            },
        )

        validate_archive(archive)

        print("Archive: CREATED")
        print("Reconstruction: VERIFIED")

    else:

        print("\n=== DRY RUN ===")

        print("Archive does not exist")
        print("No files created")

    print("\n=== SUMMARY ===")

    print("Content ID:", content_id)
    print("Archive path:", archive)
    print("Archives modified:", bool(
        args.write and
        archive.exists()
    ))

    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
