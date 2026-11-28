#!/usr/bin/env python3
"""Experimental EPL result overlay for V8 research.

Read-only. Candidate results are NOT authoritative.
Never writes historical CSV, V8 cache or forecasts.
"""

import json

from datetime import date
from pathlib import Path


CANDIDATES = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_live/reconciliation_candidates/"
    "epl_20261121_20261122.json"
)


def apply_overlay(context, season, as_of):
    """Return an independent context with candidate results."""

    if not isinstance(as_of, date):
        raise TypeError("as_of must be a date")

    data = json.loads(
        CANDIDATES.read_text(encoding="utf-8")
    )

    if data.get("schema") != "bubus_epl_result_candidates_v1":
        raise RuntimeError("Unsupported candidate schema")

    if data.get("authoritative_for_v8") is not False:
        raise RuntimeError("Unexpected candidate authorization")

    if data.get("snapshot_id") != season["snapshot_id"]:
        raise RuntimeError("Snapshot mismatch")

    records = data["records"]

    if len(records) != 9:
        raise RuntimeError("Unexpected candidate count")

    fixtures = {
        (m["competition"], str(m["match_id"])): m
        for m in season["matches"]
    }

    if len(fixtures) != len(season["matches"]):
        raise RuntimeError("Duplicate fixture IDs")

    combined = list(context["combined"])
    bridge = context["bridge"]

    if not combined:
        raise RuntimeError("Empty historical context")

    Fixture = type(combined[0])

    existing = {
        (m.league, m.day, m.home, m.away): m
        for m in combined
    }

    if len(existing) != len(combined):
        raise RuntimeError("Duplicate historical matches")

    added = []
    seen = set()

    for row in records:

        if (
            row["competition"] != "EPL"
            or row["status"] != "EXTERNAL_RESULT_CANDIDATE"
        ):
            raise RuntimeError("Invalid candidate status")

        identity = ("EPL", str(row["match_id"]))

        if identity in seen:
            raise RuntimeError("Duplicate candidate")

        seen.add(identity)

        original = fixtures.get(identity)

        if original is None:
            raise RuntimeError("Unknown fixture")

        if (
            original["home"] != row["home"]
            or original["away"] != row["away"]
            or str(original["date"])[:10] != row["date"]
        ):
            raise RuntimeError("Fixture identity mismatch")

        day = date.fromisoformat(row["date"])

        # Do not inject results dated on or after cutoff.
        if day >= as_of:
            continue

        if original["state_status"] != "STATE_UNAVAILABLE":
            raise RuntimeError("Unexpected fixture state")

        home = bridge["EPL"][row["home"]]["historical_id"]
        away = bridge["EPL"][row["away"]]["historical_id"]

        score = (
            row["home_score"],
            row["away_score"],
        )

        if not all(
            type(x) is int and 0 <= x <= 30
            for x in score
        ):
            raise RuntimeError("Invalid score")

        key = ("EPL", day, home, away)

        if key in existing:
            if tuple(existing[key].goals) != score:
                raise RuntimeError("Historical score conflict")
            continue

        match = Fixture(
            league="EPL",
            day=day,
            home=home,
            away=away,
            goals=score,
        )

        combined.append(match)
        existing[key] = match
        added.append(match)

    combined.sort(
        key=lambda m: (
            m.day,
            m.league,
            m.home,
            m.away,
        )
    )

    result = dict(context)
    result["combined"] = combined
    result["epl_overlay_added"] = len(added)
    result["epl_overlay_authoritative"] = False

    return result
