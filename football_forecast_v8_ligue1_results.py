#!/usr/bin/env python3
"""Ligue1 date-only result collection for the V8 Europe collector."""

import hashlib
import json

from datetime import datetime, timezone, date, time, timedelta
from pathlib import Path
from collections import Counter

import football_europe_ligue1_monitor_v2 as monitor
from football_forecast_v8_ligue1_history import historical_provider
from football_forecast_v8_ligue1_register_v2 import verify_existing


ROOT = Path(__file__).resolve().parent

FOLDER = ROOT / "state/football_v8_ligue1_shadow"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_utc(value):
    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    require(dt.tzinfo is not None, "Timezone missing")
    return dt.astimezone(timezone.utc)


def collect_ligue1(fixtures, snapshot_id, now):
    """Return new immutable events without writing any files."""

    print()
    print("=== LIGUE1 ===")

    paths = sorted(
        (FOLDER / "observations").glob("*.json")
    )

    require(
        len(paths) >= 18,
        f"Ligue1: expected at least 18 observations, got {len(paths)}",
    )

    _, _, provider, observation = (
        monitor.load_previous_observation()
    )

    require(
        observation is not None,
        "Ligue1: missing provider observation",
    )

    provider_id = monitor.dataset_id(provider)

    print("Current provider:", provider_id)

    provider_rows = {}

    for row in provider["matches"]:
        match_id = str(row["match_id"])

        require(
            match_id not in provider_rows,
            "Ligue1: duplicate provider match",
        )

        provider_rows[match_id] = row

    require(
        len(provider_rows) == 306,
        "Ligue1: invalid provider coverage",
    )

    planned = []
    stats = Counter()
    seen = set()
    history_cache = {}

    print("Observations:", len(paths))

    for path in paths:

        # Verify every forecast against its own immutable batch and calendar.
        match_identity = str(json.loads(path.read_bytes())["match_id"])
        obs = verify_existing(path, match_identity)
        history_key = obs["forecast_created_utc"]
        if history_key not in history_cache:
            history_cache[history_key] = historical_provider(history_key)
        history = history_cache[history_key]
        require(obs["provider_id"] == history["provider_id"],
                f"Ligue1: historical provider mismatch {match_identity}")
        historical_row = history["matches"].get(match_identity)
        require(historical_row is not None,
                f"Ligue1: historical match missing {match_identity}")
        require(historical_row["date"] == obs["provider_date"] == obs["forecast_date"],
                f"Ligue1: historical date mismatch {match_identity}")
        for field, stored in (("time", "provider_time_raw"),
                              ("time_source", "provider_time_source"),
                              ("time_status", "provider_time_status")):
            require(historical_row.get(field) == obs[stored],
                    f"Ligue1: historical {field} mismatch {match_identity}")

        require(
            obs["schema"]
            == "bubus_v8_ligue1_date_observation_v1",
            f"Ligue1: invalid observation schema: {path}",
        )

        require(
            obs["competition"] == "Ligue1",
            "Ligue1: competition mismatch",
        )

        require(
            obs["production_eligible"] is False,
            "Ligue1: unexpected production flag",
        )

        require(
            obs["kickoff_candidate_utc"] is None
            and obs["kickoff_verified"] is False,
            "Ligue1: unexpected kickoff verification",
        )

        match_id = str(obs["match_id"])

        require(
            match_id not in seen,
            f"Ligue1: duplicate observation {match_id}",
        )

        seen.add(match_id)

        require(
            obs["forecast"]["match_id"] == match_id,
            f"Ligue1: nested forecast mismatch {match_id}",
        )

        calendar_row = fixtures.get(("Ligue1", match_id))
        provider_row = provider_rows.get(match_id)

        require(
            calendar_row is not None
            and provider_row is not None,
            f"Ligue1: missing match {match_id}",
        )

        for field in ("home", "away"):
            require(
                calendar_row[field] == obs["forecast"][field],
                f"Ligue1: {field} mismatch {match_id}",
            )

        match_date = date.fromisoformat(
            obs["forecast_date"]
        )

        require(
            obs["forecast"]["date"]
            == obs["forecast_date"],
            f"Ligue1: forecast date mismatch {match_id}",
        )

        created = parse_utc(
            obs["forecast_created_utc"]
        )

        registered = parse_utc(
            obs["registered_utc"]
        )

        # No kickoff timezone is assumed.
        # Registration must precede the entire UTC calendar day.
        require(
            created <= registered
            and registered
            < datetime.combine(
                match_date,
                time.min,
                tzinfo=timezone.utc,
            ),
            f"Ligue1: unsafe registration timeline {match_id}",
        )

        if (
            provider_row.get("date") != obs["forecast_date"]
        ):
            stats["DATE_CHANGED"] += 1
            print("DATE CHANGED:", match_id)
            continue

        if calendar_row.get("state_status") != "FINISHED":
            stats["PENDING"] += 1
            continue

        if calendar_row.get("finished") is not True:
            stats["STATE_CONFLICT"] += 1
            print("STATE CONFLICT:", match_id)
            continue

        # Conservative date-only delay:
        # at least 48 hours after the calendar day has ended.
        eligible_after = datetime.combine(
            match_date + timedelta(days=3),
            time.min,
            tzinfo=timezone.utc,
        )

        if now < eligible_after:
            stats["TOO_EARLY"] += 1
            continue

        home_score = calendar_row.get("home_score")
        away_score = calendar_row.get("away_score")

        require(
            type(home_score) is int
            and type(away_score) is int
            and 0 <= home_score <= 30
            and 0 <= away_score <= 30,
            f"Ligue1: invalid final score {match_id}",
        )

        result_source = calendar_row.get("result_source")

        require(
            result_source == "restored_league_states",
            f"Ligue1: unexpected result source {match_id}",
        )

        identity = {
            "competition": "Ligue1",
            "match_id": match_id,
            "home_score": home_score,
            "away_score": away_score,
            "state_status": "FINISHED",
            "result_source": result_source,
        }

        match_hash = digest(
            f"Ligue1:{match_id}".encode()
        )[:20]

        result_hash = digest(
            canonical(identity)
        )[:20]

        target = (
            FOLDER
            / "result_events"
            / f"{match_hash}_{result_hash}.json"
        )

        if target.exists():
            saved = json.loads(target.read_bytes())

            require(
                saved.get("result_identity") == identity,
                f"Ligue1: existing result conflict {match_id}",
            )

            stats["EXISTING"] += 1
            continue

        event = {
            "schema": "bubus_v8_europe_result_event_v1",
            "result_identity": identity,

            "home": obs["forecast"]["home"],
            "away": obs["forecast"]["away"],

            "forecast_observation_file": path.name,
            "forecast_batch_sha256":
                obs["forecast_batch_sha256"],

            "forecast_created_utc":
                obs["forecast_created_utc"],

            "forecast_registered_utc":
                obs["registered_utc"],

            "forecast_date": obs["forecast_date"],

            "provider_time_raw":
                obs["provider_time_raw"],

            "provider_time_source":
                obs["provider_time_source"],

            "provider_time_status": "UNVERIFIED",

            "kickoff_candidate_utc": None,
            "kickoff_verified": False,

            "result_grade": "PROVIDER_ONLY",

            "result_date": None,

            "provider_source_sha256": None,

            "collection_snapshot_id": snapshot_id,
            "collected_utc": now.isoformat(),

            "date_only_delay":
                "48H_AFTER_CALENDAR_DAY_END",

            "production_eligible": False,
        }

        planned.append((target, event))
        stats["NEW"] += 1

        print(
            "NEW RESULT:",
            obs["forecast"]["home"],
            "—",
            obs["forecast"]["away"],
            f"{home_score}:{away_score}",
        )

    print(
        "Ligue1 | pending:", stats["PENDING"],
        "| too early:", stats["TOO_EARLY"],
        "| existing:", stats["EXISTING"],
        "| new:", stats["NEW"],
        "| date changed:", stats["DATE_CHANGED"],
        "| state conflict:", stats["STATE_CONFLICT"],
    )

    return planned, stats
