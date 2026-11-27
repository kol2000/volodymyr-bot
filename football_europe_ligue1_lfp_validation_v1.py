#!/usr/bin/env python3
"""Bubus Ligue 1 Official LFP Validation V1.

Compare Ligue 1 2026/27 Open Matchday fixtures
against the official LFP PDF calendar.

Checks:
- 34 official matchdays
- 9 fixtures per matchday
- 306 official fixtures
- exact home/away correspondence
- no missing or unexpected matches

READ ONLY.
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

import football_europe_ligue1_sources_v1 as sources
import football_europe_ligue1_season_audit_v1 as season


OFFICIAL_TEAMS = (
    "AJ AUXERRE",
    "ANGERS SCO",
    "AS MONACO",
    "ESTAC TROYES",
    "FC LORIENT",
    "LE HAVRE",
    "LE MANS FC",
    "LOSC",
    "OGC NICE",
    "OLYMPIQUE DE MARSEILLE",
    "OLYMPIQUE LYONNAIS",
    "PARIS FC",
    "PARIS SAINT-GERMAIN",
    "RC LENS",
    "RC STRASBOURG ALSACE",
    "STADE BRESTOIS 29",
    "STADE RENNAIS FC",
    "TOULOUSE FC",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def canonical_team(name):
    return season.normalize_name(name)


def make_team_pattern():

    # Longer names first to avoid partial matching.

    names = sorted(
        OFFICIAL_TEAMS,
        key=len,
        reverse=True,
    )

    alternatives = []

    for name in names:

        parts = name.split()

        expression = r"\s+".join(
            re.escape(part)
            for part in parts
        )

        alternatives.append(expression)

    return "(?:" + "|".join(alternatives) + ")"


TEAM_PATTERN = make_team_pattern()

PAIR_PATTERN = re.compile(
    r"(?<!\w)"
    r"(?P<home>" + TEAM_PATTERN + r")"
    r"\s*/\s*"
    r"(?P<away>" + TEAM_PATTERN + r")"
    r"(?!\w)",
    re.IGNORECASE,
)

ROUND_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?P<round>\d{1,2})"
    r"\s*è(?:re|me)\s+journ[ée]+e?",
    re.IGNORECASE,
)


def download_pdf():

    url = sources.SOURCES[
        "LFP_OFFICIAL_PDF"
    ]

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Bubus-Football-Monitor/1.0"
            ),
            "Accept": "application/pdf",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        require(
            response.status == 200,
            "Official PDF HTTP error",
        )

        raw = response.read(5_000_001)

    require(
        len(raw) <= 5_000_000,
        "PDF exceeds size limit",
    )

    require(
        raw.startswith(b"%PDF-"),
        "Invalid PDF signature",
    )

    executable = shutil.which(
        "pdftotext"
    )

    require(
        executable is not None,
        "pdftotext is unavailable",
    )

    with tempfile.TemporaryDirectory(
        prefix="bubus-lfp-validation-"
    ) as tmp:

        path = Path(tmp) / "calendar.pdf"

        path.write_bytes(raw)

        result = subprocess.run(
            [
                executable,
                "-layout",
                "-nopgbrk",
                "-enc",
                "UTF-8",
                str(path),
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

        return result.stdout, len(raw)


def extract_pairs(line):

    matches = list(
        PAIR_PATTERN.finditer(line)
    )

    result = []

    for match in matches:

        home = canonical_team(
            match.group("home")
        )

        away = canonical_team(
            match.group("away")
        )

        require(
            home != away,
            "Self-match in official PDF",
        )

        result.append((home, away))

    return result


def parse_official_calendar(text):

    lines = [
        line
        for line in text.splitlines()
        if line.strip()
    ]

    fixtures = []
    problems = []
    header_groups = []

    index = 0

    while index < len(lines):

        line = lines[index]

        rounds = [
            int(match.group("round"))
            for match in ROUND_PATTERN.finditer(line)
        ]

        if not rounds:

            index += 1
            continue

        # Official document has two or three
        # matchday columns in each fixture group.

        if not (
            1 <= len(rounds) <= 3
            and len(set(rounds)) == len(rounds)
            and all(1 <= r <= 34 for r in rounds)
        ):

            problems.append({
                "type": "INVALID_HEADER",
                "line": index + 1,
                "text": line[:200],
                "rounds": rounds,
            })

            index += 1
            continue

        header_groups.append(
            tuple(rounds)
        )

        row_count = 0
        cursor = index + 1

        while (
            cursor < len(lines)
            and row_count < 9
        ):

            candidate = lines[cursor]

            # A new matchday header before nine
            # fixture rows means malformed extraction.

            if ROUND_PATTERN.search(candidate):

                problems.append({
                    "type": "EARLY_NEXT_HEADER",
                    "rounds": rounds,
                    "rows_found": row_count,
                    "line": cursor + 1,
                })

                break

            if "/" not in candidate:

                cursor += 1
                continue

            pairs = extract_pairs(
                candidate
            )

            if len(pairs) != len(rounds):

                problems.append({
                    "type": "PAIR_COUNT_MISMATCH",
                    "rounds": rounds,
                    "row": row_count + 1,
                    "expected": len(rounds),
                    "actual": len(pairs),
                    "line": cursor + 1,
                    "text": candidate[:250],
                })

                # Still count this fixture row so
                # diagnostics remain meaningful.

                row_count += 1
                cursor += 1
                continue

            for gameweek, pair in zip(
                rounds,
                pairs,
            ):

                fixtures.append({
                    "gameweek": gameweek,
                    "home_key": pair[0],
                    "away_key": pair[1],
                })

            row_count += 1
            cursor += 1

        if row_count != 9:

            problems.append({
                "type": "INVALID_ROW_COUNT",
                "rounds": rounds,
                "rows_found": row_count,
            })

        index = cursor

    return fixtures, header_groups, problems


def load_provider_calendar():

    fixtures_rows, _ = season.fetch_csv(
        "OPEN_MATCHDAY_FIXTURES"
    )

    results_rows, _ = season.fetch_csv(
        "OPEN_MATCHDAY_RESULTS"
    )

    fixtures = [
        season.parse_match(row, False)
        for row in fixtures_rows
    ]

    results = [
        season.parse_match(row, True)
        for row in results_rows
    ]

    return fixtures + results


def fixture_key(match):

    return (
        match["gameweek"],
        match["home_key"],
        match["away_key"],
    )


def describe(key):

    gameweek, home, away = key

    return (
        f"Round {gameweek}: "
        f"{home} — {away}"
    )


def main():

    print(
        "=== BUBUS LIGUE 1 LFP VALIDATION V1 ==="
    )

    print("MODE: READ ONLY")

    print("\n=== OFFICIAL LFP PDF ===")

    text, size = download_pdf()

    print("PDF bytes:", size)
    print("Extracted characters:", len(text))

    official, groups, problems = (
        parse_official_calendar(text)
    )

    print("\n=== PDF PARSING ===")

    print("Header groups:", len(groups))

    print(
        "Recognized rounds:",
        sum(len(group) for group in groups),
    )

    print("Parsed fixtures:", len(official))
    print("Parser problems:", len(problems))

    print("\n=== HEADER GROUPS ===")

    for group in groups:

        print(
            "Rounds:",
            ", ".join(map(str, group)),
        )

    print("\n=== PARSER PROBLEMS ===")

    for problem in problems[:30]:
        print(problem)

    print("\n=== OFFICIAL MATCHDAYS ===")

    official_rounds = Counter(
        item["gameweek"]
        for item in official
    )

    for round_number in range(1, 35):

        print(
            f"Round {round_number:2d}:",
            official_rounds[round_number],
        )

    print("\n=== OPEN MATCHDAY ===")

    provider = load_provider_calendar()

    print("Provider fixtures:", len(provider))

    official_keys = [
        fixture_key(item)
        for item in official
    ]

    provider_keys = [
        fixture_key(item)
        for item in provider
    ]

    official_set = set(official_keys)
    provider_set = set(provider_keys)

    print("\n=== SOURCE COMPARISON ===")

    print(
        "Unique official fixtures:",
        len(official_set),
    )

    print(
        "Unique provider fixtures:",
        len(provider_set),
    )

    matching = official_set & provider_set

    missing = official_set - provider_set
    unexpected = provider_set - official_set

    print("Matching:", len(matching))

    print(
        "Official not in provider:",
        len(missing),
    )

    print(
        "Provider not in official:",
        len(unexpected),
    )

    for key in sorted(missing)[:20]:

        print(
            "MISSING IN PROVIDER:",
            describe(key),
        )

    for key in sorted(unexpected)[:20]:

        print(
            "NOT MATCHED IN OFFICIAL:",
            describe(key),
        )

    print("\n=== VALIDATION ===")

    require(
        not problems,
        "Official PDF parsing problems detected",
    )

    require(
        len(groups) >= 12,
        "Expected official matchday groups",
    )

    require(
        len(official) == 306,
        "Expected 306 official fixtures",
    )

    require(
        len(official_set) == 306,
        "Duplicate official fixtures",
    )

    require(
        len(provider) == 306,
        "Expected 306 provider fixtures",
    )

    require(
        len(provider_set) == 306,
        "Duplicate provider fixtures",
    )

    require(
        len(official_rounds) == 34,
        "Expected 34 official rounds",
    )

    require(
        all(
            official_rounds[r] == 9
            for r in range(1, 35)
        ),
        "Invalid official matchday sizes",
    )

    require(
        len(matching) == 306,
        "Official/provider calendar mismatch",
    )

    require(
        not missing and not unexpected,
        "Unmatched fixtures detected",
    )

    print("\n=== FINAL STATUS ===")

    print("306 OFFICIAL FIXTURES: VERIFIED")
    print("34 MATCHDAYS: VERIFIED")
    print("HOME/AWAY TEAMS: VERIFIED")
    print("OPEN MATCHDAY CORRESPONDENCE: VERIFIED")

    print("STATUS: VALIDATION PASSED")

    print("READ ONLY — NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
