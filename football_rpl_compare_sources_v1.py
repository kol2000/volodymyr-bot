#!/usr/bin/env python3

import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import football_rpl_se_calendar_v1 as se
import football_rpl_source_v1 as rpl

BASE = Path("state/football_expansion/rpl_live")
SNAPSHOTS = BASE / "snapshots"

MSK = ZoneInfo("Europe/Moscow")

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def calendar_date(text):
    parts = text.split()

    day = int(parts[0])
    month = MONTHS[parts[1]]

    return datetime(
        2026,
        month,
        day
    ).date().isoformat()


def latest_snapshot():

    candidates = []

    for path in SNAPSHOTS.glob("*.csv"):

        raw = path.read_bytes()

        digest = hashlib.sha256(
            raw
        ).hexdigest()

        if digest != path.stem:
            raise RuntimeError(
                f"Snapshot hash mismatch: {path}"
            )

        manifest_path = path.with_suffix(".json")

        if not manifest_path.exists():
            raise RuntimeError(
                f"Missing manifest: {path}"
            )

        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

        if manifest.get("results_sha256") != digest:
            raise RuntimeError(
                "Manifest hash mismatch"
            )

        if manifest.get("status") != "provisional":
            raise RuntimeError(
                "Unexpected snapshot status"
            )

        with path.open(
            encoding="utf-8-sig",
            newline=""
        ) as f:
            rows = list(csv.DictReader(f))

        if len(rows) != manifest["results_count"]:
            raise RuntimeError(
                "Manifest row count mismatch"
            )

        candidates.append((
            len(rows),
            manifest["created_utc"],
            path,
            rows,
        ))

    if not candidates:
        raise RuntimeError("No snapshots")

    return max(candidates)


def main():

    print("=== BUBUS RPL TWO-SOURCE COMPARISON ===")

    # Primary source: RPLstat.
    primary = [
        m for m in rpl.parse_matches(rpl.fetch())
        if m["gw"] == 15
    ]

    # Secondary source: Sport-Express.
    raw = se.fetch()

    parser = se.Parser()
    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    tokens = se.extract_round(
        parser.tokens, 15
    )

    secondary = se.parse_matches(tokens)

    if len(primary) != 8 or len(secondary) != 8:
        raise RuntimeError(
            "Expected 8 matches from each source"
        )

    # Check selected stored snapshot.
    _, _, snapshot_path, snapshot_rows = (
        latest_snapshot()
    )

    stored = {
        (r["date"], r["home"], r["away"]): (
            f'{r["ft_home"]}:{r["ft_away"]}'
        )
        for r in snapshot_rows
        if int(r["gw"]) == 15
    }

    primary_index = {}

    for m in primary:

        key = (
            m["kickoff_msk"].date().isoformat(),
            m["home"],
            m["away"],
        )

        if key in primary_index:
            raise RuntimeError(
                f"Primary duplicate: {key}"
            )

        primary_index[key] = (
            None
            if m["gh"] is None
            else f'{m["gh"]}:{m["ga"]}'
        )

    secondary_index = {}

    for m in secondary:

        key = (
            calendar_date(m["date"]),
            m["home"],
            m["away"],
        )

        if key in secondary_index:
            raise RuntimeError(
                f"Secondary duplicate: {key}"
            )

        secondary_index[key] = m

    if set(primary_index) != set(secondary_index):
        raise RuntimeError(
            "MATCH LIST CONFLICT: "
            f"only primary={set(primary_index)-set(secondary_index)}, "
            f"only secondary={set(secondary_index)-set(primary_index)}"
        )

    print("Snapshot:", snapshot_path.name)
    print("Matches:", len(primary_index))
    print()

    matched = 0
    pending = 0
    conflicts = 0

    for key in sorted(primary_index):

        primary_score = primary_index[key]
        secondary_match = secondary_index[key]

        secondary_score = secondary_match["score"]
        stored_score = stored.get(key)

        if (
            primary_score is not None
            and secondary_score is not None
            and stored_score == primary_score == secondary_score
            and secondary_match["status"] in ("Завершен", "Завершён")
            and secondary_match["url"]
        ):
            status = "MATCH"
            matched += 1

        elif (
            primary_score is None
            and secondary_score is None
            and stored_score is None
        ):
            status = "PENDING"
            pending += 1

        else:
            status = "CONFLICT"
            conflicts += 1

        print(
            f"{key[0]} "
            f"{key[1]} — {key[2]} | "
            f"RPL={primary_score} "
            f"SE={secondary_score} "
            f"CSV={stored_score} "
            f"=> {status}"
        )

        if status == "MATCH":
            print("  URL:", secondary_match["url"])

    print("\n=== SUMMARY ===")
    print("Matched:", matched)
    print("Pending:", pending)
    print("Conflicts:", conflicts)

    if conflicts:
        raise SystemExit(
            "STATUS: SOURCE CONFLICT — NO APPROVAL"
        )

    print("STATUS: SOURCE COMPARISON PASSED")
    print("INDEPENDENT MATCH-CENTER CHECK: STILL REQUIRED")
    print("READ ONLY — NO FILES MODIFIED")


if __name__ == "__main__":
    main()
