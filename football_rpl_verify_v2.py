#!/usr/bin/env python3
"""Bubus RPL Verification V2.

Verify completed RPL matches against:
- RPLstat results
- Sport-Express calendar
- Sport-Express match center
- Stored result snapshot

Default: read-only.
--write: save per-match verification evidence.

Does not modify forecasts or original results.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import football_rpl_source_v1 as rpl
import football_rpl_se_calendar_v1 as se
import football_rpl_compare_sources_v1 as compare
import football_rpl_matchcenter_audit_v1 as mc


ROOT = Path(__file__).resolve().parent

BASE = ROOT / "state/football_expansion/rpl_live"

VERIFIED = BASE / "verified"

ROUND = 15
SEASON = "2026/2027"

FINISHED = ("Завершен", "Завершён")

SCORE_RE = re.compile(r"^\d{1,2}:\d{1,2}$")

MONTH_SHORT = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "мая",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}

# The match-center heading must identify the expected
# team, rather than a different Dynamo or other club.
HEADERS = {
    "FK Rostov": ("Ростов", "Ростов-на-Дону"),
    "Krylya Sovetov": ("Крылья Советов", "Самара"),
    "Orenburg": ("Оренбург", "Оренбург"),
    "Baltika": ("Балтика", "Калининград"),
    "Akron Togliatti": ("Акрон", "Тольятти"),
    "Fakel Voronezh": ("Факел", "Воронеж"),
    "Rubin Kazan": ("Рубин", "Казань"),
    "Zenit": ("Зенит", "Санкт-Петербург"),
    "CSKA Moscow": ("ЦСКА", "Москва"),
    "Akhmat Grozny": ("Ахмат", "Грозный"),
    "Dynamo Moscow": ("Динамо", "Москва"),
    "Dynamo Makhachkala": ("Динамо Мх", "Махачкала"),
    "Krasnodar": ("Краснодар", "Краснодар"),
    "Spartak Moscow": ("Спартак", "Москва"),
    "Rodina Moscow": ("Родина", "Москва"),
    "Lokomotiv Moscow": ("Локомотив", "Москва"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def match_key(day, home, away):
    return (day, home, away)


def date_from_se(value):
    return compare.calendar_date(value)


def time_from_se(value):
    return value.split()[-1]


def parse_matchcenter(raw, expected):

    parser = mc.TextParser()

    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    items = parser.items

    day = datetime.fromisoformat(expected["date"])

    short_month = MONTH_SHORT[day.month]

    expected_round = (
        f"{ROUND}-й тур. "
        f"{day.day} {short_month}, "
        f"{expected['time']}"
    )

    home_name, home_city = HEADERS[expected["home"]]
    away_name, away_city = HEADERS[expected["away"]]

    headings = [
        i for i, value in enumerate(items)
        if expected_round in value
    ]

    if not headings:
        raise RuntimeError(
            f"Match heading not found: {expected_round}"
        )

    candidates = []

    for start in headings:

        # Main match header is expected near the
        # competition/date heading.
        section = items[start:start + 22]

        positions = [
            i for i, value in enumerate(section)
            if value in FINISHED
        ]

        for pos in positions:

            if pos + 1 >= len(section):
                continue

            score = section[pos + 1]

            if not SCORE_RE.fullmatch(score):
                continue

            before = section[:pos]
            after = section[pos + 2:]

            if home_name not in before:
                continue

            if away_name not in after[:8]:
                continue

            if home_city not in before:
                continue

            if away_city not in after[:8]:
                continue

            if "1-й тайм" not in after[:5]:
                continue

            candidates.append({
                "score": score,
                "header_position": start,
                "status_position": start + pos,
            })

    # The same primary block may be detected through
    # overlapping headings. Deduplicate by status position.
    unique = {
        candidate["status_position"]: candidate
        for candidate in candidates
    }

    if len(unique) != 1:
        raise RuntimeError(
            "Cannot uniquely identify primary score: "
            f"{expected['home']} — {expected['away']}; "
            f"headings={len(headings)}, "
            f"valid_candidates={len(unique)}"
        )

    return next(iter(unique.values()))["score"]


def get_primary():

    matches = [
        m for m in rpl.parse_matches(rpl.fetch())
        if m["gw"] == ROUND
    ]

    if len(matches) != 8:
        raise RuntimeError(
            f"RPLstat: expected 8 matches, got {len(matches)}"
        )

    result = {}

    for m in matches:

        key = match_key(
            m["kickoff_msk"].date().isoformat(),
            m["home"],
            m["away"],
        )

        if key in result:
            raise RuntimeError(
                f"Duplicate primary match: {key}"
            )

        score = None

        if m["gh"] is not None:

            if m["ga"] is None:
                raise RuntimeError("Incomplete primary score")

            score = f'{m["gh"]}:{m["ga"]}'

        result[key] = score

    return result


def get_secondary():

    raw = se.fetch()

    parser = se.Parser()

    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    tokens = se.extract_round(
        parser.tokens,
        ROUND
    )

    matches = se.parse_matches(tokens)

    if len(matches) != 8:
        raise RuntimeError(
            f"Sport-Express: expected 8, got {len(matches)}"
        )

    result = {}

    for m in matches:

        day = date_from_se(m["date"])

        key = match_key(
            day,
            m["home"],
            m["away"],
        )

        if key in result:
            raise RuntimeError(
                f"Duplicate secondary match: {key}"
            )

        result[key] = {
            **m,
            "iso_date": day,
            "time": time_from_se(m["date"]),
        }

    return result


def get_snapshot():

    _, _, path, rows = compare.latest_snapshot()

    result = {}

    for row in rows:

        if int(row["gw"]) != ROUND:
            continue

        key = match_key(
            row["date"],
            row["home"],
            row["away"],
        )

        if key in result:
            raise RuntimeError(
                f"Duplicate stored result: {key}"
            )

        result[key] = (
            f'{row["ft_home"]}:{row["ft_away"]}'
        )

    return path, result


def verify_all():

    print("=== BUBUS RPL VERIFICATION V2 ===")

    now = datetime.now(timezone.utc)

    primary = get_primary()
    secondary = get_secondary()

    snapshot_path, stored = get_snapshot()

    if set(primary) != set(secondary):
        raise RuntimeError(
            "Primary/secondary fixture lists differ"
        )

    if not set(stored).issubset(primary):
        raise RuntimeError(
            "Stored snapshot has unknown matches"
        )

    print("Snapshot:", snapshot_path.name)
    print("Matches in round:", len(primary))

    verified = []
    pending = 0
    conflicts = []

    for key in sorted(primary):

        day, home, away = key

        a = primary[key]
        b = secondary[key]

        c = stored.get(key)

        title = f"{day} {home} — {away}"

        if a is None and b["score"] is None and c is None:

            print("PENDING:", title)
            pending += 1
            continue

        if not (
            a is not None
            and a == b["score"] == c
            and b["status"] in FINISHED
            and b["url"]
        ):

            print(
                "CONFLICT:",
                title,
                "RPL=", a,
                "SE=", b["score"],
                "CSV=", c,
            )

            conflicts.append(title)
            continue

        try:

            raw = mc.fetch(b["url"])

            center_score = parse_matchcenter(
                raw,
                {
                    "date": day,
                    "time": b["time"],
                    "home": home,
                    "away": away,
                }
            )

            if center_score != a:
                raise RuntimeError(
                    f"Match-center score mismatch: {center_score}"
                )

            verified.append({
                "schema": "bubus-rpl-verification-v2",
                "competition": "RPL",
                "season": SEASON,
                "round": ROUND,
                "date": day,
                "home": home,
                "away": away,
                "score": a,
                "primary_source": rpl.URL,
                "secondary_source": se.URL,
                "matchcenter_url": b["url"],
                "matchcenter_sha256": sha(raw),
                "primary_snapshot_sha256": snapshot_path.stem,
                "checked_utc": now.isoformat(),
                "status": "verified",
            })

            print("VERIFIED:", title, a)

        except Exception as exc:

            print(
                "FAILED:",
                title,
                type(exc).__name__,
                str(exc)
            )

            conflicts.append(title)

    print("\n=== SUMMARY ===")

    print("Verified:", len(verified))
    print("Pending:", pending)
    print("Conflicts:", len(conflicts))

    if conflicts:
        raise RuntimeError(
            "Verification conflicts detected; "
            "no evidence will be written"
        )

    if len(verified) + pending != 8:
        raise RuntimeError(
            "Unexpected verification totals"
        )

    return verified, pending


def save_verified(records):

    VERIFIED.mkdir(
        parents=True,
        exist_ok=True
    )

    created = 0
    existing = 0

    for record in records:

        filename = (
            f"{record['date']}_"
            f"{record['home'].replace(' ', '_')}_"
            f"{record['away'].replace(' ', '_')}"
            ".v2.json"
        )

        destination = VERIFIED / filename

        if destination.exists():

            old = json.loads(
                destination.read_text(
                    encoding="utf-8"
                )
            )

            fields = (
                "date",
                "home",
                "away",
                "score",
                "matchcenter_url",
            )

            if any(
                old.get(field) != record[field]
                for field in fields
            ):
                raise RuntimeError(
                    f"Existing evidence conflict: {destination}"
                )

            existing += 1
            continue

        with destination.open(
            "x",
            encoding="utf-8"
        ) as f:

            json.dump(
                record,
                f,
                ensure_ascii=False,
                indent=2,
            )

        created += 1

    print("New evidence:", created)
    print("Already verified:", existing)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Save verified evidence records"
    )

    args = parser.parse_args()

    records, pending = verify_all()

    if args.write:

        save_verified(records)

        print("\nVERIFICATION EVIDENCE SAVED")

    else:

        print("\nREAD-ONLY PREVIEW")
        print("No files modified")

    print("Pending matches:", pending)

    print("Forecast journal: untouched")
    print("UCL journal: untouched")
    print("Telegram: untouched")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            "VERIFICATION FAILED:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr
        )

        sys.exit(2)
