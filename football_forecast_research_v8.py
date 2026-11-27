#!/usr/bin/env python3
"""Bubus Football Forecast Research V8.

Combined research:
- V7 historical results
- Europe Adapter V4
- Team Bridge V1
- V3 match outcomes
- V2 goal totals

STRICTLY OFFLINE / READ ONLY.

No SQLite writes.
No live forecast recording.
No Telegram publication.
"""

from collections import Counter
from datetime import date, timedelta

import football_forecast_live_v7 as engine
import football_europe_adapter_v4 as adapter
import football_europe_team_bridge_v1 as bridge_module
import football_europe_ligue1_monitor_v2 as ligue_monitor
import football_totals_research_v1 as totals


AS_OF = date(2026, 11, 23)
HORIZON_DAYS = 14

LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

ACCEPTED_RESULTS = {
    "FINISHED",
    "RESULT_CORROBORATED",
    "RESULT_PROVIDER_ONLY",
    "RESULT_REPORTED",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_date(value):
    if value is None:
        return None

    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def main():

    print("=== BUBUS FORECAST RESEARCH V8 ===")
    print("MODE: STRICTLY READ ONLY / OFFLINE")
    print("RESEARCH AS OF:", AS_OF)

    # ---------------------------------------
    # 1. Original V7 history
    # ---------------------------------------

    history, aliases, stats, skipped = (
        engine.load_matches(
            engine.HISTORY,
            engine.SOURCE,
            engine.MAPPING,
        )
    )

    require(
        all(
            history[i].day <= history[i + 1].day
            for i in range(len(history) - 1)
        ),
        "Original history not sorted",
    )

    print("\n=== ORIGINAL HISTORY ===")
    print("Loaded matches:", len(history))

    # ---------------------------------------
    # 2. Team Bridge
    # ---------------------------------------

    bridge = bridge_module.load_bridge()

    def team_id(league, name):

        require(
            league in bridge,
            f"League missing from bridge: {league}",
        )

        require(
            name in bridge[league],
            f"Team missing from bridge: {league}/{name}",
        )

        return bridge[league][name]["historical_id"]

    # ---------------------------------------
    # 3. Ligue1 provider dates
    # ---------------------------------------

    (
        content,
        states,
        provider,
        observation,
    ) = ligue_monitor.load_previous_observation()

    provider_dates = {}

    for row in provider["matches"]:

        match_id = str(row["match_id"])

        require(
            match_id not in provider_dates,
            f"Duplicate Ligue1 provider ID: {match_id}",
        )

        provider_dates[match_id] = {
            "date": parse_date(row.get("date")),
            "time_status": row.get("time_status"),
        }

    require(
        len(provider_dates) == 306,
        "Unexpected Ligue1 provider coverage",
    )

    # ---------------------------------------
    # 4. Adapter V4
    # ---------------------------------------

    fixtures, counts = adapter.load_all()

    require(
        len(fixtures) == 1752,
        "Unexpected European calendar size",
    )

    fixture_ids = set()

    for row in fixtures:

        key = (
            row["competition"],
            str(row["match_id"]),
        )

        require(
            key not in fixture_ids,
            f"Duplicate adapter fixture: {key}",
        )

        fixture_ids.add(key)

    def fixture_date(row):

        if row["competition"] == "Ligue1":

            match_id = str(row["match_id"])

            require(
                match_id in provider_dates,
                f"Missing Ligue1 provider ID: {match_id}",
            )

            return provider_dates[match_id]["date"]

        return parse_date(row.get("date"))

    # ---------------------------------------
    # 5. Existing season results
    # ---------------------------------------

    start = date(2026, 7, 1)
    end = date(2027, 7, 1)

    existing = {}

    for match in history:

        if not start <= match.day < end:
            continue

        key = (
            match.league,
            match.home,
            match.away,
        )

        require(
            key not in existing,
            f"Duplicate original fixture: {key}",
        )

        existing[key] = match

    print("\n=== ORIGINAL SEASON ===")
    print("Existing results:", len(existing))

    # ---------------------------------------
    # 6. Reconcile result scores and dates
    # ---------------------------------------

    additions = []
    conflicts = []
    unresolved = []
    result_states = Counter()

    for row in fixtures:

        league = row["competition"]

        if league not in LEAGUES:
            continue

        status = row.get("state_status")

        if status not in ACCEPTED_RESULTS:
            continue

        result_states[status] += 1

        home = team_id(league, row["home"])
        away = team_id(league, row["away"])

        score = (
            row.get("home_score"),
            row.get("away_score"),
        )

        require(
            all(
                type(x) is int and 0 <= x <= 30
                for x in score
            ),
            f"Invalid score: {row['match_id']}",
        )

        day = fixture_date(row)

        key = (league, home, away)

        old = existing.get(key)

        if old is not None:

            if tuple(old.goals) != score:
                conflicts.append((
                    key,
                    "SCORE",
                    old.goals,
                    score,
                ))

            if day is not None and old.day != day:
                conflicts.append((
                    key,
                    "DATE",
                    old.day,
                    day,
                ))

            continue

        if day is None:

            unresolved.append((
                league,
                row["match_id"],
                "MISSING_DATE",
            ))
            continue

        if not start <= day < end:

            unresolved.append((
                league,
                row["match_id"],
                "DATE_OUTSIDE_SEASON",
            ))
            continue

        if day >= AS_OF:

            unresolved.append((
                league,
                row["match_id"],
                "RESULT_NOT_BEFORE_CUTOFF",
            ))
            continue

        Fixture = type(history[0])

        additions.append(
            Fixture(
                league=league,
                day=day,
                home=home,
                away=away,
                goals=score,
            )
        )

    print("\n=== RESULT RECONCILIATION ===")

    print("Adapter result states:", dict(result_states))
    print("Adapter results:", sum(result_states.values()))
    print("New dated results:", len(additions))
    print("Conflicts:", len(conflicts))
    print("Unresolved:", len(unresolved))

    for item in conflicts[:20]:
        print("CONFLICT:", item)

    for item in unresolved[:20]:
        print("UNRESOLVED:", item)

    require(
        not conflicts,
        "Result reconciliation conflicts detected",
    )

    require(
        not unresolved,
        "Some additional results are not usable",
    )

    # ---------------------------------------
    # 7. Combined history
    # ---------------------------------------

    combined = [
        m for m in history
        if m.day < AS_OF
    ]

    combined.extend(additions)

    combined.sort(
        key=lambda m: (
            m.day,
            m.league,
            m.home,
            m.away,
        )
    )

    unique = set()

    for match in combined:

        key = (
            match.league,
            match.day,
            match.home,
            match.away,
        )

        require(
            key not in unique,
            f"Duplicate combined match: {key}",
        )

        unique.add(key)

        require(
            match.day < AS_OF,
            "Future result in combined history",
        )

    print("\n=== COMBINED HISTORY ===")

    print("Original loaded:", len(history))
    print("New results added:", len(additions))
    print("Combined before cutoff:", len(combined))

    print("\n=== NEW RESULTS BY LEAGUE ===")

    for league in LEAGUES:

        league_new = [
            m for m in additions
            if m.league == league
        ]

        print(
            f"{league:12s}",
            len(league_new),
        )

    # ---------------------------------------
    # 8. Upcoming fixture candidates
    # ---------------------------------------

    upcoming = []
    skipped_future = Counter()

    until = AS_OF + timedelta(days=HORIZON_DAYS)

    for row in fixtures:

        league = row["competition"]

        if league not in LEAGUES:
            continue

        if row.get("state_status") in ACCEPTED_RESULTS:
            continue

        day = fixture_date(row)

        if day is None:
            skipped_future["MISSING_DATE"] += 1
            continue

        if not AS_OF < day <= until:
            continue

        upcoming.append((
            day,
            league,
            row,
        ))

    upcoming.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2]["match_id"],
        )
    )

    print("\n=== UPCOMING CANDIDATES ===")

    print(
        "Date-based candidates:",
        len(upcoming),
    )

    print(
        "Missing fixture dates:",
        skipped_future["MISSING_DATE"],
    )

    print(
        "WARNING: kickoff times are not verified."
    )

    # ---------------------------------------
    # 9. Research predictions
    # ---------------------------------------

    print("\n=== RESEARCH FORECASTS ===")

    print(
        "No predictions are registered or published."
    )

    coverage = Counter()
    shown = 0

    for day, league, row in upcoming:

        home = team_id(league, row["home"])
        away = team_id(league, row["away"])

        # Strict cutoff before the fixture date.
        past = [
            m for m in combined
            if m.day < day
        ]

        require(
            all(m.day < day for m in past),
            "Temporal leakage detected",
        )

        ratings = engine.rating_before(
            combined,
            day,
        )

        fixture = {
            "league": league,
            "day": day,
            "home_id": home,
            "away_id": away,
        }

        predictions, reason = engine.forecast_at(
            fixture,
            past,
            ratings,
        )

        goals = totals.analyze(
            home,
            away,
            past,
            league,
            day,
        )

        if predictions is None:
            coverage["V3_UNAVAILABLE"] += 1
        else:
            coverage["V3_AVAILABLE"] += 1

        if goals is None:
            coverage["TOTALS_UNAVAILABLE"] += 1
        else:
            coverage["TOTALS_AVAILABLE"] += 1

        # Display only the first ten candidates.
        if shown >= 10:
            continue

        shown += 1

        print()
        print(
            f"{day} | {league} | "
            f"{row['home']} — {row['away']}"
        )

        if predictions is None:

            print(
                "P1/X/P2:",
                "UNAVAILABLE",
                reason,
            )

        else:

            p = predictions["V3"]

            print(
                "P1/X/P2:",
                f"{p[0]:.1%}",
                f"{p[1]:.1%}",
                f"{p[2]:.1%}",
            )

        if goals is None:

            print("TOTALS: UNAVAILABLE")

        else:

            expected_total = (
                goals["expected_home"]
                + goals["expected_away"]
            )

            print(
                "Expected goals:",
                f"{goals['expected_home']:.2f}",
                f"{goals['expected_away']:.2f}",
                "| total:",
                f"{expected_total:.2f}",
            )

            for threshold in (1.5, 2.5, 3.5):

                p_over = goals["over"][threshold]

                print(
                    f"  Over {threshold}: "
                    f"{p_over:.1%}"
                    f" | Under {threshold}: "
                    f"{1-p_over:.1%}"
                )

            print(
                "Both teams score:",
                f"{goals['btts']:.1%}",
            )

            score = goals["most_likely_score"]

            print(
                "Most likely score:",
                f"{score[0]}:{score[1]}",
            )

    print("\n=== FORECAST COVERAGE ===")

    print(dict(coverage))

    print("\n=== FINAL STATUS ===")

    print("RESEARCH V8 COMPLETED")
    print("SOURCE OBSERVATION TIMES: UNVERIFIED")
    print("KICKOFF TIMES: UNVERIFIED")
    print("NO DATABASE WRITES")
    print("NO LIVE FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
