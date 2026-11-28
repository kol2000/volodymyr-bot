#!/usr/bin/env python3
"""Bubus LaLiga Monitor V3.

Official API observation and calendar change detection.

Default: READ ONLY.
--write: record observation and archive new calendar content.

No forecast database writes.
"""

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import football_europe_laliga_season_v1 as season
import football_europe_laliga_calendar_v2 as calendar
import football_europe_laliga_monitor_v1 as monitor


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars"

ARCHIVES = BASE / "laliga_season"
OBSERVATIONS = BASE / "laliga_observations"
SOURCES = BASE / "laliga_observation_sources"

LOCK = BASE / "laliga_monitor.lock"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return season.encode(value)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_exclusive(path, raw):
    with path.open("xb") as output:
        output.write(raw)


def atomic_publish(directory, files):
    """Publish complete immutable directory atomically.

    Stage on the same filesystem, verify written bytes,
    then rename into place without overwriting.
    """

    import shutil

    directory = Path(directory)
    parent = directory.parent

    if not parent.is_dir():
        raise RuntimeError(
            "Archive parent directory missing"
        )

    if directory.exists():
        raise FileExistsError(
            "Archive already exists: " + str(directory)
        )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=parent,
        )
    )

    try:
        for name, raw in files.items():

            if (
                Path(name).name != name
                or name in ("", ".", "..")
            ):
                raise RuntimeError(
                    "Unsafe archive filename"
                )

            if not isinstance(raw, bytes):
                raise RuntimeError(
                    "Archive data must be bytes"
                )

            path = staging / name

            with path.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())

            if path.read_bytes() != raw:
                raise RuntimeError(
                    "Staged file verification failed"
                )

        # Persist directory entries before publication.
        fd = os.open(staging, os.O_RDONLY)

        try:
            os.fsync(fd)
        finally:
            os.close(fd)

        # Under the monitor's exclusive flock, no other
        # writer should publish this target concurrently.
        if directory.exists():
            raise FileExistsError(
                "Archive appeared concurrently"
            )

        os.rename(staging, directory)

        # Persist the published directory entry.
        fd = os.open(parent, os.O_RDONLY)

        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    finally:
        if staging.exists():
            shutil.rmtree(staging)


def calendar_from_archive(content_id):
    if (
        not isinstance(content_id, str)
        or len(content_id) != 64
        or any(c not in "0123456789abcdef" for c in content_id)
    ):
        raise RuntimeError("Invalid calendar content ID")

    path = ARCHIVES / content_id

    season.validate_archive(path)

    return read_json(path / "calendar.json")


def initial_archive():
    candidates = []

    if not ARCHIVES.is_dir():
        raise RuntimeError("Calendar archive directory missing")

    for path in ARCHIVES.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue

        manifest = season.validate_archive(path)

        candidates.append((
            manifest["fetched_utc"],
            path,
        ))

    if not candidates:
        raise RuntimeError("No calendar archives")

    candidates.sort()

    path = candidates[-1][1]

    return path.name


def previous_state():
    observations = sorted(
        OBSERVATIONS.glob("*.json")
    ) if OBSERVATIONS.exists() else []

    if observations:
        previous = read_json(observations[-1])

        if previous.get("schema") != "bubus-laliga-observation-v2":
            raise RuntimeError("Invalid previous observation")

        previous_id = previous["content_id"]

        calendar_from_archive(previous_id)

        source_sha = previous["source_sha256"]

        source_path = SOURCES / source_sha / "source_pages.json"

        if not source_path.is_file():
            raise RuntimeError("Previous source snapshot missing")

        raw = source_path.read_bytes()

        if sha(raw) != source_sha:
            raise RuntimeError("Previous source checksum mismatch")

        previous_calendar, statuses = season.reconstruct(
            json.loads(raw)
        )

        if calendar.content_id(previous_calendar) != previous_id:
            raise RuntimeError("Previous source/calendar mismatch")

        return previous_id, statuses, previous

    previous_id = initial_archive()

    archive_path = ARCHIVES / previous_id

    pages = read_json(
        archive_path / "source_pages.json"
    )

    _, statuses = season.reconstruct(pages)

    return previous_id, statuses, None


def compare_all(old_content, new_content):
    """Include team display names in schedule comparison."""

    events = monitor.compare(
        old_content["matches"],
        new_content["matches"],
    )

    old_by_id = monitor.index_by_id(
        old_content["matches"]
    )

    new_by_id = monitor.index_by_id(
        new_content["matches"]
    )

    event_by_id = {
        event["id"]: event
        for event in events
    }

    for match_id in sorted(set(old_by_id) & set(new_by_id)):
        old = old_by_id[match_id]
        new = new_by_id[match_id]

        name_changes = {}

        for field in ("home", "away"):
            if old.get(field) != new.get(field):
                name_changes[field] = {
                    "old": old.get(field),
                    "new": new.get(field),
                }

        if not name_changes:
            continue

        if match_id in event_by_id:
            event_by_id[match_id]["changes"].update(
                name_changes
            )
        else:
            event = {
                "type": "SCHEDULE_CHANGED",
                "id": match_id,
                "match": f"{new['home']} — {new['away']}",
                "changes": name_changes,
            }

            events.append(event)
            event_by_id[match_id] = event

    events.sort(key=lambda e: e["id"])

    return events


def save_source(pages_raw):
    source_id = sha(pages_raw)

    target = SOURCES / source_id

    if target.exists():
        file = target / "source_pages.json"

        if not file.is_file():
            raise RuntimeError("Incomplete source archive")

        if sha(file.read_bytes()) != source_id:
            raise RuntimeError("Source archive corrupted")

        return source_id, False

    SOURCES.mkdir(parents=True, exist_ok=True)

    atomic_publish(
        target,
        {"source_pages.json": pages_raw},
    )

    return source_id, True


def save_calendar(content, pages, fetched):
    content_id = calendar.content_id(content)

    target = ARCHIVES / content_id

    if target.exists():
        season.validate_archive(target)
        return False

    ARCHIVES.mkdir(parents=True, exist_ok=True)

    calendar_raw = encode(content)
    pages_raw = encode(pages)

    manifest = {
        "schema": "bubus-laliga-season-archive-v1",
        "content_id": content_id,
        "source_sha256": sha(pages_raw),
        "source_url": (
            "https://apim.laliga.com/"
            "public-service/api/v1/matches"
        ),
        "fetched_utc": fetched,
        "matches": 380,
        "pages": 4,
    }

    atomic_publish(
        target,
        {
            "calendar.json": calendar_raw,
            "source_pages.json": pages_raw,
            "manifest.json": json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        },
    )

    season.validate_archive(target)

    return True


def run(write):
    print("=== BUBUS LALIGA MONITOR V3 ===")
    print("MODE:", "WRITE" if write else "READ ONLY")

    previous_id, old_statuses, previous = previous_state()

    old_content = calendar_from_archive(previous_id)

    pages, matches = season.download()

    fetched = datetime.now(timezone.utc)

    new_content, new_statuses = season.reconstruct(pages)

    current_id = calendar.content_id(new_content)

    pages_raw = encode(pages)
    source_id = sha(pages_raw)

    schedule_events = compare_all(
        old_content,
        new_content,
    )

    status_events = monitor.compare_statuses(
        old_statuses,
        new_statuses,
    )

    changed = current_id != previous_id

    if changed and not schedule_events:
        raise RuntimeError(
            "Content changed without detected schedule events"
        )

    if not changed and schedule_events:
        raise RuntimeError(
            "Schedule events without content change"
        )

    print("\n=== CALENDAR ===")
    print("Previous:", previous_id)
    print("Current: ", current_id)
    print("Source:  ", source_id)
    print("Matches:", len(matches))

    print("\n=== CHANGES ===")
    print("Schedule events:", len(schedule_events))
    print("Status events:", len(status_events))

    for event in schedule_events[:20]:
        print(
            "SCHEDULE:",
            json.dumps(event, ensure_ascii=False)
        )

    for event in status_events[:20]:
        print(
            "STATUS:",
            json.dumps(event, ensure_ascii=False)
        )

    if not write:
        print("\nREAD ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return

    created = save_calendar(
        new_content,
        pages,
        fetched.isoformat(),
    )

    saved_source, source_created = save_source(
        pages_raw
    )

    if saved_source != source_id:
        raise RuntimeError("Source ID mismatch")

    observation = {
        "schema": "bubus-laliga-observation-v2",
        "fetched_utc": fetched.isoformat(),
        "content_id": current_id,
        "previous_content_id": previous_id,
        "source_sha256": source_id,
        "calendar_changed": changed,
        "archive_created": created,
        "schedule_events": schedule_events,
        "status_events": status_events,
        "match_count": len(matches),
    }

    OBSERVATIONS.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        fetched.strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + current_id[:12]
        + ".json"
    )

    target = OBSERVATIONS / filename

    write_exclusive(
        target,
        json.dumps(
            observation,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )

    print("\n=== SAVED ===")
    print("New calendar archive:", created)
    print("New source snapshot:", source_created)
    print("Observation:", target.name)
    print("FORECAST RECORDING: BLOCKED")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.write:
        run(False)
        return

    BASE.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        run(True)


if __name__ == "__main__":
    main()
