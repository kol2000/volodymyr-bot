#!/usr/bin/env python3
"""Bubus Bundesliga Season Archive V1.

OpenLigaDB 2026/27 -> immutable archive.

Default: READ ONLY.
--write: create archive if calendar Content ID is new.

The archive contains:
- calendar.json
- provider_schedule.json
- source.json
- manifest.json

STRICTLY NO FORECAST RECORDING.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request

from datetime import datetime, timezone
from pathlib import Path

import football_europe_bundesliga_calendar_v3 as calendar
import football_europe_bundesliga_season_audit_v1 as audit


ROOT = Path(__file__).resolve().parent

ARCHIVES = (
    ROOT
    / "state/football_live/calendars/bundesliga_season"
)

LOCK = (
    ROOT
    / "state/football_live/calendars/bundesliga_archive.lock"
)

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = "bubus-bundesliga-season-archive-v1"

MAX_RESPONSE = 5_000_000


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return calendar.canonical(value)


def download():

    request = urllib.request.Request(
        audit.URL,
        headers={
            "User-Agent": "Bubus-Football-Monitor/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        require(
            response.status == 200,
            "Unexpected HTTP status",
        )

        ctype = response.headers.get(
            "Content-Type", ""
        )

        require(
            "json" in ctype.lower(),
            "Unexpected content type",
        )

        raw = response.read(MAX_RESPONSE + 1)

    require(
        len(raw) <= MAX_RESPONSE,
        "API response too large",
    )

    data = json.loads(raw)

    require(
        isinstance(data, list),
        "Expected JSON match list",
    )

    return raw, data


def read_canonical(path):

    raw = path.read_bytes()

    value = json.loads(raw)

    require(
        canonical(value) == raw,
        "Noncanonical JSON: " + path.name,
    )

    return value, raw


def validate_archive(path):

    require(
        path.is_dir(),
        "Archive directory missing",
    )

    require(
        HASH_RE.fullmatch(path.name) is not None,
        "Invalid archive directory name",
    )

    expected_files = {
        "calendar.json",
        "provider_schedule.json",
        "source.json",
        "manifest.json",
    }

    actual_files = {
        item.name
        for item in path.iterdir()
        if item.is_file()
    }

    require(
        actual_files == expected_files,
        "Unexpected archive file set",
    )

    manifest, manifest_raw = read_canonical(
        path / "manifest.json"
    )

    content, content_raw = read_canonical(
        path / "calendar.json"
    )

    provider, provider_raw = read_canonical(
        path / "provider_schedule.json"
    )

    source_raw = (
        path / "source.json"
    ).read_bytes()

    require(
        manifest.get("schema") == SCHEMA,
        "Invalid manifest schema",
    )

    require(
        manifest.get("competition") == "DE1",
        "Invalid competition",
    )

    require(
        manifest.get("season") == "2026-27",
        "Invalid season",
    )

    require(
        manifest.get("source_url") == audit.URL,
        "Invalid source URL",
    )

    require(
        manifest.get("match_count") == 306,
        "Invalid match count",
    )

    require(
        sha(content_raw) == manifest["calendar_sha256"],
        "Calendar checksum mismatch",
    )

    require(
        sha(provider_raw) == manifest["provider_sha256"],
        "Provider schedule checksum mismatch",
    )

    require(
        sha(source_raw) == manifest["source_sha256"],
        "Source checksum mismatch",
    )

    require(
        manifest["calendar_id"] == path.name,
        "Calendar ID/path mismatch",
    )

    require(
        sha(content_raw) == path.name,
        "Calendar Content ID mismatch",
    )

    require(
        sha(provider_raw) == manifest["provider_id"],
        "Provider Content ID mismatch",
    )

    source = json.loads(source_raw)

    rebuilt_content, rebuilt_states, rebuilt_provider = (
        calendar.normalize(source)
    )

    require(
        canonical(rebuilt_content) == content_raw,
        "Calendar reconstruction mismatch",
    )

    require(
        canonical(rebuilt_provider) == provider_raw,
        "Provider reconstruction mismatch",
    )

    require(
        len(rebuilt_states) == 306,
        "Invalid reconstructed states",
    )

    require(
        calendar.content_id(rebuilt_states)
        == manifest["initial_states_id"],
        "Initial states reconstruction mismatch",
    )

    require(
        len(content["matches"]) == 306,
        "Invalid calendar size",
    )

    require(
        len(provider) == 306,
        "Invalid provider schedule size",
    )

    return manifest


def sync_dir(path):

    fd = os.open(path, os.O_RDONLY)

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_archive(path, files):

    ARCHIVES.mkdir(
        parents=True,
        exist_ok=True,
    )

    require(
        not path.exists(),
        "Archive already exists",
    )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=ARCHIVES,
        )
    )

    try:

        for name, raw in files.items():

            require(
                Path(name).name == name,
                "Unsafe filename",
            )

            target = staging / name

            with target.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())

            require(
                target.read_bytes() == raw,
                "Staging verification failed",
            )

        sync_dir(staging)

        # All writers using this archive module
        # must hold the shared archive lock.
        #
        # No existing archive is replaced.

        require(
            not path.exists(),
            "Archive appeared during publication",
        )

        os.rename(staging, path)

        sync_dir(ARCHIVES)

    finally:

        if staging.exists():
            shutil.rmtree(staging)


def execute(write):

    print("=== BUBUS BUNDESLIGA SEASON ARCHIVE V1 ===")

    print(
        "MODE:",
        "WRITE" if write else "READ ONLY",
    )

    fetched = datetime.now(
        timezone.utc
    ).isoformat()

    source_raw, data = download()

    content, states, provider = (
        calendar.normalize(data)
    )

    content_raw = canonical(content)
    provider_raw = canonical(provider)

    calendar_id = sha(content_raw)
    states_id = calendar.content_id(states)
    provider_id = sha(provider_raw)

    print("\n=== DATASETS ===")

    print("Calendar matches:", len(content["matches"]))
    print("Match states:", len(states))
    print("Provider schedule:", len(provider))

    print("\n=== CONTENT IDS ===")

    print("Calendar:", calendar_id)
    print("States:", states_id)
    print("Provider:", provider_id)

    print("\n=== SOURCE ===")

    print("URL:", audit.URL)
    print("Bytes:", len(source_raw))
    print("SHA256:", sha(source_raw))

    archive = ARCHIVES / calendar_id

    manifest = {
        "schema": SCHEMA,
        "competition": "DE1",
        "season": "2026-27",
        "source_name": "OpenLigaDB",
        "source_url": audit.URL,
        "fetched_utc": fetched,
        "calendar_id": calendar_id,
        "calendar_sha256": calendar_id,
        "provider_id": provider_id,
        "provider_sha256": provider_id,
        "initial_states_id": states_id,
        "source_sha256": sha(source_raw),
        "match_count": 306,
    }

    if archive.exists():

        print("\n=== EXISTING ARCHIVE ===")

        validate_archive(archive)

        print("Archive: VALID")
        print("New archive: NOT REQUIRED")

    elif write:

        print("\n=== CREATING ARCHIVE ===")

        publish_archive(
            archive,
            {
                "calendar.json": content_raw,
                "provider_schedule.json": provider_raw,
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

    print("\n=== FINAL STATUS ===")

    print("Archive:", archive)

    print("CALENDAR: VALID")
    print("STATES: VALID")
    print("PROVIDER SCHEDULE: VALID")

    print("FORECAST RECORDING: BLOCKED")


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.write:
        execute(False)
        return

    LOCK.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LOCK.open("a+b") as lock:

        fcntl.flock(
            lock,
            fcntl.LOCK_EX,
        )

        execute(True)


if __name__ == "__main__":
    main()
