#!/usr/bin/env python3
"""Bubus Ligue 1 Immutable Season Archive V1.

Season: 2026/27

Source files:
- Official LFP PDF
- Open Matchday fixtures CSV
- Open Matchday results CSV

Outputs:
- calendar.json
- states.json
- provider_schedule.json
- original source files
- manifest.json

--check : read-only source validation
--write : create immutable season archive

No observations.
No forecasts.
"""

import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile

from pathlib import Path

import football_europe_ligue1_sources_v1 as sources
import football_europe_ligue1_season_audit_v1 as season
import football_europe_ligue1_lfp_validation_v1 as lfp
import football_europe_ligue1_calendar_v1 as calendar


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars"

ARCHIVES = BASE / "ligue1_season"

LOCK = BASE / "ligue1_season.lock"

SCHEMA = "bubus-ligue1-season-archive-v1"

SOURCE_FILES = {
    "lfp_official.pdf": "LFP_OFFICIAL_PDF",
    "fixtures_2627.csv": "OPEN_MATCHDAY_FIXTURES",
    "results_2627.csv": "OPEN_MATCHDAY_RESULTS",
}

DATASET_FILES = (
    "calendar.json",
    "states.json",
    "provider_schedule.json",
)


def require(condition, message):

    if not condition:
        raise RuntimeError(message)


def sha(raw):

    return hashlib.sha256(raw).hexdigest()


def canonical(value):

    return calendar.canonical(value)


def decode_csv(raw):

    text = raw.decode("utf-8-sig")

    reader = csv.DictReader(
        io.StringIO(text)
    )

    require(
        reader.fieldnames is not None,
        "CSV header missing",
    )

    rows = list(reader)

    require(
        len(rows) > 0,
        "CSV is empty",
    )

    return rows


def parse_pdf(raw):

    require(
        raw.startswith(b"%PDF-"),
        "Invalid official PDF",
    )

    executable = shutil.which("pdftotext")

    require(
        executable is not None,
        "pdftotext unavailable",
    )

    with tempfile.TemporaryDirectory(
        prefix="bubus-ligue1-pdf-"
    ) as tmp:

        path = Path(tmp) / "source.pdf"

        path.write_bytes(raw)

        result = subprocess.run(
            [
                executable,
                "-layout",
                "-nopgbrk",
                "-enc",
                "UTF-8",
                str(path),
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

    official, groups, problems = (
        lfp.parse_official_calendar(
            result.stdout
        )
    )

    require(
        not problems,
        "Official PDF parser errors",
    )

    require(
        len(official) == 306,
        "Expected 306 official matches",
    )

    require(
        len({
            lfp.fixture_key(item)
            for item in official
        }) == 306,
        "Duplicate official matches",
    )

    return official


def normalize_sources(raw_sources):

    official = parse_pdf(
        raw_sources["lfp_official.pdf"]
    )

    fixture_rows = decode_csv(
        raw_sources["fixtures_2627.csv"]
    )

    result_rows = decode_csv(
        raw_sources["results_2627.csv"]
    )

    fixtures = [
        season.parse_match(row, False)
        for row in fixture_rows
    ]

    results = [
        season.parse_match(row, True)
        for row in result_rows
    ]

    matches = fixtures + results

    require(
        len(matches) == 306,
        "Expected 306 provider matches",
    )

    datasets = calendar.normalize(
        official,
        matches,
    )

    return datasets


def download_sources():

    result = {}

    for filename, source_name in SOURCE_FILES.items():

        print(
            "Downloading:",
            source_name,
        )

        url = sources.SOURCES[
            source_name
        ]

        status, content_type, raw = (
            sources.download(url)
        )

        require(
            status == 200,
            source_name + ": HTTP error",
        )

        if filename.endswith(".pdf"):

            require(
                raw.startswith(b"%PDF-"),
                "Invalid official PDF",
            )

        result[filename] = raw

        print(
            "  Bytes:",
            len(raw),
        )

        print(
            "  SHA256:",
            sha(raw),
        )

    return result


def build_files(raw_sources):

    content, states, provider = (
        normalize_sources(raw_sources)
    )

    calendar_id = calendar.dataset_id(
        content
    )

    states_id = calendar.dataset_id(
        states
    )

    provider_id = calendar.dataset_id(
        provider
    )

    files = dict(raw_sources)

    files["calendar.json"] = canonical(
        content
    )

    files["states.json"] = canonical(
        states
    )

    files["provider_schedule.json"] = canonical(
        provider
    )

    manifest = {
        "schema": SCHEMA,
        "season": "2026-27",
        "competition": "ligue1",
        "calendar_id": calendar_id,
        "initial_states_id": states_id,
        "initial_provider_id": provider_id,
        "match_count": 306,
        "source_names": SOURCE_FILES,
        "files": {
            name: {
                "sha256": sha(raw),
                "bytes": len(raw),
            }
            for name, raw in sorted(
                files.items()
            )
        },
    }

    files["manifest.json"] = canonical(
        manifest
    )

    return (
        calendar_id,
        states_id,
        provider_id,
        files,
    )


def validate_archive(directory):

    manifest_path = (
        directory / "manifest.json"
    )

    require(
        manifest_path.is_file(),
        "Missing manifest",
    )

    manifest_raw = manifest_path.read_bytes()

    manifest = json.loads(
        manifest_raw
    )

    require(
        canonical(manifest) == manifest_raw,
        "Noncanonical manifest",
    )

    require(
        manifest["schema"] == SCHEMA,
        "Archive schema mismatch",
    )

    require(
        manifest["match_count"] == 306,
        "Archive match count mismatch",
    )

    require(
        manifest["source_names"] == SOURCE_FILES,
        "Unexpected source definitions",
    )

    expected_names = (
        set(SOURCE_FILES)
        | set(DATASET_FILES)
    )

    require(
        set(manifest["files"]) == expected_names,
        "Archive file list mismatch",
    )

    actual_names = {
        p.name
        for p in directory.iterdir()
        if p.is_file()
    }

    require(
        actual_names
        == expected_names | {"manifest.json"},
        "Unexpected or missing archive files",
    )

    files = {}

    for name, metadata in manifest["files"].items():

        path = directory / name

        require(
            path.is_file(),
            "Missing file: " + name,
        )

        raw = path.read_bytes()

        require(
            sha(raw) == metadata["sha256"],
            "Checksum mismatch: " + name,
        )

        require(
            len(raw) == metadata["bytes"],
            "Size mismatch: " + name,
        )

        files[name] = raw

    original_sources = {
        name: files[name]
        for name in SOURCE_FILES
    }

    rebuilt = build_files(
        original_sources
    )

    (
        calendar_id,
        states_id,
        provider_id,
        reconstructed,
    ) = rebuilt

    require(
        directory.name == calendar_id,
        "Archive directory ID mismatch",
    )

    require(
        manifest["calendar_id"] == calendar_id,
        "Calendar ID mismatch",
    )

    require(
        manifest["initial_states_id"] == states_id,
        "States ID mismatch",
    )

    require(
        manifest["initial_provider_id"] == provider_id,
        "Provider ID mismatch",
    )

    for name in DATASET_FILES:

        require(
            files[name] == reconstructed[name],
            "Reconstruction mismatch: " + name,
        )

    return manifest


def sync_dir(path):

    fd = os.open(
        str(path),
        os.O_RDONLY,
    )

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_archive(calendar_id, files):

    BASE.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(LOCK, "a+b") as lock:

        fcntl.flock(
            lock.fileno(),
            fcntl.LOCK_EX,
        )

        ARCHIVES.mkdir(
            parents=True,
            exist_ok=True,
        )

        existing = [
            path
            for path in ARCHIVES.iterdir()
        ]

        target = ARCHIVES / calendar_id

        if existing:

            require(
                len(existing) == 1
                and existing[0] == target,
                "A different or unexpected archive exists",
            )

            validate_archive(target)

            print(
                "Existing immutable archive: VALID"
            )

            print(
                "No files overwritten"
            )

            return target, False

        staging = Path(
            tempfile.mkdtemp(
                prefix=".ligue1-stage-",
                dir=ARCHIVES,
            )
        )

        try:

            for name, raw in files.items():

                path = staging / name

                with open(path, "xb") as output:

                    output.write(raw)
                    output.flush()
                    os.fsync(
                        output.fileno()
                    )

            staging_validation_path(
                staging,
                calendar_id,
            )

        except Exception:

            shutil.rmtree(
                staging,
                ignore_errors=True,
            )

            raise

        # Stage validation uses the calendar ID as
        # the directory name. Move only after validation.

        os.rename(
            staging,
            target,
        )

        sync_dir(ARCHIVES)

        validate_archive(target)

        return target, True


def staging_validation_path(staging, identifier):

    # validate_archive checks that directory name
    # matches content ID. Validate staging content
    # using a temporary sibling with that name would
    # be unsafe if target already exists.
    #
    # Instead validate the staged file reconstruction
    # independently before publishing.

    manifest = json.loads(
        (staging / "manifest.json").read_bytes()
    )

    require(
        manifest["calendar_id"] == identifier,
        "Staging calendar ID mismatch",
    )

    raw_sources = {
        name: (staging / name).read_bytes()
        for name in SOURCE_FILES
    }

    rebuilt_id, states_id, provider_id, rebuilt = (
        build_files(raw_sources)
    )

    require(
        rebuilt_id == identifier,
        "Staging reconstruction failed",
    )

    require(
        manifest["initial_states_id"] == states_id,
        "Staging states mismatch",
    )

    require(
        manifest["initial_provider_id"] == provider_id,
        "Staging provider mismatch",
    )

    for name, raw in rebuilt.items():

        require(
            (staging / name).read_bytes() == raw,
            "Staging file mismatch: " + name,
        )

    # validate_archive requires the published name.
    # Return a lightweight path-like adapter is not
    # necessary: the above checks are sufficient here.

    return staging


def main():

    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--check",
        action="store_true",
    )

    group.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "=== BUBUS LIGUE 1 SEASON ARCHIVE V1 ==="
    )

    print(
        "MODE:",
        "WRITE" if args.write else "READ ONLY",
    )

    print("\n=== ORIGINAL SOURCES ===")

    raw_sources = download_sources()

    print("\n=== NORMALIZATION ===")

    (
        calendar_id,
        states_id,
        provider_id,
        files,
    ) = build_files(
        raw_sources
    )

    print(
        "Calendar ID:",
        calendar_id,
    )

    print(
        "States ID:",
        states_id,
    )

    print(
        "Provider ID:",
        provider_id,
    )

    print("\n=== ARCHIVE CONTENT ===")

    for name, raw in files.items():

        print(
            name,
            len(raw),
            "bytes",
        )

    if args.check:

        print("\n=== FINAL STATUS ===")

        print("SOURCE VALIDATION: PASSED")
        print("306 MATCHES: VERIFIED")
        print("ARCHIVE PREVIEW: READY")
        print("NO ARCHIVES CREATED")
        print("NO PROJECT STATE WRITES")
        print("FORECAST RECORDING: BLOCKED")

        return

    print("\n=== WRITING IMMUTABLE ARCHIVE ===")

    target, created = write_archive(
        calendar_id,
        files,
    )

    print("Archive:", target)

    print(
        "Created:",
        created,
    )

    print("\n=== FINAL STATUS ===")

    print("ARCHIVE: VALID")
    print("SOURCE RECONSTRUCTION: VERIFIED")
    print("IMMUTABLE STORAGE: ENABLED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
