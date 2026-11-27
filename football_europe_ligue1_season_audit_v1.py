#!/usr/bin/env python3
"""Bubus Ligue 1 Season Audit V1.

Audit Open Matchday Ligue 1 2026/27.

Sources:
- fixtures_2627.csv
- results_2627.csv

Checks:
- exactly 306 matches
- 18 teams
- 34 matchdays
- 9 games per matchday
- 17 home + 17 away per team
- complete directed round robin
- no duplicates
- valid dates and times
- valid finished match scores

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import csv
import io
import re
import unicodedata

from collections import Counter
from datetime import datetime

import football_europe_ligue1_sources_v1 as sources


EXPECTED_MATCHES = 306
EXPECTED_TEAMS = 18
EXPECTED_ROUNDS = 34


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def normalize_name(value):
    """Normalize spelling without merging distinct clubs."""

    value = unicodedata.normalize(
        "NFKC",
        value,
    )

    value = value.casefold()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = value.strip()

    aliases = {
        "stade rennais": "stade rennais fc",
        "losc lille": "losc",
        "havre athletic club": "le havre",
    }

    return aliases.get(value, value)


def fetch_csv(source_name):

    url = sources.SOURCES[source_name]

    status, content_type, raw = (
        sources.download(url)
    )

    require(
        status == 200,
        source_name + ": HTTP error",
    )

    text = raw.decode("utf-8-sig")

    reader = csv.DictReader(
        io.StringIO(text)
    )

    require(
        reader.fieldnames is not None,
        source_name + ": missing header",
    )

    rows = list(reader)

    require(
        rows,
        source_name + ": empty CSV",
    )

    return rows, raw


def parse_match(row, finished):

    required = (
        "gw",
        "date",
        "time",
        "home",
        "away",
    )

    for field in required:

        require(
            field in row,
            "Missing field: " + field,
        )

    gameweek = int(row["gw"])

    require(
        1 <= gameweek <= 34,
        "Invalid gameweek",
    )

    date = row["date"].strip()
    time = row["time"].strip()

    require(
        bool(date) and bool(time),
        "Missing match date or time",
    )

    datetime.strptime(
        date + " " + time,
        "%Y-%m-%d %H:%M",
    )

    home = row["home"].strip()
    away = row["away"].strip()

    require(
        home and away,
        "Missing team name",
    )

    home_key = normalize_name(home)
    away_key = normalize_name(away)

    require(
        home_key != away_key,
        "Team plays itself",
    )

    match = {
        "gameweek": gameweek,
        "date": date,
        "time": time,
        "home": home,
        "away": away,
        "home_key": home_key,
        "away_key": away_key,
        "finished": finished,
    }

    if finished:

        require(
            "ft_home" in row
            and "ft_away" in row,
            "Missing final result fields",
        )

        home_score = int(row["ft_home"])
        away_score = int(row["ft_away"])

        require(
            home_score >= 0
            and away_score >= 0,
            "Negative final score",
        )

        match["home_score"] = home_score
        match["away_score"] = away_score

        result = row.get(
            "ft_result",
            "",
        ).strip().upper()

        require(
            result in {"H", "D", "A"},
            "Invalid final result label",
        )

        expected_result = (
            "H" if home_score > away_score
            else "A" if away_score > home_score
            else "D"
        )

        require(
            result == expected_result,
            "Final result label mismatch",
        )

    else:

        match["home_score"] = None
        match["away_score"] = None

    return match


def audit():

    print("=== BUBUS LIGUE 1 SEASON AUDIT V1 ===")
    print("MODE: READ ONLY")

    print("\n=== DOWNLOADING SOURCES ===")

    fixtures_rows, fixtures_raw = fetch_csv(
        "OPEN_MATCHDAY_FIXTURES"
    )

    results_rows, results_raw = fetch_csv(
        "OPEN_MATCHDAY_RESULTS"
    )

    print("Fixtures rows:", len(fixtures_rows))
    print("Results rows:", len(results_rows))

    print(
        "Total rows:",
        len(fixtures_rows) + len(results_rows),
    )

    print("\n=== PARSING MATCHES ===")

    fixtures = [
        parse_match(row, False)
        for row in fixtures_rows
    ]

    results = [
        parse_match(row, True)
        for row in results_rows
    ]

    matches = fixtures + results

    require(
        len(matches) == EXPECTED_MATCHES,
        "Expected 306 matches",
    )

    print("Parsed matches:", len(matches))

    print("\n=== UNIQUE MATCHES ===")

    keys = [
        (
            m["gameweek"],
            m["home_key"],
            m["away_key"],
        )
        for m in matches
    ]

    duplicates = [
        key
        for key, count in Counter(keys).items()
        if count > 1
    ]

    print("Unique round/team keys:", len(set(keys)))
    print("Duplicates:", len(duplicates))

    for item in duplicates[:10]:
        print("DUPLICATE:", item)

    require(
        not duplicates,
        "Duplicate matches detected",
    )

    print("\n=== TEAMS ===")

    teams = {}

    for match in matches:

        for side in ("home", "away"):

            key = match[side + "_key"]
            name = match[side]

            teams.setdefault(key, name)

    print("Teams:", len(teams))

    for key, name in sorted(teams.items()):

        print(name)

    require(
        len(teams) == EXPECTED_TEAMS,
        "Expected 18 teams",
    )

    print("\n=== MATCHDAYS ===")

    rounds = Counter(
        m["gameweek"]
        for m in matches
    )

    print("Matchdays:", len(rounds))

    for gameweek in range(1, 35):

        count = rounds[gameweek]

        print(
            f"Round {gameweek:2d}: {count}"
        )

        require(
            count == 9,
            f"Invalid round {gameweek}",
        )

    print("\n=== HOME / AWAY BALANCE ===")

    home_counts = Counter(
        m["home_key"]
        for m in matches
    )

    away_counts = Counter(
        m["away_key"]
        for m in matches
    )

    for key, name in sorted(teams.items()):

        home = home_counts[key]
        away = away_counts[key]

        print(
            f"{name}: "
            f"home={home}, away={away}, "
            f"total={home + away}"
        )

        require(
            home == 17,
            "Invalid home count: " + name,
        )

        require(
            away == 17,
            "Invalid away count: " + name,
        )

    print("\n=== ROUND ROBIN ===")

    directed_pairs = Counter(
        (
            m["home_key"],
            m["away_key"],
        )
        for m in matches
    )

    require(
        len(directed_pairs) == 306,
        "Directed pair count mismatch",
    )

    require(
        all(
            count == 1
            for count in directed_pairs.values()
        ),
        "Repeated directed pairing",
    )

    for home in teams:

        for away in teams:

            if home == away:
                continue

            require(
                directed_pairs[(home, away)] == 1,
                "Missing directed pairing: "
                + teams[home]
                + " — "
                + teams[away],
            )

    print("Directed pairings: 306")
    print("Home/away symmetry: VALID")

    print("\n=== FIXTURES / RESULTS ===")

    fixture_keys = {
        (
            m["gameweek"],
            m["home_key"],
            m["away_key"],
        )
        for m in fixtures
    }

    result_keys = {
        (
            m["gameweek"],
            m["home_key"],
            m["away_key"],
        )
        for m in results
    }

    overlap = fixture_keys & result_keys

    print("Fixtures:", len(fixture_keys))
    print("Results:", len(result_keys))
    print("Overlap:", len(overlap))

    require(
        not overlap,
        "Fixtures and results overlap",
    )

    print("\n=== RESULT DISTRIBUTION ===")

    finished_by_round = Counter(
        m["gameweek"]
        for m in results
    )

    for gameweek in range(1, 35):

        count = finished_by_round[gameweek]

        if count:
            print(
                f"Round {gameweek}: "
                f"{count} finished"
            )

    print("\n=== UPCOMING SAMPLE ===")

    upcoming = sorted(
        fixtures,
        key=lambda m: (
            m["date"],
            m["time"],
            m["home_key"],
        ),
    )

    for match in upcoming[:10]:

        print(
            match["date"],
            match["time"],
            "| Round",
            match["gameweek"],
            "|",
            match["home"],
            "—",
            match["away"],
        )

    print("\n=== FINAL STATUS ===")

    print("306 MATCHES: VALID")
    print("18 TEAMS: VALID")
    print("34 MATCHDAYS: VALID")
    print("9 GAMES PER ROUND: VALID")
    print("17 HOME + 17 AWAY: VALID")
    print("306 DIRECTED PAIRS: VALID")
    print("FIXTURES / RESULTS: SEPARATED")
    print("RESULT SCORES: VALID")

    print("STRUCTURE: PASSED")
    print("OFFICIAL LFP COMPARISON: PENDING")
    print("NO ARCHIVES CREATED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    audit()
