#!/usr/bin/env python3
"""Bubus LaLiga Observations Audit V1.

Validate observation chain, calendar archives,
source snapshots and recorded changes.

STRICTLY READ ONLY.
"""

import json
import re
import sys

from datetime import datetime, timezone
from pathlib import Path

import football_europe_laliga_season_v1 as season
import football_europe_laliga_calendar_v2 as calendar
import football_europe_laliga_monitor_v2 as monitor


BASE = Path(__file__).resolve().parent / (
    "state/football_live/calendars"
)

ARCHIVES = BASE / "laliga_season"
SOURCES = BASE / "laliga_observation_sources"
OBSERVATIONS = BASE / "laliga_observations"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def valid_hash(value):
    return isinstance(value, str) and bool(
        HASH_RE.fullmatch(value)
    )


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_calendar(content_id, cache):

    if not valid_hash(content_id):
        raise RuntimeError("Invalid calendar ID")

    if content_id not in cache:

        path = ARCHIVES / content_id

        season.validate_archive(path)

        content = load_json(path / "calendar.json")

        if calendar.content_id(content) != content_id:
            raise RuntimeError(
                "Calendar content ID mismatch"
            )

        cache[content_id] = content

    return cache[content_id]


def load_source(source_id, cache):

    if not valid_hash(source_id):
        raise RuntimeError("Invalid source ID")

    if source_id not in cache:

        path = SOURCES / source_id / "source_pages.json"

        raw = path.read_bytes()

        if monitor.sha(raw) != source_id:
            raise RuntimeError(
                "Source SHA256 mismatch"
            )

        pages = json.loads(raw)

        if season.encode(pages) != raw:
            raise RuntimeError(
                "Noncanonical source JSON"
            )

        content, statuses = season.reconstruct(pages)

        cache[source_id] = (content, statuses)

    return cache[source_id]


def initial_baseline(first_previous_id):

    content = load_calendar(
        first_previous_id,
        calendars,
    )

    path = ARCHIVES / first_previous_id

    pages = load_json(path / "source_pages.json")

    reconstructed, statuses = season.reconstruct(pages)

    if season.encode(reconstructed) != season.encode(content):
        raise RuntimeError(
            "Initial archive reconstruction mismatch"
        )

    return content, statuses


calendars = {}
sources = {}


def main():

    print("=== BUBUS LALIGA OBSERVATIONS AUDIT V1 ===")
    print("MODE: READ ONLY")

    if not OBSERVATIONS.is_dir():
        raise RuntimeError(
            "Observations directory missing"
        )

    files = sorted(OBSERVATIONS.glob("*.json"))

    if not files:
        raise RuntimeError("No observations found")

    previous_observation = None
    previous_statuses = None

    schedule_count = 0
    status_count = 0

    for path in files:

        data = load_json(path)

        if data.get("schema") != (
            "bubus-laliga-observation-v2"
        ):
            raise RuntimeError(
                "Invalid observation schema"
            )

        stamp = datetime.fromisoformat(
            data["fetched_utc"]
        )

        if stamp.tzinfo is None:
            raise RuntimeError(
                "Observation timestamp has no timezone"
            )

        stamp = stamp.astimezone(timezone.utc)

        if stamp > datetime.now(timezone.utc):
            raise RuntimeError(
                "Observation timestamp is in future"
            )

        current_id = data["content_id"]
        previous_id = data["previous_content_id"]
        source_id = data["source_sha256"]

        if not all(
            valid_hash(value)
            for value in (
                current_id,
                previous_id,
                source_id,
            )
        ):
            raise RuntimeError("Invalid SHA256 identifier")

        expected_name = (
            stamp.strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + current_id[:12]
            + ".json"
        )

        if path.name != expected_name:
            raise RuntimeError(
                "Observation filename mismatch"
            )

        current_calendar = load_calendar(
            current_id,
            calendars,
        )

        old_calendar = load_calendar(
            previous_id,
            calendars,
        )

        source_calendar, current_statuses = load_source(
            source_id,
            sources,
        )

        if season.encode(source_calendar) != season.encode(
            current_calendar
        ):
            raise RuntimeError(
                "Observation source/calendar mismatch"
            )

        if previous_observation is None:

            baseline, previous_statuses = initial_baseline(
                previous_id
            )

            if season.encode(baseline) != season.encode(
                old_calendar
            ):
                raise RuntimeError(
                    "Initial calendar baseline mismatch"
                )

        else:

            if stamp <= previous_observation["stamp"]:
                raise RuntimeError(
                    "Non-monotonic observation timestamps"
                )

            if previous_id != previous_observation["content_id"]:
                raise RuntimeError(
                    "Observation chain broken"
                )

        expected_schedule = monitor.compare_all(
            old_calendar,
            current_calendar,
        )

        expected_status = monitor.monitor.compare_statuses(
            previous_statuses,
            current_statuses,
        )

        if season.encode(expected_schedule) != season.encode(
            data["schedule_events"]
        ):
            raise RuntimeError(
                "Schedule events mismatch"
            )

        if season.encode(expected_status) != season.encode(
            data["status_events"]
        ):
            raise RuntimeError(
                "Match status events mismatch"
            )

        calendar_changed = current_id != previous_id

        if data["calendar_changed"] is not calendar_changed:
            raise RuntimeError(
                "Calendar changed flag mismatch"
            )

        if data["match_count"] != 380:
            raise RuntimeError(
                "Invalid match count"
            )

        schedule_count += len(expected_schedule)
        status_count += len(expected_status)

        print(
            "OK:",
            stamp.isoformat(),
            "| schedule:",
            len(expected_schedule),
            "| status:",
            len(expected_status),
        )

        previous_observation = {
            "stamp": stamp,
            "content_id": current_id,
        }

        previous_statuses = current_statuses

    print("\n=== SUMMARY ===")
    print("Observations:", len(files))
    print("Calendar archives validated:", len(calendars))
    print("Source snapshots validated:", len(sources))
    print("Schedule events:", schedule_count)
    print("Status events:", status_count)
    print("Observation chain: OK")
    print("Source/calendar consistency: OK")
    print("STATUS: AUDIT PASSED")
    print("READ ONLY — NO FILES MODIFIED")

    return 0


if __name__ == "__main__":

    try:
        sys.exit(main())

    except Exception as exc:
        print(
            "STATUS: AUDIT FAILED",
            type(exc).__name__,
            str(exc),
        )
        sys.exit(2)
