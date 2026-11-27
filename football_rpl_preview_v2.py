#!/usr/bin/env python3

import csv
import json
import math
import hashlib
import sqlite3
import collections

from pathlib import Path
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

import football_mega_backtest_v12 as m

from football_backtest import Fixture
from football_compare_v2 import candidate_prediction


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "state/football_expansion"

ARCHIVE = BASE / "mega_v11/RPL_archive.csv"
CURRENT = BASE / "rpl_live/RPL_results_v2.csv"
FIXTURES = BASE / "rpl_live/RPL_fixtures_v2.csv"

DB = BASE / "rpl_live/forecasts_v1.sqlite3"

MSK = ZoneInfo("Europe/Moscow")

MODEL_FILES = [
    ROOT / "football_mega_backtest_v12.py",
    ROOT / "football_compare_v2.py",
    ROOT / "football_backtest.py",
    ROOT / "football_predictions.py",
    ROOT / "football_rpl_preview_v2.py",
]

INPUT_FILES = [ARCHIVE, CURRENT, FIXTURES] + MODEL_FILES


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def input_fingerprint():
    files = {}

    for path in INPUT_FILES:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[str(path.relative_to(ROOT))] = digest

    manifest = json.dumps(
        files,
        sort_keys=True,
        separators=(",", ":")
    ).encode()

    return hashlib.sha256(manifest).hexdigest(), files


def load_games():

    games = []
    seen = set()

    def add(day, home, away, gh, ga, source):

        key = (day, home, away)

        if key in seen:
            raise RuntimeError(f"Duplicate match: {key}")

        seen.add(key)

        games.append(
            m.Match(
                day, "RPL", home, away,
                int(gh), int(ga), source
            )
        )

    for row in read_csv(ARCHIVE):

        if row["Season"] in ("2026/2027", "2026-27"):
            continue

        day = datetime.strptime(
            row["Date"], "%d/%m/%Y"
        ).date()

        add(
            day,
            row["Home"],
            row["Away"],
            row["HG"],
            row["AG"],
            "football-data"
        )

    current = read_csv(CURRENT)

    for row in current:

        add(
            date.fromisoformat(row["date"]),
            row["home"],
            row["away"],
            row["ft_home"],
            row["ft_away"],
            row["source"]
        )

    games.sort(key=lambda x: (x.day, x.home, x.away))

    if len(games) != 3400 + len(current):
        raise RuntimeError("Unexpected historical match count")

    return games, current


def calculate_ratings(games):

    ratings = collections.defaultdict(lambda: 1500.0)
    by_day = collections.defaultdict(list)

    for game in games:
        by_day[game.day].append(game)

    for day in sorted(by_day):

        changes = collections.defaultdict(float)

        for game in by_day[day]:

            rh = ratings[game.home]
            ra = ratings[game.away]

            points = (1.0, 0.5, 0.0)[game.actual]

            delta = 20 * (
                points - m.elo_expected(rh, ra)
            )

            changes[game.home] += delta
            changes[game.away] -= delta

        for team, change in changes.items():
            ratings[team] += change

    return ratings


def calculate_predictions(games, fixtures):

    ratings = calculate_ratings(games)

    prior = [
        Fixture(
            game.day,
            "RPL",
            game.home,
            game.away,
            (game.gh, game.ga)
        )
        for game in games
    ]

    output = []

    for fixture in fixtures:

        home = fixture["home"]
        away = fixture["away"]

        kickoff = datetime.fromisoformat(
            fixture["kickoff_utc"]
        )

        if kickoff.tzinfo is None:
            raise ValueError("Kickoff must have timezone")

        day = kickoff.astimezone(MSK).date()

        if day.isoformat() != fixture["date"]:
            raise ValueError("Fixture date/time mismatch")

        if not games[-1].day < day:
            raise ValueError(
                "Historical results overlap forecast date"
            )

        rh = ratings[home]
        ra = ratings[away]

        p_elo = m.elo_probs(rh, ra)

        dummy = m.Match(
            day, "RPL", home, away,
            0, 0, "forecast-only"
        )

        p_poisson = m.poisson1(games, dummy)

        p_v2 = candidate_prediction(
            home, away, prior, "RPL", day
        )

        p_v3 = None

        if p_v2 is not None:

            decisive = 1 - p_v2[1]
            expected = m.elo_expected(rh, ra)

            elo_component = (
                decisive * expected,
                p_v2[1],
                decisive * (1 - expected)
            )

            p_v3 = tuple(
                .75 * p_v2[i] + .25 * elo_component[i]
                for i in range(3)
            )

        models = {
            "Elo": p_elo,
            "Poisson12": p_poisson,
            "V2": p_v2,
            "V3": p_v3,
        }

        for model, probabilities in models.items():

            if probabilities is None:
                raise RuntimeError(
                    f"No prediction: {model} {home} — {away}"
                )

            if not all(
                math.isfinite(p) and 0 <= p <= 1
                for p in probabilities
            ):
                raise ValueError("Invalid probability")

            if not math.isclose(
                sum(probabilities),
                1.0,
                abs_tol=1e-6
            ):
                raise ValueError(
                    "Probabilities do not sum to 1"
                )

            output.append({
                "competition": "RPL",
                "season": "2026/2027",
                "round": int(fixture["gw"]),
                "home": home,
                "away": away,
                "kickoff_utc": kickoff.astimezone(
                    timezone.utc
                ).isoformat(),
                "model": model,
                "model_version": "RPL-preview-v2",
                "p_home": probabilities[0],
                "p_draw": probabilities[1],
                "p_away": probabilities[2],
            })

    return output


SCHEMA = """
CREATE TABLE IF NOT EXISTS preview_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    competition TEXT NOT NULL,
    season TEXT NOT NULL,
    round INTEGER NOT NULL,

    home TEXT NOT NULL,
    away TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,

    model TEXT NOT NULL,
    model_version TEXT NOT NULL,

    p_home REAL NOT NULL CHECK(p_home BETWEEN 0 AND 1),
    p_draw REAL NOT NULL CHECK(p_draw BETWEEN 0 AND 1),
    p_away REAL NOT NULL CHECK(p_away BETWEEN 0 AND 1),

    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
    calculated_utc TEXT NOT NULL,

    CHECK(ABS(p_home+p_draw+p_away-1.0)<0.000001),
    CHECK(home <> away),

    UNIQUE(
        competition, season, round,
        home, away, kickoff_utc,
        model, input_sha256
    )
);

CREATE TRIGGER IF NOT EXISTS preview_snapshots_no_update
BEFORE UPDATE ON preview_snapshots
BEGIN
    SELECT RAISE(
        ABORT,
        'Preview snapshots are immutable'
    );
END;

CREATE TRIGGER IF NOT EXISTS preview_snapshots_no_delete
BEFORE DELETE ON preview_snapshots
BEGIN
    SELECT RAISE(
        ABORT,
        'Preview snapshots cannot be deleted'
    );
END;

CREATE TABLE IF NOT EXISTS preview_input_manifests (
    input_sha256 TEXT PRIMARY KEY,
    files_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS preview_manifests_no_update
BEFORE UPDATE ON preview_input_manifests
BEGIN
    SELECT RAISE(
        ABORT,
        'Input manifests are immutable'
    );
END;

CREATE TRIGGER IF NOT EXISTS preview_manifests_no_delete
BEFORE DELETE ON preview_input_manifests
BEGIN
    SELECT RAISE(
        ABORT,
        'Input manifests cannot be deleted'
    );
END;
"""


def main():

    print("=== BUBUS RPL PREVIEW V2 ===")

    if not DB.exists():
        raise SystemExit(
            "Journal V1 database missing. Refusing to create it."
        )

    now = datetime.now(timezone.utc)

    fixtures = read_csv(FIXTURES)
    games, current = load_games()

    if len(fixtures) != 8:
        raise RuntimeError("Expected exactly 8 fixtures")

    if len(current) < 117:
        raise RuntimeError("Current results unexpectedly incomplete")

    if any(
        datetime.fromisoformat(row["kickoff_utc"]).astimezone(
            timezone.utc
        ) <= now
        for row in fixtures
    ):
        raise RuntimeError(
            "Cannot snapshot matches after kickoff"
        )

    if any(
        game.day > now.astimezone(MSK).date()
        for game in games
    ):
        raise RuntimeError(
            "Future-dated result in historical inputs"
        )

    fingerprint, hashes = input_fingerprint()

    forecasts = calculate_predictions(games, fixtures)

    keys = {
        (
            f["competition"],
            f["season"],
            f["round"],
            f["home"],
            f["away"],
            f["kickoff_utc"],
            f["model"]
        )
        for f in forecasts
    }

    if len(forecasts) != 32 or len(keys) != 32:
        raise RuntimeError(
            f"Expected 32 unique predictions, got {len(keys)}"
        )

    conn = sqlite3.connect(
        f"file:{DB.resolve()}?mode=rw",
        uri=True
    )

    conn.execute("PRAGMA busy_timeout=5000")

    try:
        existing = {
            name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        if not {
            "preview_predictions",
            "committed_predictions",
            "match_results"
        }.issubset(existing):
            raise RuntimeError(
                "RPL Journal V1 schema missing"
            )

        conn.executescript(SCHEMA)

        created = now.isoformat()

        inserted = 0

        with conn:

            conn.execute("""
                INSERT OR IGNORE INTO preview_input_manifests
                (input_sha256, files_json, created_utc)
                VALUES (?, ?, ?)
            """, (
                fingerprint,
                json.dumps(hashes, sort_keys=True),
                created
            ))

            for f in forecasts:

                cur = conn.execute("""
                    INSERT OR IGNORE INTO preview_snapshots (
                        competition, season, round,
                        home, away, kickoff_utc,
                        model, model_version,
                        p_home, p_draw, p_away,
                        input_sha256, calculated_utc
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
                """, (
                    f["competition"],
                    f["season"],
                    f["round"],
                    f["home"],
                    f["away"],
                    f["kickoff_utc"],
                    f["model"],
                    f["model_version"],
                    f["p_home"],
                    f["p_draw"],
                    f["p_away"],
                    fingerprint,
                    created
                ))

                inserted += cur.rowcount

        total = conn.execute(
            "SELECT COUNT(*) FROM preview_snapshots"
        ).fetchone()[0]

        committed = conn.execute(
            "SELECT COUNT(*) FROM committed_predictions"
        ).fetchone()[0]

        print("Historical matches:", len(games))
        print("Current season:", len(current))
        print("Round 16 fixtures:", len(fixtures))
        print("Calculated forecasts:", len(forecasts))
        print("Input SHA256:", fingerprint)
        print("New preview rows:", inserted)
        print("All preview snapshots:", total)
        print("Committed predictions:", committed)

        print("\n=== V3 PREVIEW ===")

        for f in forecasts:
            if f["model"] != "V3":
                continue

            print(
                f'{f["home"]} — {f["away"]}: '
                f'П1={f["p_home"]:.1%} '
                f'Х={f["p_draw"]:.1%} '
                f'П2={f["p_away"]:.1%}'
            )

        print("\nPREVIEW SNAPSHOT SAVED")
        print("Committed predictions: NOT MODIFIED")
        print("UCL database: NOT MODIFIED")
        print("Telegram messages: NOT SENT")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
