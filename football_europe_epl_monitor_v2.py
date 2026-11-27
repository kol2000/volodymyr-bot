#!/usr/bin/env python3
"""Bubus EPL Calendar Monitor V2.

Default: read-only.
--write: archive changed calendar and append observation.

Never writes forecasts or modifies existing archives.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile

from datetime import datetime, timezone
from pathlib import Path

import football_europe_epl_season_v1 as season
import football_europe_epl_monitor_v1 as monitor

from football_europe_sources_v3 import (
    SOURCES,
    fetch,
)

ROOT = Path(__file__).resolve().parent

ARCHIVES = ROOT / "state/football_live/calendars/epl_season"

OBSERVATIONS = ROOT / "state/football_live/calendars/epl_observations"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def latest_observation():
    if not OBSERVATIONS.exists():
        return None

    items = sorted(
        p for p in OBSERVATIONS.glob("*.json")
        if p.is_file()
    )

    if not items:
        return None

    data = json.loads(
        items[-1].read_text(encoding="utf-8")
    )

    if data.get("schema") != "bubus-epl-observation-v2":
        raise RuntimeError("Invalid previous observation")

    return data


def archive_content(content, raw, fetched, original_count):
    calendar_raw = season.encode(content)

    content_id = sha(calendar_raw)
    target = ARCHIVES / content_id

    if target.exists():
        season.validate_archive(target)
        return content_id, False

    ARCHIVES.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "bubus-epl-season-archive-v1",
        "content_id": content_id,
        "source_url": SOURCES["EPL"],
        "fetched_utc": fetched.isoformat(),
        "html_sha256": sha(raw),
        "original_records": original_count,
        "matches": len(content["matches"]),
        "conflicts": len(content["conflicts"]),
    }

    staging = Path(
        tempfile.mkdtemp(
            prefix=".staging-",
            dir=ARCHIVES,
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

        if sha((staging / "official.html").read_bytes()) != sha(raw):
            raise RuntimeError("Staged HTML mismatch")

        if sha((staging / "calendar.json").read_bytes()) != content_id:
            raise RuntimeError("Staged calendar mismatch")

        reconstructed, count = season.build(raw)

        if (
            season.encode(reconstructed) != calendar_raw
            or count != original_count
        ):
            raise RuntimeError("Staged reconstruction mismatch")

        if target.exists():
            season.validate_archive(target)
            return content_id, False

        os.rename(staging, target)

        season.validate_archive(target)

        return content_id, True

    finally:
        if staging.exists():
            shutil.rmtree(staging)


def save_observation(data):
    OBSERVATIONS.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.fromisoformat(
        data["fetched_utc"]
    )

    name = (
        timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + data["content_id"][:12]
        + ".json"
    )

    target = OBSERVATIONS / name

    payload = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    # Exclusive creation: never overwrite an observation.
    with target.open("xb") as f:
        f.write(payload)

    print("OBSERVATION SAVED:", target)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--write",
        action="store_true",
    )

    args = ap.parse_args()

    print("=== BUBUS EPL MONITOR V2 ===")
    print("MODE:", "WRITE" if args.write else "READ ONLY")

    previous = latest_observation()

    if previous:
        old_id = previous["content_id"]
        old_path = ARCHIVES / old_id
    else:
        old_path, _ = monitor.load_latest()
        old_id = old_path.name

    season.validate_archive(old_path)

    old = json.loads(
        (old_path / "calendar.json").read_text(
            encoding="utf-8"
        )
    )

    status, content_type, raw = fetch(SOURCES["EPL"])

    fetched = datetime.now(timezone.utc)

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official source unavailable")

    current, original_count = season.build(raw)

    current_id = sha(season.encode(current))
    html_id = sha(raw)

    events = monitor.compare(
        old["matches"],
        current["matches"],
    )

    changed = current_id != old_id

    print("Previous content:", old_id)
    print("Current content: ", current_id)
    print("HTML SHA256:    ", html_id)

    print("Matches:", len(current["matches"]))
    print("Changes:", len(events))

    for event in events:
        print(
            "EVENT:",
            json.dumps(event, ensure_ascii=False)
        )

    if changed:
        print("STATUS: CALENDAR CONTENT CHANGED")
    else:
        print("STATUS: CALENDAR CONTENT UNCHANGED")

    if not args.write:
        print("READ ONLY — NO FILES MODIFIED")
        print("FORECAST RECORDING: BLOCKED")
        return

    archived_id, created = archive_content(
        current,
        raw,
        fetched,
        original_count,
    )

    observation = {
        "schema": "bubus-epl-observation-v2",
        "fetched_utc": fetched.isoformat(),
        "source_url": SOURCES["EPL"],
        "html_sha256": html_id,
        "content_id": archived_id,
        "previous_content_id": old_id,
        "calendar_changed": changed,
        "archive_created": created,
        "match_count": len(current["matches"]),
        "events": events,
    }

    # Archive first, observation second.
    save_observation(observation)

    print("Archive created:", created)
    print("Observation recorded: YES")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
