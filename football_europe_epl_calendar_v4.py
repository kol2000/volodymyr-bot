#!/usr/bin/env python3
"""Bubus EPL Calendar V4.

Full season structural validation and duplicate resolution.

READ ONLY.
No automatic kickoff acceptance.
No forecast recording.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone

from football_europe_epl_calendar_v3 import extract
from football_europe_sources_v3 import fetch, Parser, SOURCES


SEASON_START = datetime(
    2026, 8, 1, tzinfo=timezone.utc
)


def load_calendar():

    status, content_type, raw = fetch(SOURCES["EPL"])

    if status != 200 or "html" not in content_type.lower():
        raise RuntimeError("Official EPL source unavailable")

    parser = Parser()
    parser.feed(raw.decode("utf-8", errors="replace"))

    text = " ".join(parser.parts)

    timed, skipped = extract(text, SEASON_START)

    records = []

    for item in timed:
        records.append({
            "date": item["date"],
            "home": item["home"],
            "away": item["away"],
            "kickoff_utc": item["kickoff_utc"],
            "status": "TIMED",
        })

    for date, home, away, reason in skipped:
        records.append({
            "date": date.isoformat(),
            "home": home,
            "away": away,
            "kickoff_utc": None,
            "status": "DISCOVERED",
            "reason": reason,
        })

    return records


def normalize(records):

    groups = defaultdict(list)

    for item in records:
        key = (
            item["home"],
            item["away"],
        )

        groups[key].append(item)

    cleaned = []
    conflicts = []
    unresolved = []

    for key, entries in sorted(groups.items()):

        if len(entries) == 1:
            cleaned.append(entries[0])
            continue

        timed = [
            item for item in entries
            if item["status"] == "TIMED"
        ]

        untimed = [
            item for item in entries
            if item["status"] == "DISCOVERED"
        ]

        # Conservative conflict resolution:
        # exactly two records, one timed and one untimed.
        #
        # The timed entry is only a provisional candidate.
        # It is NOT automatically accepted as a verified kickoff.

        if (
            len(entries) == 2
            and len(timed) == 1
            and len(untimed) == 1
        ):

            selected = dict(timed[0])

            selected["status"] = "TIMED_CONFLICT"
            selected["requires_reverification"] = True

            cleaned.append(selected)

            conflicts.append({
                "pair": key,
                "selected": selected,
                "discarded_candidate": untimed[0],
            })

        else:
            unresolved.append({
                "pair": key,
                "entries": entries,
            })

    cleaned.sort(
        key=lambda r: (
            r["date"],
            r["home"],
            r["away"],
        )
    )

    return cleaned, conflicts, unresolved


def validate(records):

    home = Counter()
    away = Counter()
    directed_pairs = Counter()

    for item in records:

        h = item["home"]
        a = item["away"]

        if h == a:
            raise RuntimeError("Team playing itself")

        home[h] += 1
        away[a] += 1

        directed_pairs[(h, a)] += 1

    teams = set(home) | set(away)

    problems = []

    if len(records) != 380:
        problems.append(
            f"Expected 380 matches, got {len(records)}"
        )

    if len(teams) != 20:
        problems.append(
            f"Expected 20 teams, got {len(teams)}"
        )

    for team in sorted(teams):

        if home[team] != 19 or away[team] != 19:
            problems.append(
                f"{team}: "
                f"home={home[team]} away={away[team]}"
            )

    if len(directed_pairs) != 380:
        problems.append(
            "Directed fixture pairs are not unique"
        )

    # Every club must have exactly one home fixture
    # against every other club.
    for h in teams:
        for a in teams:
            if h == a:
                continue

            if directed_pairs[(h, a)] != 1:
                problems.append(
                    f"Invalid pairing: {h} vs {a}"
                )

    return problems


def main():

    print("=== BUBUS EPL CALENDAR V4 ===")
    print("MODE: READ ONLY")

    original = load_calendar()

    print("\nOriginal records:", len(original))

    cleaned, conflicts, unresolved = normalize(original)

    print("\n=== DUPLICATE RESOLUTION ===")

    for conflict in conflicts:

        home, away = conflict["pair"]

        selected = conflict["selected"]
        discarded = conflict["discarded_candidate"]

        print("\nCONFLICT:", home, "—", away)

        print(
            "Untimed candidate:",
            discarded["date"]
        )

        print(
            "Timed candidate:",
            selected["date"],
            selected["kickoff_utc"]
        )

        print("Resolution: PROVISIONAL")
        print("Independent re-verification: REQUIRED")

    for conflict in unresolved:

        print(
            "\nUNRESOLVED:",
            conflict["pair"]
        )

        for item in conflict["entries"]:
            print(
                " ",
                item["date"],
                item["status"],
                item["kickoff_utc"]
            )

    problems = validate(cleaned)

    print("\n=== STRUCTURAL VALIDATION ===")

    print("Original:", len(original))
    print("Normalized:", len(cleaned))

    print("Provisional resolutions:", len(conflicts))
    print("Unresolved conflicts:", len(unresolved))

    stats = Counter(r["status"] for r in cleaned)

    for status, count in sorted(stats.items()):
        print(status + ":", count)

    print("\n=== FINAL STATUS ===")

    if problems or unresolved:

        print("STATUS: STRUCTURE INVALID")

        for problem in problems:
            print("ERROR:", problem)

        print("FORECAST RECORDING: BLOCKED")
        return 2

    print("STATUS: STRUCTURE VALID")

    if conflicts:
        print("KICKOFF CONFLICTS: REQUIRE REVERIFICATION")

    print("KICKOFF ACCEPTANCE: NOT AUTHORIZED")
    print("FORECAST RECORDING: BLOCKED")
    print("READ ONLY")

    return 0


if __name__ == "__main__":

    raise SystemExit(main())
