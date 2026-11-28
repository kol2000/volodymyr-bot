#!/usr/bin/env python3
"""Bubus Totals Research V1.

Research implementation of the existing V2
Poisson / Dixon-Coles score distribution.

READ ONLY / OFFLINE.
No database writes.
No Telegram messages.
No live forecast recording.
"""

import math
from collections import defaultdict
from datetime import date

import football_forecast_live_v7 as engine
from football_compare_v2 import candidate_prediction


START = date(2026, 7, 1)
END = date(2027, 7, 1)

LEAGUES = (
    "EPL",
    "LaLiga",
    "SerieA",
    "Bundesliga",
    "Ligue1",
)

THRESHOLDS = (0.5, 1.5, 2.5, 3.5, 4.5)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def score_distribution(home, away, prior, league, when):
    """Reproduce V2 rates and Dixon-Coles score grid."""

    rho = -0.08
    half_life = 120.0

    past = [
        m for m in prior
        if m.league == league
        and m.day < when
        and (when - m.day).days <= 540
    ]

    if len(past) < 90:
        return None

    def weight(m):
        return 2 ** (
            -(when - m.day).days / half_life
        )

    weights = [weight(m) for m in past]

    total = sum(weights)

    if total <= 0:
        return None

    league_home = max(
        0.2,
        sum(
            w * m.goals[0]
            for m, w in zip(past, weights)
        ) / total,
    )

    league_away = max(
        0.2,
        sum(
            w * m.goals[1]
            for m, w in zip(past, weights)
        ) / total,
    )

    def rate(team, venue):

        subset = [
            (m, w)
            for m, w in zip(past, weights)
            if (
                m.home if venue == "home"
                else m.away
            ) == team
        ]

        broad = [
            (m, w)
            for m, w in zip(past, weights)
            if team in (m.home, m.away)
        ]

        if len(broad) < 6 or len(subset) < 3:
            return None

        def gf_ga(m):
            if m.home == team:
                return m.goals
            return m.goals[::-1]

        broad_weight = sum(
            w for _, w in broad
        )

        broad_for = sum(
            gf_ga(m)[0] * w
            for m, w in broad
        ) / broad_weight

        broad_against = sum(
            gf_ga(m)[1] * w
            for m, w in broad
        ) / broad_weight

        is_home = venue == "home"

        default_for = (
            league_home if is_home
            else league_away
        )

        default_against = (
            league_away if is_home
            else league_home
        )

        prior_for = (
            0.5 * broad_for
            + 0.5 * default_for
        )

        prior_against = (
            0.5 * broad_against
            + 0.5 * default_against
        )

        denom = (
            sum(w for _, w in subset)
            + 5.0
        )

        return (
            (
                sum(
                    gf_ga(m)[0] * w
                    for m, w in subset
                )
                + 5 * prior_for
            ) / denom,
            (
                sum(
                    gf_ga(m)[1] * w
                    for m, w in subset
                )
                + 5 * prior_against
            ) / denom,
        )

    h = rate(home, "home")
    a = rate(away, "away")

    if h is None or a is None:
        return None

    lh = max(
        0.15,
        min(
            4.5,
            h[0] * a[1] / league_home,
        ),
    )

    la = max(
        0.15,
        min(
            4.5,
            a[0] * h[1] / league_away,
        ),
    )

    ph = [
        math.exp(-lh)
        * lh**i
        / math.factorial(i)
        for i in range(13)
    ]

    pa = [
        math.exp(-la)
        * la**i
        / math.factorial(i)
        for i in range(13)
    ]

    grid = {}

    for i in range(13):
        for j in range(13):

            p = ph[i] * pa[j]

            if i == 0 and j == 0:
                p *= 1 - lh * la * rho
            elif i == 0 and j == 1:
                p *= 1 + lh * rho
            elif i == 1 and j == 0:
                p *= 1 + la * rho
            elif i == 1 and j == 1:
                p *= 1 - rho

            require(
                p >= 0,
                "Negative Dixon-Coles probability",
            )

            grid[(i, j)] = p

    mass = sum(grid.values())

    require(
        mass > 0,
        "Invalid score distribution",
    )

    grid = {
        score: p / mass
        for score, p in grid.items()
    }

    return lh, la, grid


def analyze(home, away, prior, league, when):

    result = score_distribution(
        home,
        away,
        prior,
        league,
        when,
    )

    if result is None:
        return None

    lh, la, grid = result

    outcomes = (
        sum(p for (i, j), p in grid.items() if i > j),
        sum(p for (i, j), p in grid.items() if i == j),
        sum(p for (i, j), p in grid.items() if i < j),
    )

    original = candidate_prediction(
        home,
        away,
        prior,
        league,
        when,
    )

    require(
        original is not None,
        "Original V2 returned no prediction",
    )

    max_diff = max(
        abs(x - y)
        for x, y in zip(outcomes, original)
    )

    require(
        max_diff < 1e-10,
        f"V2 compatibility error: {max_diff}",
    )

    over = {
        threshold: sum(
            p
            for (i, j), p in grid.items()
            if i + j > threshold
        )
        for threshold in THRESHOLDS
    }

    btts = sum(
        p
        for (i, j), p in grid.items()
        if i > 0 and j > 0
    )

    most_likely = max(
        grid,
        key=grid.get,
    )

    expected_home = sum(
        i * p
        for (i, j), p in grid.items()
    )

    expected_away = sum(
        j * p
        for (i, j), p in grid.items()
    )

    return {
        "lambda_home": lh,
        "lambda_away": la,
        "expected_home": expected_home,
        "expected_away": expected_away,
        "outcomes_v2": outcomes,
        "over": over,
        "btts": btts,
        "most_likely_score": most_likely,
    }


def binary_metrics(rows):

    if not rows:
        return None

    n = len(rows)

    mean_predicted = sum(
        p for p, y in rows
    ) / n

    mean_actual = sum(
        y for p, y in rows
    ) / n

    brier = sum(
        (p - y) ** 2
        for p, y in rows
    ) / n

    logloss = -sum(
        y * math.log(max(p, 1e-15))
        + (1 - y) * math.log(max(1 - p, 1e-15))
        for p, y in rows
    ) / n

    return n, mean_predicted, mean_actual, brier, logloss


def main():

    print("=== BUBUS TOTALS RESEARCH V1 ===")
    print("MODE: READ ONLY / OFFLINE")

    matches, _, _, _ = engine.load_matches(
        engine.HISTORY,
        engine.SOURCE,
        engine.MAPPING,
    )

    targets = [
        m for m in matches
        if START <= m.day < END
        and m.league in LEAGUES
    ]

    print("\nTarget matches:", len(targets))

    rows = defaultdict(list)
    evaluated = defaultdict(int)
    skipped = defaultdict(int)

    cache = {}

    for match in targets:

        day = match.day

        if day not in cache:
            cache[day] = [
                m for m in matches
                if m.day < day
            ]

        past = cache[day]

        require(
            all(m.day < day for m in past),
            "Temporal cutoff violation",
        )

        result = analyze(
            match.home,
            match.away,
            past,
            match.league,
            day,
        )

        if result is None:
            skipped[match.league] += 1
            continue

        evaluated[match.league] += 1

        goals = sum(match.goals)

        for threshold in THRESHOLDS:

            rows[
                (match.league, f"OVER_{threshold}")
            ].append((
                result["over"][threshold],
                int(goals > threshold),
            ))

        rows[
            (match.league, "BTTS")
        ].append((
            result["btts"],
            int(
                match.goals[0] > 0
                and match.goals[1] > 0
            ),
        ))

    print("\n=== V2 COMPATIBILITY ===")

    print(
        "All evaluated score distributions "
        "match original V2 outcome probabilities."
    )

    print("\n=== TOTALS BACKTEST ===")

    for league in (*LEAGUES, "ALL"):

        print()
        print("LEAGUE:", league)

        for label in (
            "OVER_1.5",
            "OVER_2.5",
            "OVER_3.5",
            "BTTS",
        ):

            if league == "ALL":
                data = [
                    item
                    for lg in LEAGUES
                    for item in rows[(lg, label)]
                ]
            else:
                data = rows[(league, label)]

            result = binary_metrics(data)

            if result is None:
                print(label, "NO RESULTS")
                continue

            n, predicted, actual, brier, loss = result

            print(
                f"{label:10s}"
                f" n={n:3d}"
                f" predicted={predicted:6.2%}"
                f" actual={actual:6.2%}"
                f" Brier={brier:.4f}"
                f" LogLoss={loss:.4f}"
            )

    print("\n=== COVERAGE ===")

    for league in LEAGUES:
        print(
            league,
            "| evaluated:",
            evaluated[league],
            "| skipped:",
            skipped[league],
        )

    print(
        "TOTAL:",
        sum(evaluated.values()),
        "evaluated,",
        sum(skipped.values()),
        "skipped",
    )

    print("\n=== FINAL STATUS ===")
    print("TOTALS RESEARCH COMPLETED")
    print("ORIGINAL V2 OUTCOMES: VERIFIED")
    print("HISTORICAL AVAILABILITY: UNVERIFIED")
    print("SAME-DAY RESULTS: EXCLUDED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
