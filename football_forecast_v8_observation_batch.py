#!/usr/bin/env python3
"""Bubus V8 idempotent batch observation registrar.

Validates all records before writing.
Preserves existing observations.
Does not calculate Legacy forecasts.
Does not publish Telegram messages.
"""

import fcntl
import json
import math
import os
import re
import stat

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from football_forecast_v8_shadow import (
    CACHE_DIR,
    load_shadow,
)


JOURNAL = CACHE_DIR / "observations.jsonl"


def parse_time(value):

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError("Naive timestamp")

    return dt.astimezone(timezone.utc)


def build_observations():

    now = datetime.now(timezone.utc)

    manifest_path = CACHE_DIR / "manifest.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest.get("schema") != "bubus_v8_batch_manifest_v1":
        raise RuntimeError("Unsupported manifest")

    if manifest.get("production_eligible") is not False:
        raise RuntimeError("Unexpected production eligibility")

    batch_id = manifest.get("batch_id")
    snapshot_id = manifest.get("snapshot_id")
    fingerprint = manifest.get("data_fingerprint")

    if not all(isinstance(x, str) and x for x in (
        batch_id,
        snapshot_id,
        fingerprint,
    )):
        raise RuntimeError("Invalid batch identity")

    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("Invalid fingerprint")

    expires = parse_time(manifest["expires_utc"])

    if now >= expires:
        raise RuntimeError("Batch expired; regenerate forecasts")

    match_ids = manifest.get("match_ids")

    if not isinstance(match_ids, list) or not match_ids:
        raise RuntimeError("Invalid match list")

    if len(match_ids) != len(set(match_ids)):
        raise RuntimeError("Duplicate match IDs in manifest")

    observations = []

    for match_id in match_ids:

        if not re.fullmatch(r"s365:\d+", match_id):
            raise RuntimeError(f"Invalid match ID: {match_id}")

        path = CACHE_DIR / (
            match_id.replace(":", "_") + ".json"
        )

        row = json.loads(
            path.read_text(encoding="utf-8")
        )

        if row.get("match_id") != match_id:
            raise RuntimeError(f"Match ID mismatch: {match_id}")

        kickoff = parse_time(row["kickoff_utc"])

        match = SimpleNamespace(
            id=match_id,
            competition=row["competition"],
            home=row["home"],
            away=row["away"],
            kickoff=kickoff.timestamp(),
            status="scheduled",
        )

        shadow = load_shadow(
            match,
            snapshot_id,
            now=now,
        )

        p = shadow["probabilities"]

        values = [
            p["home"],
            p["draw"],
            p["away"],
        ]

        if not all(
            math.isfinite(x) and 0 <= x <= 1
            for x in values
        ):
            raise RuntimeError(
                f"Invalid probabilities: {match_id}"
            )

        if abs(sum(values) - 1) > 0.001:
            raise RuntimeError(
                f"Probability sum mismatch: {match_id}"
            )

        observations.append({
            "schema": "bubus_v8_observation_v2",
            "created_utc": now.isoformat(),

            "match_id": match_id,
            "competition": row["competition"],
            "home": row["home"],
            "away": row["away"],
            "kickoff_utc": kickoff.isoformat(),

            "batch_id": batch_id,
            "snapshot_id": snapshot_id,
            "data_fingerprint": fingerprint,

            "legacy_status": "NOT_EVALUATED",
            "legacy_reason": None,
            "legacy_probabilities": None,

            "v8_probabilities": {
                "home": p["home"] * 100,
                "draw": p["draw"] * 100,
                "away": p["away"] * 100,
            },

            "published_v8": False,
            "production_eligible": False,
            "source": "football_v8_shadow_cache",
        })

    # Ensure the manifest did not change during validation.

    current = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    for key in (
        "batch_id",
        "snapshot_id",
        "data_fingerprint",
        "expires_utc",
    ):
        if current.get(key) != manifest.get(key):
            raise RuntimeError(
                f"Manifest changed during validation: {key}"
            )

    if datetime.now(timezone.utc) >= expires:
        raise RuntimeError("Batch expired during validation")

    return observations


def append_once(observations):
    from football_forecast_v8_journal import write_observations
    return write_observations(observations)


def main():

    print("=== BUBUS V8 BATCH OBSERVATION ===")
    print("MODE: RESEARCH ONLY")

    observations = build_observations()
    print("Validated forecasts:", len(observations))

    added, updated, skipped = append_once(observations)

    print("\n=== RESULT ===")
    print("New observations:", added)
    print("Upgraded legacy statuses:", updated)
    print("Already registered:", skipped)
    print("Total processed:", added + updated + skipped)

    print("\nLegacy status: NOT_EVALUATED unless later evaluated")
    print("V8 publication: DISABLED")
    print("Production authorization: NOT GRANTED")


if __name__ == "__main__":
    main()
