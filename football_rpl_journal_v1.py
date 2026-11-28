#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent

DB = (
    ROOT /
    "state/football_expansion/rpl_live/"
    "forecasts_v1.sqlite3"
)


def connect(path=DB):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS preview_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    competition TEXT NOT NULL DEFAULT 'RPL',
    season TEXT NOT NULL,
    round INTEGER NOT NULL,

    home TEXT NOT NULL,
    away TEXT NOT NULL,

    kickoff_utc TEXT NOT NULL,

    model TEXT NOT NULL,
    model_version TEXT NOT NULL,

    p_home REAL NOT NULL
        CHECK(p_home >= 0 AND p_home <= 1),

    p_draw REAL NOT NULL
        CHECK(p_draw >= 0 AND p_draw <= 1),

    p_away REAL NOT NULL
        CHECK(p_away >= 0 AND p_away <= 1),

    input_sha256 TEXT NOT NULL
        CHECK(length(input_sha256) = 64),

    calculated_utc TEXT NOT NULL,

    CHECK(ABS(p_home+p_draw+p_away-1.0)<0.000001),
    CHECK(home <> away),

    UNIQUE(
        competition, season,
        home, away, kickoff_utc, model
    )
);


CREATE TABLE IF NOT EXISTS committed_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    competition TEXT NOT NULL DEFAULT 'RPL',
    season TEXT NOT NULL,
    round INTEGER NOT NULL,

    home TEXT NOT NULL,
    away TEXT NOT NULL,

    kickoff_utc TEXT NOT NULL,

    model TEXT NOT NULL,
    model_version TEXT NOT NULL,

    p_home REAL NOT NULL
        CHECK(p_home >= 0 AND p_home <= 1),

    p_draw REAL NOT NULL
        CHECK(p_draw >= 0 AND p_draw <= 1),

    p_away REAL NOT NULL
        CHECK(p_away >= 0 AND p_away <= 1),

    input_sha256 TEXT NOT NULL
        CHECK(length(input_sha256) = 64),

    committed_utc TEXT NOT NULL,

    CHECK(ABS(p_home+p_draw+p_away-1.0)<0.000001),
    CHECK(home <> away),

    UNIQUE(
        competition, season,
        home, away, kickoff_utc, model
    )
);


CREATE TRIGGER IF NOT EXISTS committed_no_update
BEFORE UPDATE ON committed_predictions
BEGIN
    SELECT RAISE(
        ABORT,
        'Committed predictions are immutable'
    );
END;


CREATE TRIGGER IF NOT EXISTS committed_no_delete
BEFORE DELETE ON committed_predictions
BEGIN
    SELECT RAISE(
        ABORT,
        'Committed predictions cannot be deleted'
    );
END;


CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    competition TEXT NOT NULL DEFAULT 'RPL',
    season TEXT NOT NULL,

    home TEXT NOT NULL,
    away TEXT NOT NULL,

    kickoff_utc TEXT NOT NULL,

    home_goals INTEGER NOT NULL
        CHECK(home_goals >= 0),

    away_goals INTEGER NOT NULL
        CHECK(away_goals >= 0),

    source TEXT NOT NULL,
    verified_utc TEXT NOT NULL,

    UNIQUE(
        competition, season,
        home, away, kickoff_utc
    )
);


CREATE INDEX IF NOT EXISTS idx_rpl_preview_kickoff
ON preview_predictions(kickoff_utc);


CREATE INDEX IF NOT EXISTS idx_rpl_committed_kickoff
ON committed_predictions(kickoff_utc);
"""


def test_immutability(conn):

    print("\n=== IMMUTABILITY TEST ===")

    conn.execute("SAVEPOINT journal_test")

    try:
        conn.execute("""
            INSERT INTO committed_predictions (
                competition,
                season,
                round,
                home,
                away,
                kickoff_utc,
                model,
                model_version,
                p_home,
                p_draw,
                p_away,
                input_sha256,
                committed_utc
            )
            VALUES (
                'TEST',
                'test',
                0,
                'Home',
                'Away',
                '2099-01-01T12:00:00+00:00',
                'TEST',
                'test',
                0.5,
                0.3,
                0.2,
                ?,
                ?
            )
        """, (
            "0" * 64,
            datetime.now(timezone.utc).isoformat()
        ))

        row_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        blocked = 0

        for operation in (
            "UPDATE committed_predictions "
            "SET p_home=0.6 WHERE id=?",

            "DELETE FROM committed_predictions "
            "WHERE id=?",
        ):

            try:
                conn.execute(operation, (row_id,))
            except sqlite3.IntegrityError:
                blocked += 1

        assert blocked == 2, (
            "IMMUTABILITY TEST FAILED"
        )

        print("UPDATE blocked: YES")
        print("DELETE blocked: YES")
        print("Protection: OK")

    finally:
        conn.execute("ROLLBACK TO journal_test")
        conn.execute("RELEASE journal_test")

    print("Test prediction removed by rollback: YES")


def main():

    DB.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    print("=== BUBUS RPL JOURNAL V1 ===")
    print("Database:", DB)

    conn = connect()

    try:
        conn.executescript(SCHEMA)

        test_immutability(conn)

        print("\n=== TABLES ===")

        for table in (
            "preview_predictions",
            "committed_predictions",
            "match_results"
        ):

            count = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

            print(f"{table}: {count}")

        print("\n=== STATUS ===")
        print("Schema initialized: YES")
        print("Immutable committed predictions: YES")
        print("Real predictions inserted: NO")
        print("Production UCL database modified: NO")
        print("Telegram messages sent: NO")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
