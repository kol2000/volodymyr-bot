#!/usr/bin/env python3
"""Research-only V8 observations without legacy forecast."""

import json
import os
import re

from datetime import datetime, timezone
from pathlib import Path

from football_forecast_v8_shadow import load_shadow


JOURNAL = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_v8_shadow/observations.jsonl"
)


def observe_unavailable_legacy(
    match,
    snapshot_id,
    reason,
    now=None,
):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    shadow = load_shadow(match, snapshot_id, now=now)
    batch_id = shadow.get("batch_id")
    fingerprint = shadow.get("data_fingerprint")

    if not isinstance(batch_id, str) or not batch_id:
        raise RuntimeError("Shadow cache has no batch_id")
    if (
        not isinstance(fingerprint, str)
        or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    ):
        raise RuntimeError("Shadow cache has no valid data_fingerprint")

    p = shadow["probabilities"]
    return {
        "schema": "bubus_v8_observation_v2",
        "created_utc": now.isoformat(),
        "match_id": match.id,
        "competition": match.competition,
        "home": match.home,
        "away": match.away,
        "kickoff_utc": datetime.fromtimestamp(
            match.kickoff, timezone.utc
        ).isoformat(),
        "batch_id": batch_id,
        "snapshot_id": shadow["snapshot_id"],
        "data_fingerprint": fingerprint,
        "legacy_status": "LEGACY_UNAVAILABLE",
        "legacy_reason": reason,
        "legacy_probabilities": None,
        "v8_probabilities": {
            "home": p["home"] * 100,
            "draw": p["draw"] * 100,
            "away": p["away"] * 100,
        },
        "published_v8": False,
        "production_eligible": False,
        "source": "football_predictions_shadow_hook",
    }


def append_observation(record):
    from football_forecast_v8_journal import JOURNAL, write_observations
    write_observations([record])
    return JOURNAL
