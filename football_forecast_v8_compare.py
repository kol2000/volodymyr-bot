#!/usr/bin/env python3
"""Bubus V8 shadow model comparison.

Compares cached V8 probabilities with an already
calculated legacy forecast.

Does not invoke either prediction model.
Does not publish Telegram messages.
"""

import json
import math
import os
import re

from pathlib import Path
from datetime import datetime, timezone

from football_forecast_v8_shadow import (
    load_shadow,
    ShadowUnavailable,
)


JOURNAL = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_v8_shadow/comparisons.jsonl"
)


def extract_v8_probabilities(message):

    patterns = (
        r"(?m)^П1\s*[—–-]\s*(\d+(?:[,.]\d+)?)%",
        r"(?m)^Х\s*[—–-]\s*(\d+(?:[,.]\d+)?)%",
        r"(?m)^П2\s*[—–-]\s*(\d+(?:[,.]\d+)?)%",
    )

    values = []

    for pattern in patterns:

        matches = re.findall(pattern, message)

        if len(matches) != 1:
            raise ShadowUnavailable(
                "V8 probabilities missing or ambiguous"
            )

        value = float(
            matches[0].replace(",", ".")
        )

        if not math.isfinite(value):
            raise ShadowUnavailable(
                "Non-finite V8 probability"
            )

        values.append(value)

    if any(not 0 <= x <= 100 for x in values):
        raise ShadowUnavailable(
            "V8 probability out of range"
        )

    if abs(sum(values) - 100) > 0.2:
        raise ShadowUnavailable(
            "V8 probabilities do not sum to 100"
        )

    return values


def compare(match, legacy_probabilities, snapshot_id, now=None):

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now = now.astimezone(timezone.utc)

    if len(legacy_probabilities) != 3:
        raise ValueError(
            "Expected three legacy probabilities"
        )

    legacy = [
        float(x) for x in legacy_probabilities
    ]

    if (
        any(not math.isfinite(x) or x < 0 or x > 100
            for x in legacy)
        or abs(sum(legacy) - 100) > 2
    ):
        raise ValueError(
            "Invalid legacy probabilities"
        )

    shadow = load_shadow(
        match,
        snapshot_id,
        now=now,
    )

    # Read structured values from the validated
    # cache instead of parsing the Telegram text.

    probabilities = shadow["probabilities"]

    v8 = [
        probabilities["home"] * 100,
        probabilities["draw"] * 100,
        probabilities["away"] * 100,
    ]

    difference = [
        round(v8[i] - legacy[i], 2)
        for i in range(3)
    ]

    record = {
        "schema": "bubus_v8_comparison_v1",
        "created_utc": now.isoformat(),
        "match_id": match.id,
        "competition": match.competition,
        "home": match.home,
        "away": match.away,
        "kickoff": match.kickoff,
        "snapshot_id": snapshot_id,
        "legacy": legacy,
        "v8": v8,
        "difference_pp": difference,
        "published_v8": False,
    }

    return record


def append_comparison(record):

    JOURNAL.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = (
        json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(
        JOURNAL,
        flags,
        0o600,
    )

    try:
        with os.fdopen(fd, "ab") as f:
            f.write(payload)
    except Exception:
        raise

    return JOURNAL
