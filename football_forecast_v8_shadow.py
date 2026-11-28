#!/usr/bin/env python3
"""Bubus V8 shadow cache reader.

Research-only cache validation.

No network calls.
No database writes.
No Telegram publication.
"""

import json
import math
import re

from pathlib import Path
from datetime import datetime, timezone


CACHE_DIR = Path(
    "/home/botadmin/volodymyr_bot/state/football_v8_shadow"
)


class ShadowUnavailable(Exception):
    pass


def parse_timestamp(value):

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            raise ValueError

        return dt.astimezone(timezone.utc)

    except (TypeError, ValueError):
        raise ShadowUnavailable(
            "Invalid cache timestamp"
        )


def load_shadow(match, snapshot_id, now=None):

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError(
            "now must be timezone-aware"
        )

    now = now.astimezone(timezone.utc)

    match_id = str(match.id)

    if not re.fullmatch(
        r"s365:\d+",
        match_id,
    ):
        raise ShadowUnavailable(
            "Unsupported match ID"
        )

    path = CACHE_DIR / (
        match_id.replace(":", "_") + ".json"
    )

    try:
        record = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        raise ShadowUnavailable(
            "Shadow cache unavailable"
        )

    if not isinstance(record, dict):
        raise ShadowUnavailable(
            "Invalid cache structure"
        )

    if record.get("schema") != "bubus_v8_shadow_v1":
        raise ShadowUnavailable(
            "Unsupported cache schema"
        )

    if record.get("match_id") != match_id:
        raise ShadowUnavailable(
            "Match ID mismatch"
        )

    if record.get("competition") != match.competition:
        raise ShadowUnavailable(
            "Competition mismatch"
        )

    if record.get("home") != match.home:
        raise ShadowUnavailable(
            "Home team mismatch"
        )

    if record.get("away") != match.away:
        raise ShadowUnavailable(
            "Away team mismatch"
        )

    if record.get("snapshot_id") != snapshot_id:
        raise ShadowUnavailable(
            "Statistics snapshot mismatch"
        )

    # Verify that this forecast belongs to the
    # currently published research batch.

    try:
        manifest = json.loads(
            (CACHE_DIR / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        raise ShadowUnavailable(
            "Shadow manifest unavailable"
        )

    if not isinstance(manifest, dict):
        raise ShadowUnavailable(
            "Invalid shadow manifest"
        )

    if manifest.get("schema") != (
        "bubus_v8_batch_manifest_v1"
    ):
        raise ShadowUnavailable(
            "Unsupported manifest schema"
        )

    if manifest.get("production_eligible") is not False:
        raise ShadowUnavailable(
            "Unexpected manifest eligibility"
        )

    if manifest.get("snapshot_id") != snapshot_id:
        raise ShadowUnavailable(
            "Manifest snapshot mismatch"
        )

    fingerprint = record.get("data_fingerprint")

    if not (
        isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    ):
        raise ShadowUnavailable(
            "Invalid data fingerprint"
        )

    if manifest.get("data_fingerprint") != fingerprint:
        raise ShadowUnavailable(
            "Historical data fingerprint mismatch"
        )

    batch_id = record.get("batch_id")

    if not isinstance(batch_id, str) or not batch_id:
        raise ShadowUnavailable(
            "Missing batch identity"
        )

    if manifest.get("batch_id") != batch_id:
        raise ShadowUnavailable(
            "Mixed cache generations"
        )

    match_ids = manifest.get("match_ids")

    if not isinstance(match_ids, list):
        raise ShadowUnavailable(
            "Invalid manifest match list"
        )

    if match_id not in match_ids:
        raise ShadowUnavailable(
            "Match absent from current batch"
        )

    manifest_expires = parse_timestamp(
        manifest.get("expires_utc")
    )

    if now >= manifest_expires:
        raise ShadowUnavailable(
            "Batch manifest expired"
        )

    # Never authorize publication from shadow cache.

    if record.get("production_eligible") is not False:
        raise ShadowUnavailable(
            "Unexpected production eligibility"
        )

    if record.get("verified") is not False:
        raise ShadowUnavailable(
            "Unexpected verification flag"
        )

    created = parse_timestamp(
        record.get("created_utc")
    )

    expires = parse_timestamp(
        record.get("expires_utc")
    )

    kickoff = parse_timestamp(
        record.get("kickoff_utc")
    )

    if not isinstance(
        match.kickoff,
        (int, float),
    ):
        raise ShadowUnavailable(
            "Invalid match kickoff"
        )

    if not math.isfinite(match.kickoff):
        raise ShadowUnavailable(
            "Non-finite match kickoff"
        )

    actual_kickoff = datetime.fromtimestamp(
        match.kickoff,
        timezone.utc,
    )

    if abs(
        (kickoff - actual_kickoff).total_seconds()
    ) > 1:
        raise ShadowUnavailable(
            "Kickoff mismatch"
        )

    if not created <= now < expires:
        raise ShadowUnavailable(
            "Cache expired or not yet valid"
        )

    if now >= kickoff:
        raise ShadowUnavailable(
            "Match has already started"
        )

    if expires > kickoff:
        raise ShadowUnavailable(
            "Cache expires after kickoff"
        )

    if match.status != "scheduled":
        raise ShadowUnavailable(
            "Match is not scheduled"
        )

    message = record.get("message")

    if not isinstance(message, str) or not message.strip():
        raise ShadowUnavailable(
            "Empty forecast"
        )

    probabilities = record.get("probabilities")

    if not isinstance(probabilities, dict):
        raise ShadowUnavailable(
            "Missing numeric probabilities"
        )

    values = []

    for key in ("home", "draw", "away"):

        value = probabilities.get(key)

        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ShadowUnavailable(
                "Invalid numeric probability"
            )

        values.append(value)

    if abs(sum(values) - 1) > 0.001:
        raise ShadowUnavailable(
            "Probability sum mismatch"
        )

    return {
        "probabilities": {
            "home": values[0],
            "draw": values[1],
            "away": values[2],
        },
        "match_id": match_id,
        "snapshot_id": snapshot_id,
        "created_utc": created,
        "expires_utc": expires,
        "message": message,
        "production_eligible": False,
        "batch_id": record.get("batch_id"),
        "data_fingerprint": record.get("data_fingerprint"),
    }
