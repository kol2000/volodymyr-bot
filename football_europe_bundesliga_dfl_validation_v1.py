#!/usr/bin/env python3
"""Bubus Bundesliga DFL Validation V1.

Compare DFL official PDF fixtures against OpenLigaDB.

Validate:
- 306 official fixture numbers
- 34 matchdays
- home and away teams
- fixture correspondence

STRICTLY READ ONLY.
No archive writes.
No forecast recording.
"""

import re
import shutil
import subprocess
import tempfile
import urllib.request

from collections import Counter
from pathlib import Path

import football_europe_bundesliga_calendar_v2 as calendar


PDF_URL = (
    "https://media.dfl.de/sites/2/2026/07/"
    "DE_s73GnueV_Bundesliga_Spielplan_2026_27.pdf"
)

MAX_PDF = 10_000_000


# Explicit aliases for official DFL naming.
ALIASES = {
    "SC Freiburg": "Sport-Club Freiburg",
    "SV 07 Elversberg": "SV Elversberg",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def compact(text):
    """Normalize formatting, retaining team identity."""

    return re.sub(
        r"\s+",
        "",
        text,
    ).casefold()


def get_pdf_text():

    executable = shutil.which("pdftotext")

    require(
        executable is not None,
        "pdftotext not installed",
    )

    request = urllib.request.Request(
        PDF_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/pdf",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        require(
            response.status == 200,
            "DFL HTTP status is not 200",
        )

        raw = response.read(MAX_PDF + 1)

    require(
        len(raw) <= MAX_PDF,
        "DFL PDF too large",
    )

    require(
        raw.startswith(b"%PDF-"),
        "Invalid PDF signature",
    )

    with tempfile.TemporaryDirectory(
        prefix="bubus-dfl-validation-"
    ) as tmp:

        pdf_path = Path(tmp) / "schedule.pdf"

        pdf_path.write_bytes(raw)

        result = subprocess.run(
            [
                executable,
                "-layout",
                "-nopgbrk",
                "-enc",
                "UTF-8",
                str(pdf_path),
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

        return result.stdout, len(raw)


def get_team_names(api_matches):

    teams = {}

    for match in api_matches:

        for key in ("team1", "team2"):

            team = match[key]

            identifier = team["teamId"]
            name = team["teamName"]

            if identifier in teams:
                require(
                    teams[identifier] == name,
                    "Inconsistent API team name",
                )

            teams[identifier] = name

    require(
        len(teams) == 18,
        "Expected 18 API teams",
    )

    return teams


def build_dfl_names(teams):

    names = {}

    for identifier, api_name in teams.items():

        official_name = ALIASES.get(
            api_name,
            api_name,
        )

        normalized = compact(official_name)

        require(
            normalized not in names,
            "Duplicate normalized team name",
        )

        names[normalized] = identifier

    return names


def resolve_pair(text, names):
    """Resolve a fixture's two teams by exact concatenation.

    Avoid splitting on column spaces because PDF text
    extraction sometimes merges adjacent columns.
    """

    value = compact(text)

    candidates = []

    for home_name, home_id in names.items():

        if not value.startswith(home_name):
            continue

        away_name = value[len(home_name):]

        away_id = names.get(away_name)

        if away_id is None:
            continue

        if home_id == away_id:
            continue

        candidates.append((home_id, away_id))

    if len(candidates) == 1:
        return candidates[0]

    return None


def parse_dfl(text, names):

    fixtures = {}

    unresolved = []
    candidate_lines = 0

    # PDF contains a date column, optional kickoff,
    # matchday, sequential fixture number, two teams.
    #
    # Matchday and fixture number are recognized
    # using their structural relationship.

    pattern = re.compile(
        r"^\s*(?P<prefix>.*?)"
        r"\s+(?P<round>\d{1,2})"
        r"\s+(?P<number>\d{1,3})"
        r"\s+(?P<teams>\S.*?)\s*$"
    )

    for line_number, line in enumerate(
        text.splitlines(),
        1,
    ):

        match = pattern.match(line)

        if not match:
            continue

        round_number = int(
            match.group("round")
        )

        fixture_number = int(
            match.group("number")
        )

        if not 1 <= round_number <= 34:
            continue

        first_number = (
            (round_number - 1) * 9 + 1
        )

        last_number = round_number * 9

        if not (
            first_number
            <= fixture_number
            <= last_number
        ):
            continue

        # Exclude accidentally matched non-fixture
        # lines by requiring a date-like prefix.

        prefix = match.group("prefix")

        if not re.search(
            r"\d{2}\.\d{2}\.202[67]",
            prefix,
        ):
            continue

        candidate_lines += 1

        pair = resolve_pair(
            match.group("teams"),
            names,
        )

        if pair is None:

            unresolved.append({
                "line": line_number,
                "round": round_number,
                "number": fixture_number,
                "text": line.strip(),
            })

            continue

        require(
            fixture_number not in fixtures,
            "Duplicate DFL fixture number: "
            + str(fixture_number),
        )

        fixtures[fixture_number] = {
            "round": round_number,
            "home_id": pair[0],
            "away_id": pair[1],
        }

    return fixtures, unresolved, candidate_lines


def api_fixture_set(matches):

    result = set()

    for match in matches:

        key = (
            match["group"]["groupOrderID"],
            match["team1"]["teamId"],
            match["team2"]["teamId"],
        )

        require(
            key not in result,
            "Duplicate API fixture",
        )

        result.add(key)

    return result


def main():

    print("=== BUBUS BUNDESLIGA DFL VALIDATION V1 ===")
    print("MODE: READ ONLY")

    print("\n=== OFFICIAL DFL PDF ===")

    text, pdf_bytes = get_pdf_text()

    print("PDF bytes:", pdf_bytes)
    print("Text characters:", len(text))

    print("\n=== OPENLIGADB ===")

    data = calendar.fetch_season()

    content, states = calendar.normalize(data)

    print("API matches:", len(data))
    print("Normalized:", len(content["matches"]))
    print("States:", len(states))

    teams = get_team_names(data)

    names = build_dfl_names(teams)

    print("\n=== EXTRACTING DFL FIXTURES ===")

    fixtures, unresolved, candidate_lines = (
        parse_dfl(text, names)
    )

    print(
        "Candidate fixture lines:",
        candidate_lines,
    )

    print(
        "Resolved DFL fixtures:",
        len(fixtures),
    )

    print(
        "Unresolved lines:",
        len(unresolved),
    )

    for item in unresolved[:20]:

        print(
            "UNRESOLVED:",
            item,
        )

    print("\n=== OFFICIAL NUMBERING ===")

    actual_numbers = set(fixtures)

    expected_numbers = set(
        range(1, 307)
    )

    missing_numbers = sorted(
        expected_numbers - actual_numbers
    )

    unexpected_numbers = sorted(
        actual_numbers - expected_numbers
    )

    print(
        "Missing fixture numbers:",
        missing_numbers[:30],
    )

    print(
        "Unexpected fixture numbers:",
        unexpected_numbers[:30],
    )

    rounds = Counter(
        item["round"]
        for item in fixtures.values()
    )

    print(
        "DFL matchdays:",
        len(rounds),
    )

    print(
        "Games per matchday:",
        dict(sorted(rounds.items())),
    )

    print("\n=== SOURCE COMPARISON ===")

    official = {
        (
            item["round"],
            item["home_id"],
            item["away_id"],
        )
        for item in fixtures.values()
    }

    provider = api_fixture_set(data)

    matching = official & provider

    missing = official - provider
    unexpected = provider - official

    print("DFL fixtures:", len(official))
    print("OpenLigaDB fixtures:", len(provider))

    print("Matching:", len(matching))

    print(
        "DFL not found in API:",
        len(missing),
    )

    print(
        "API not found in DFL:",
        len(unexpected),
    )

    def describe(key):

        gameweek, home_id, away_id = key

        return (
            f"Round {gameweek}: "
            f"{teams[home_id]} — "
            f"{teams[away_id]}"
        )

    for key in sorted(missing)[:20]:

        print(
            "MISSING IN API:",
            describe(key),
        )

    for key in sorted(unexpected)[:20]:

        print(
            "NOT MATCHED IN DFL:",
            describe(key),
        )

    print("\n=== VALIDATION ===")

    require(
        candidate_lines == 306,
        "Expected 306 candidate fixture lines",
    )

    require(
        not unresolved,
        "Some DFL fixture lines were not parsed",
    )

    require(
        not missing_numbers,
        "Missing DFL fixture numbers",
    )

    require(
        not unexpected_numbers,
        "Unexpected DFL fixture numbers",
    )

    require(
        len(fixtures) == 306,
        "Expected 306 DFL fixtures",
    )

    require(
        len(rounds) == 34,
        "Expected 34 DFL matchdays",
    )

    require(
        all(rounds[r] == 9 for r in range(1, 35)),
        "Invalid DFL matches per round",
    )

    require(
        len(matching) == 306,
        "Official/API fixture mismatch",
    )

    require(
        not missing and not unexpected,
        "Source comparison mismatch",
    )

    print("\n=== FINAL STATUS ===")

    print("306 OFFICIAL FIXTURES: VERIFIED")
    print("34 MATCHDAYS: VERIFIED")
    print("HOME/AWAY TEAMS: VERIFIED")
    print("OPENLIGADB CORRESPONDENCE: VERIFIED")

    print("STATUS: VALIDATION PASSED")
    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
