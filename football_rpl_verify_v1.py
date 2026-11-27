#!/usr/bin/env python3

import csv
import hashlib
import json
import re
import urllib.request

from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime, timezone


BASE = Path(
    "state/football_expansion/rpl_live"
)

SNAPSHOTS = BASE / "snapshots"

VERIFIED = BASE / "verified"

MATCH = {
    "date": "2026-11-22",
    "home": "Dynamo Moscow",
    "away": "Krasnodar",
    "source_url": (
        "https://www.sport-express.net/"
        "football/n/russia/premier/"
        "match-dinamo-krasnodar-444366/"
    ),
}


class TextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.items = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.items.append(value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def latest_snapshot():

    candidates = []

    for path in SNAPSHOTS.glob("*.csv"):

        data = path.read_bytes()

        if sha(data) != path.stem:
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

        if manifest.get("results_sha256") != path.stem:
            raise RuntimeError(
                "Manifest SHA256 mismatch"
            )

        if manifest.get("status") != "provisional":
            raise RuntimeError(
                "Unexpected snapshot status"
            )

        candidates.append((
            int(manifest["results_count"]),
            manifest["created_utc"],
            path,
        ))

    if not candidates:
        raise RuntimeError("No result snapshots")

    return max(candidates)[2]


def find_match(path):

    with path.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    matches = [
        row for row in rows
        if (
            row["date"] == MATCH["date"]
            and row["home"] == MATCH["home"]
            and row["away"] == MATCH["away"]
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one match, got {len(matches)}"
        )

    row = matches[0]

    return (
        int(row["ft_home"]),
        int(row["ft_away"])
    )


def fetch_independent():

    request = urllib.request.Request(
        MATCH["source_url"],
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache",
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:

        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        data = response.read(2_000_001)

    if len(data) > 2_000_000:
        raise RuntimeError("HTML too large")

    parser = TextParser()

    parser.feed(
        data.decode("utf-8", errors="replace")
    )

    items = parser.items

    # Require a completed-match header with team names
    # on both sides of the result. Avoid scores in lists
    # of previous matches elsewhere on the page.

    candidates = []

    for index, item in enumerate(items):

        if item.strip().casefold() not in (
            "завершен",
            "завершён",
        ):
            continue

        before = items[max(0, index - 8):index]
        after = items[index + 1:index + 9]

        home_found = any(
            x.strip() == "Динамо"
            for x in before
        )

        away_found = any(
            x.strip() == "Краснодар"
            for x in after
        )

        if not (home_found and away_found):
            continue

        scores = []

        for value in after:

            value = value.strip()

            match = re.fullmatch(
                r"(\d{1,2})\s*:\s*(\d{1,2})",
                value
            )

            if match:
                scores.append((
                    int(match.group(1)),
                    int(match.group(2))
                ))

                break

        if scores:
            candidates.append(scores[0])

    if len(candidates) != 1:
        raise RuntimeError(
            "Could not identify a unique final score "
            f"in independent source: {candidates}"
        )

    return candidates[0], sha(data)


def main():

    print("=== BUBUS RPL VERIFICATION V1 ===")

    snapshot = latest_snapshot()

    print("Snapshot:", snapshot.name)

    primary_score = find_match(snapshot)

    print(
        "Primary score:",
        f"{primary_score[0]}:{primary_score[1]}"
    )

    independent_score, page_hash = fetch_independent()

    print(
        "Independent score:",
        f"{independent_score[0]}:{independent_score[1]}"
    )

    if primary_score != independent_score:
        raise RuntimeError(
            "SCORE CONFLICT: independent verification failed"
        )

    print("Score comparison: MATCH")

    record = {
        "schema": "bubus-rpl-verification-v1",
        "competition": "RPL",
        "season": "2026/2027",
        "date": MATCH["date"],
        "home": MATCH["home"],
        "away": MATCH["away"],
        "ft_home": primary_score[0],
        "ft_away": primary_score[1],
        "primary_snapshot_sha256": snapshot.stem,
        "independent_url": MATCH["source_url"],
        "independent_page_sha256": page_hash,
        "verified_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "verified",
    }

    VERIFIED.mkdir(
        parents=True,
        exist_ok=True
    )

    destination = VERIFIED / (
        "2026-11-22_"
        "Dynamo_Moscow_Krasnodar.json"
    )

    if destination.exists():

        existing = json.loads(
            destination.read_text(
                encoding="utf-8"
            )
        )

        identity = (
            "date",
            "home",
            "away",
            "ft_home",
            "ft_away",
            "independent_url",
        )

        if any(
            existing.get(k) != record[k]
            for k in identity
        ):
            raise RuntimeError(
                "Existing verification conflicts "
                "with current result"
            )

        print("Verification already exists")
        print("Existing record preserved")

    else:

        with destination.open(
            "x",
            encoding="utf-8"
        ) as f:

            json.dump(
                record,
                f,
                indent=2,
                ensure_ascii=False
            )

        print("New verification saved")

    print("Evidence:", destination)

    print("\nSTATUS: VERIFIED")
    print("Forecast journal: untouched")
    print("UCL journal: untouched")
    print("Telegram: untouched")


if __name__ == "__main__":
    main()
