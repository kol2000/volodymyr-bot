#!/usr/bin/env python3
"""Bubus Forecast V8 batch generator.

Bundesliga research shadow cache.

One historical context per execution.
No Telegram publication.
No SQLite writes.
Production use prohibited.
"""

import argparse
import hashlib
import json
import math
import os
import re
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import soccer365_football as soccer
import football_soccer365_v8_mapping_v1 as mapping_module
import football_forecast_data_live_v1 as loader
import football_forecast_research_v8_1 as research
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals

from football_forecast_formatter_v1 import render_forecast


MOSCOW = ZoneInfo("Europe/Moscow")

CACHE_DIR = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_v8_shadow"
)

LEAGUE = "Bundesliga"

ACCEPTED = {
    "FINISHED",
    "RESULT_REPORTED",
    "RESULT_CORROBORATED",
    "RESULT_PROVIDER_ONLY",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_day(value):
    if value is None:
        return None

    try:
        return datetime.fromisoformat(
            str(value)[:10]
        ).date()
    except (ValueError, TypeError):
        return None


def fingerprint(context, season):
    """Identify actual data content, not only CSV identity."""

    history = [
        [
            m.league,
            m.day.isoformat(),
            m.home,
            m.away,
            list(m.goals),
        ]
        for m in context["combined"]
    ]

    fixtures = [
        [
            row["competition"],
            str(row["match_id"]),
            str(row.get("date")),
            row.get("state_status"),
            row.get("home_score"),
            row.get("away_score"),
        ]
        for row in season["matches"]
    ]

    payload = json.dumps(
        {
            "snapshot": season["snapshot_id"],
            "history": history,
            "fixtures": fixtures,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def validate_bundesliga(season, today):

    rows = [
        row for row in season["matches"]
        if row["competition"] == LEAGUE
    ]

    require(
        len(rows) == 306,
        "Unexpected Bundesliga calendar size"
    )

    unresolved = []

    for row in rows:

        day = parse_day(row.get("date"))

        if day is None:
            unresolved.append((
                row["match_id"],
                "MISSING_DATE",
            ))
            continue

        if (
            day < today
            and row.get("state_status") not in ACCEPTED
        ):
            unresolved.append((
                row["match_id"],
                row.get("state_status"),
            ))

    require(
        not unresolved,
        "Bundesliga calendar blocked: "
        + repr(unresolved[:10])
    )

    return rows


def atomic_save(path, record):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = json.dumps(
        record,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".v8-batch-",
        suffix=".json",
        dir=path.parent,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def calculate(
    day,
    match,
    league,
    home,
    away,
    combined,
    bridge,
    ratings,
    past,
):

    home_id = bridge[league][home]["historical_id"]
    away_id = bridge[league][away]["historical_id"]

    fixture = {
        "league": league,
        "day": day,
        "home_id": home_id,
        "away_id": away_id,
    }

    prediction, reason = engine.forecast_at(
        fixture,
        past,
        ratings,
    )

    require(
        prediction is not None,
        f"Outcome unavailable: {reason}"
    )

    goals = totals.analyze(
        home_id,
        away_id,
        past,
        league,
        day,
    )

    require(
        goals is not None,
        "Totals unavailable"
    )

    probs = tuple(prediction["V3"])

    require(
        len(probs) == 3,
        "Invalid probability count"
    )

    require(
        all(
            math.isfinite(x) and 0 <= x <= 1
            for x in probs
        ),
        "Invalid probabilities"
    )

    require(
        abs(sum(probs) - 1) < 0.001,
        "Probabilities do not sum to one"
    )

    message = render_forecast(
        league=league,
        match_date=day,
        home=match.home,
        away=match.away,
        probabilities=probs,
        goals=goals,
        verified=False,
    )

    return probs, message


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Save research shadow cache"
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
    )

    args = parser.parse_args()

    require(
        1 <= args.days <= 14,
        "Days must be between 1 and 14"
    )

    print("=== BUBUS FORECAST V8 BATCH V1 ===")
    print("LEAGUE: Bundesliga")
    print(
        "MODE:",
        "WRITE RESEARCH CACHE"
        if args.write else "DRY RUN"
    )

    now = datetime.now(timezone.utc)
    batch_id = now.isoformat()
    today = now.astimezone(MOSCOW).date()

    print("Moscow date:", today)

    # ---------------------------------------
    # 1. Load one verified season snapshot
    # ---------------------------------------

    print("\n=== SEASON DATA ===")

    season = loader.load_current_matches()

    print("Snapshot:", season["snapshot_id"])

    validate_bundesliga(season, today)

    print("Bundesliga calendar: PASSED")

    # ---------------------------------------
    # 2. Build history exactly once
    # ---------------------------------------

    print("\n=== HISTORICAL CONTEXT ===")

    context = research.build_context(
        as_of=today,
        season_data=(
            season["matches"],
            season["counts"],
        ),
    )

    combined = context["combined"]
    bridge = context["bridge"]

    print("History:", len(combined))

    data_fingerprint = fingerprint(
        context,
        season,
    )

    print(
        "Data fingerprint:",
        data_fingerprint
    )

    # ---------------------------------------
    # 3. Index Bundesliga fixtures
    # ---------------------------------------

    fixtures = {}

    for day, league, row in context["upcoming"]:

        if league != LEAGUE:
            continue

        if not today < day <= today + timedelta(
            days=args.days
        ):
            continue

        key = (
            day,
            row["home"],
            row["away"],
        )

        require(
            key not in fixtures,
            f"Duplicate fixture: {key}"
        )

        fixtures[key] = row

    print("\nUpcoming Bundesliga fixtures:", len(fixtures))

    # ---------------------------------------
    # 4. Soccer365 team mapping
    # ---------------------------------------

    mapping = mapping_module.load_mapping()

    require(
        LEAGUE in mapping,
        "Bundesliga mapping missing"
    )

    # ---------------------------------------
    # 5. Obtain upcoming real match objects
    # ---------------------------------------

    matches = []

    for offset in range(1, args.days + 1):

        day = today + timedelta(days=offset)

        url = (
            "https://soccer365.ru/online/&date="
            + day.isoformat()
        )

        rows = soccer.listing(
            soccer.fetch(url)
        )

        for item in rows:

            league = mapping_module.LEAGUES.get(
                item["competition"]
            )

            if league != LEAGUE:
                continue

            home = mapping[league].get(
                soccer.identity(item["home"])
            )

            away = mapping[league].get(
                soccer.identity(item["away"])
            )

            require(
                home is not None and away is not None,
                "Unmapped Bundesliga team: "
                + item["home"] + " / " + item["away"]
            )

            key = (day, home, away)

            require(
                key in fixtures,
                f"Soccer365 fixture missing in V8: {key}"
            )

            match = soccer.broadcast(
                soccer.fetch(item["url"]),
                item,
            )

            require(
                match.id == item["id"],
                "Soccer365 match ID mismatch"
            )

            require(
                match.status == "scheduled",
                f"Match not scheduled: {match.id}"
            )

            require(
                match.kickoff > now.timestamp(),
                f"Match already started: {match.id}"
            )

            actual_day = datetime.fromtimestamp(
                match.kickoff,
                MOSCOW,
            ).date()

            require(
                actual_day == day,
                f"Kickoff date mismatch: {match.id}"
            )

            matches.append((
                day,
                league,
                home,
                away,
                match,
            ))

    # ---------------------------------------
    # 6. Verify complete fixture coverage
    # ---------------------------------------

    found = {
        (day, home, away)
        for day, league, home, away, match
        in matches
    }

    require(
        len(found) == len(matches),
        "Duplicate Soccer365 fixture mapping"
    )

    require(
        found == set(fixtures),
        "Incomplete Bundesliga fixture coverage"
    )

    match_ids = [m.id for *_, m in matches]

    require(
        len(match_ids) == len(set(match_ids)),
        "Duplicate Soccer365 match ID"
    )

    print("Soccer365 matched:", len(matches))

    if not matches:
        print("\nNO UPCOMING BUNDESLIGA MATCHES")
        print("Existing research cache: UNCHANGED")
        print("No Telegram messages")
        return

    # ---------------------------------------
    # 7. Calculate forecasts with date cache
    # ---------------------------------------

    ratings_cache = {}
    records = []

    for day, league, home, away, match in matches:

        if day not in ratings_cache:

            past = [
                m for m in combined
                if m.day < today
            ]

            ratings = engine.rating_before(
                combined,
                today,
            )

            ratings_cache[day] = (
                past,
                ratings,
            )

        past, ratings = ratings_cache[day]

        probs, message = calculate(
            day,
            match,
            league,
            home,
            away,
            combined,
            bridge,
            ratings,
            past,
        )

        created = datetime.now(timezone.utc)

        kickoff = datetime.fromtimestamp(
            match.kickoff,
            timezone.utc,
        )

        expires = min(
            created + timedelta(hours=1),
            kickoff,
        )

        record = {
            "schema": "bubus_v8_shadow_v1",

            "match_id": match.id,

            "competition": match.competition,

            "home": match.home,
            "away": match.away,

            "kickoff_utc": kickoff.isoformat(),

            "created_utc": created.isoformat(),
            "expires_utc": expires.isoformat(),

            "snapshot_id": season["snapshot_id"],

            "data_fingerprint": data_fingerprint,
            "batch_id": batch_id,

            "probabilities": {
                "home": probs[0],
                "draw": probs[1],
                "away": probs[2],
            },

            "verified": False,
            "production_eligible": False,

            "message": str(message),
        }

        records.append(record)

        print(
            f"{day} | "
            f"{match.home} — {match.away} | "
            f"П1 {probs[0]*100:.1f}% "
            f"Х {probs[1]*100:.1f}% "
            f"П2 {probs[2]*100:.1f}%"
        )

    # ---------------------------------------
    # 8. Write complete research batch
    # ---------------------------------------

    print("\n=== BATCH RESULT ===")

    print("Calculated:", len(records))

    if args.write:

        for record in records:

            require(
                re.fullmatch(
                    r"s365:\d+",
                    record["match_id"]
                ) is not None,
                "Invalid cache match ID"
            )

        # All forecasts have been calculated
        # before any cache entry is overwritten.

        for record in records:

            filename = (
                record["match_id"].replace(":", "_")
                + ".json"
            )

            path = CACHE_DIR / filename

            atomic_save(path, record)

            print("SAVED:", path)

        manifest = {
            "schema": "bubus_v8_batch_manifest_v1",
            "batch_id": batch_id,
            "snapshot_id": season["snapshot_id"],
            "data_fingerprint": data_fingerprint,
            "created_utc": now.isoformat(),
            "expires_utc": min(
                datetime.fromisoformat(r["expires_utc"])
                for r in records
            ).isoformat() if records else now.isoformat(),
            "match_ids": sorted(
                r["match_id"] for r in records
            ),
            "production_eligible": False,
        }

        atomic_save(
            CACHE_DIR / "manifest.json",
            manifest,
        )

        print("MANIFEST: SAVED")
        print("Batch ID:", batch_id)

        print(
            "Research cache records saved:",
            len(records)
        )

    else:

        print("DRY RUN: no cache files modified")

    print("\n=== FINAL STATUS ===")

    print("Bundesliga batch: PASSED")
    print("Historical context loads: 1")
    print("Forecasts:", len(records))

    print("Independent kickoff verification: PENDING")
    print("Source freshness: NOT VERIFIED")
    print("Point-in-time provenance: NOT VERIFIED")
    print("Production authorization: NOT GRANTED")

    print("No SQLite writes")
    print("No Telegram messages")
    print("No services restarted")


if __name__ == "__main__":
    main()
