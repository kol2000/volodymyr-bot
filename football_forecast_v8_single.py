#!/usr/bin/env python3
"""Bubus Forecast V8: single-match research interface.

Accepts an already selected Soccer365 Match object.

No Telegram publication.
No database writes.
No production authorization.
"""

import math
import re

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import soccer365_football as soccer
import football_soccer365_v8_mapping_v1 as mapping_module
import football_forecast_data_live_v1 as loader
import football_forecast_research_v8_1 as research
import football_forecast_readiness_v1 as readiness
import football_forecast_live_v7 as engine
import football_totals_research_v1 as totals

from football_forecast_formatter_v1 import render_forecast


MOSCOW = ZoneInfo("Europe/Moscow")


class V8Unavailable(Exception):
    """V8 cannot safely handle this match."""


def forecast_match(match, now=None):
    """Calculate one V8 research forecast.

    Returns a formatted message.

    Raises V8Unavailable when the match is ineligible.

    This interface is for isolated tests only.
    """

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now = now.astimezone(timezone.utc)

    # ---------------------------------------
    # 1. Validate selected Soccer365 match
    # ---------------------------------------

    match_id = str(match.id)

    if not re.fullmatch(r"s365:\d+", match_id):
        raise V8Unavailable(
            "Unsupported match identity"
        )

    if match.status != "scheduled":
        raise V8Unavailable(
            "Match is not scheduled"
        )

    if not isinstance(match.kickoff, (int, float)):
        raise V8Unavailable(
            "Missing kickoff timestamp"
        )

    if (
        not math.isfinite(match.kickoff)
        or match.kickoff <= now.timestamp()
    ):
        raise V8Unavailable(
            "Match has already started"
        )

    day = datetime.fromtimestamp(
        match.kickoff,
        MOSCOW,
    ).date()

    as_of = now.astimezone(MOSCOW).date()

    # ---------------------------------------
    # 2. Identify European competition
    # ---------------------------------------

    league = mapping_module.LEAGUES.get(
        match.competition
    )

    if league is None:
        raise V8Unavailable(
            "Competition is not supported by V8"
        )

    mapping = mapping_module.load_mapping()

    home = mapping[league].get(
        soccer.identity(match.home)
    )

    away = mapping[league].get(
        soccer.identity(match.away)
    )

    if not home or not away:
        raise V8Unavailable(
            "Team mapping unavailable"
        )

    # ---------------------------------------
    # 3. Preliminary league readiness
    # ---------------------------------------

    readiness_report = readiness.evaluate(
        as_of=as_of
    )

    info = readiness_report["leagues"][league]

    if info["status"] != "READY_CANDIDATE":
        raise V8Unavailable(
            f"League blocked: {league}"
        )

    # ---------------------------------------
    # 4. Load validated snapshot
    # ---------------------------------------

    season = loader.load_current_matches()

    if (
        season["snapshot_id"]
        != readiness_report["snapshot_id"]
    ):
        raise V8Unavailable(
            "Snapshot changed during calculation"
        )

    # ---------------------------------------
    # 5. Build current historical context
    # ---------------------------------------

    context = research.build_context(
        as_of=as_of,
        season_data=(
            season["matches"],
            season["counts"],
        ),
    )

    combined = context["combined"]
    bridge = context["bridge"]

    # ---------------------------------------
    # 6. Find exact match in V8 calendar
    # ---------------------------------------

    matching = [
        row
        for fixture_day, fixture_league, row
        in context["upcoming"]
        if (
            fixture_day == day
            and fixture_league == league
            and row["home"] == home
            and row["away"] == away
        )
    ]

    if len(matching) != 1:
        raise V8Unavailable(
            "Match not uniquely identified "
            "in V8 calendar"
        )

    # ---------------------------------------
    # 7. Resolve historical team identities
    # ---------------------------------------

    home_id = bridge[league][home][
        "historical_id"
    ]

    away_id = bridge[league][away][
        "historical_id"
    ]

    # ---------------------------------------
    # 8. Build prematch historical input
    # ---------------------------------------

    past = [
        m for m in combined
        if m.day < as_of
    ]

    if not past:
        raise V8Unavailable(
            "Historical data unavailable"
        )

    ratings = engine.rating_before(
        combined,
        as_of,
    )

    fixture = {
        "league": league,
        "day": day,
        "home_id": home_id,
        "away_id": away_id,
    }

    # ---------------------------------------
    # 9. Calculate predictions
    # ---------------------------------------

    predictions, reason = engine.forecast_at(
        fixture,
        past,
        ratings,
    )

    if predictions is None:
        raise V8Unavailable(
            f"Outcome unavailable: {reason}"
        )

    goals = totals.analyze(
        home_id,
        away_id,
        past,
        league,
        day,
    )

    if goals is None:
        raise V8Unavailable(
            "Totals unavailable"
        )

    probs = tuple(predictions["V3"])

    if (
        len(probs) != 3
        or not all(
            math.isfinite(p) and 0 <= p <= 1
            for p in probs
        )
        or abs(sum(probs) - 1) > 0.001
    ):
        raise V8Unavailable(
            "Invalid probabilities"
        )

    # ---------------------------------------
    # 10. Render existing forecast format
    # ---------------------------------------

    message = render_forecast(
        league=league,
        match_date=day,
        home=match.home,
        away=match.away,
        probabilities=probs,
        goals=goals,
        verified=False,
    )

    return message


