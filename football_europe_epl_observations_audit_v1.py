#!/usr/bin/env python3
"""Read-only audit of EPL observation history."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import football_europe_epl_season_v1 as season
import football_europe_epl_monitor_v1 as monitor

ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_live/calendars"
ARCHIVES = BASE / "epl_season"
OBSERVATIONS = BASE / "epl_observations"


def load_calendar(content_id, cache):
    if content_id not in cache:
        if (
            len(content_id) != 64
            or any(c not in "0123456789abcdef" for c in content_id)
        ):
            raise RuntimeError("Invalid content ID")

        directory = ARCHIVES / content_id

        season.validate_archive(directory)

        calendar = json.loads(
            (directory / "calendar.json").read_text(
                encoding="utf-8"
            )
        )

        cache[content_id] = calendar

    return cache[content_id]


def canonical(value):
    return season.encode(value)


def main():
    print("=== BUBUS EPL OBSERVATIONS AUDIT V1 ===")
    print("MODE: READ ONLY")

    if not OBSERVATIONS.is_dir():
        raise RuntimeError("Observation directory missing")

    files = sorted(OBSERVATIONS.glob("*.json"))

    if not files:
        raise RuntimeError("No observations found")

    cache = {}
    previous = None
    passed = 0

    for path in files:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )

        if data.get("schema") != "bubus-epl-observation-v2":
            raise RuntimeError(f"Invalid schema: {path.name}")

        stamp = datetime.fromisoformat(data["fetched_utc"])

        if stamp.tzinfo is None:
            raise RuntimeError("Timestamp lacks timezone")

        stamp = stamp.astimezone(timezone.utc)

        if stamp > datetime.now(timezone.utc):
            raise RuntimeError("Future observation timestamp")

        expected_prefix = stamp.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )

        if not path.name.startswith(expected_prefix + "-"):
            raise RuntimeError("Filename timestamp mismatch")

        current_id = data["content_id"]
        previous_id = data["previous_content_id"]

        expected_name = (
            expected_prefix + "-" + current_id[:12] + ".json"
        )

        if path.name != expected_name:
            raise RuntimeError("Observation filename mismatch")

        current = load_calendar(current_id, cache)
        old = load_calendar(previous_id, cache)

        if data["source_url"] != current["source_url"]:
            raise RuntimeError("Source URL mismatch")

        if data["match_count"] != len(current["matches"]):
            raise RuntimeError("Match count mismatch")

        expected_changed = current_id != previous_id

        if data["calendar_changed"] is not expected_changed:
            raise RuntimeError("Calendar change flag mismatch")

        actual_events = monitor.compare(
            old["matches"],
            current["matches"]
        )

        if canonical(data["events"]) != canonical(actual_events):
            raise RuntimeError("Calendar events mismatch")

        if previous is not None:
            if stamp <= previous["stamp"]:
                raise RuntimeError("Non-monotonic observation time")

            if previous_id != previous["content_id"]:
                raise RuntimeError("Observation chain broken")

        if not (
            isinstance(data.get("html_sha256"), str)
            and len(data["html_sha256"]) == 64
            and all(
                c in "0123456789abcdef"
                for c in data["html_sha256"]
            )
        ):
            raise RuntimeError("Invalid HTML SHA256 format")

        print(
            "OK:",
            stamp.isoformat(),
            "|",
            "CHANGED" if expected_changed else "UNCHANGED",
            "| events:",
            len(actual_events)
        )

        previous = {
            "stamp": stamp,
            "content_id": current_id
        }

        passed += 1

    print("\n=== SUMMARY ===")
    print("Observations:", passed)
    print("Validated archives:", len(cache))
    print("Chain continuity: OK")
    print("Recorded events: CONSISTENT")
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
            str(exc)
        )
        sys.exit(2)
