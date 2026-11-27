#!/usr/bin/env python3
"""V28: offline/synthetic V23 round-6 forecast rehearsal on disposable V14 clones.

Never modifies the real V14 database, UEFA cache, or systemd. Uses the real
V23.prepare(), V14.do_import() and SQLite triggers with a simulated clock.
Synthetic scores MUST NOT be interpreted as actual football results.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import football_ucl_forward_v23 as forward
import football_forward_ucl_v15 as v15
import football_forward_journal_v14 as journal

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / 'state/football_forward/forward_v14.sqlite3'
HTML = ROOT / 'state/football_forward/uefa_latest.html'
TABLES = ('fixtures', 'predictions', 'results')


def ro_connection(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def snapshot(path):
    """Hash complete records, not just counts, to catch any unexpected writes."""
    with closing(ro_connection(path)) as conn:
        result = {}
        for name in TABLES:
            rows = conn.execute(f'SELECT * FROM {name} ORDER BY rowid').fetchall()
            result[name] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
        return result


def backup(source, dest):
    with closing(ro_connection(source)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite clone integrity_check failed')


def clock_for_day(fixtures):
    """A 60h-before-first-kickoff UTC clock; require ALL fixtures in 48..72h."""
    kickoffs = [x[0] for x in fixtures]
    if not kickoffs:
        raise ValueError('Empty R6 matchday')
    now = min(kickoffs) - timedelta(hours=60)
    if not all(48 <= (t-now).total_seconds()/3600 <= 72 for t in kickoffs):
        raise ValueError('Cannot simulate full day in 48-72h window')
    return now


def select_round6(calendar):
    if len(calendar.get(6, [])) != 18:
        raise ValueError('Certified calendar must contain exactly 18 round-6 matches')
    groups = {}
    for fixture in calendar[6]:
        groups.setdefault(fixture[0].date().isoformat(), []).append(fixture)
    if sum(map(len, groups.values())) != 18:
        raise ValueError('Round-6 date grouping failed')
    return dict(sorted(groups.items()))


import json

def synthetic_results(db, calendar, mapping, alternate=False):
    """Insert demonstrably fictitious R5 results ONLY into an ephemeral SQLite clone."""
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        for kickoff, raw_home, raw_away in calendar[5]:
            ledger=json.loads(
                Path('state/football_expansion/ucl_ledger_mapping.json')
                .read_text(encoding='utf-8')
            )

            # Allow synthetic test teams only when explicitly supplied
            # by the test mapping. Production clubs must use the ledger.
            for name in (raw_home, raw_away):
                if name not in ledger:
                    if name not in mapping:
                        raise ValueError(
                            f'Unknown club without ledger mapping: {name}'
                        )
                    ledger[name] = mapping[name]

            home, away = (
                ledger[n] or n for n in (raw_home, raw_away)
            )
            row = conn.execute('''SELECT id FROM fixtures
                WHERE competition='UCL' AND kickoff_utc=? AND home=? AND away=?''',
                (kickoff.isoformat(timespec='seconds'), home, away)).fetchone()
            if row is None:
                raise ValueError(f'R5 fixture absent from V14: {home} - {away}')
            existing = conn.execute('SELECT 1 FROM results WHERE fixture_id=?', (row[0],)).fetchone()
            if existing:
                raise ValueError('R5 result already exists: V28 refuses to mix synthetic and real results')
            gh, ga = (0, 3) if alternate else (2, 0)
            recorded = kickoff + timedelta(hours=13)
            conn.execute('''INSERT INTO results
                (fixture_id,home_goals,away_goals,result_source,recorded_utc)
                VALUES(?,?,?,?,?)''',
                (row[0], gh, ga,
                 'V28 SYNTHETIC SHADOW ONLY; NOT A REAL RESULT',
                 recorded.isoformat(timespec='microseconds')))
        conn.commit()
        n = conn.execute('SELECT COUNT(*) FROM results').fetchone()[0]
    if n != 18:
        raise ValueError(f'Expected 18 synthetic R5 results, got {n}')


def make_args(db, html, date_str, commit=True):
    return SimpleNamespace(round_=6, **{
        'round': 6, 'date': date_str, 'commit': commit,
        'db': Path(db), 'html': Path(html), 'snapshot': v15.SNAPSHOT,
        'mapping': v15.MAPPING, 'cache': v15.CACHE,
    })


def prepare_day(db, html, day, fixtures):
    now = clock_for_day(fixtures)
    os.utime(html, (now.timestamp(), now.timestamp()))  # TEMP COPY ONLY
    args = make_args(db, html, day)
    rows, history, prior, html_sha = forward.prepare(args, now)
    if len(rows) != 2 * len(fixtures) or prior != 18:
        raise ValueError(f'Incomplete forecasts: rows={len(rows)}, prior={prior}')
    if any('v14_verified_prior_results=18' not in row['prediction_source'] for row in rows):
        raise ValueError('Forecast provenance does not include verified V14 results')
    return rows, now, history


def import_day(db, rows, now, folder, day):
    csvfile = Path(folder) / f'synthetic_r6_{day}.csv'
    with csvfile.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=journal.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    # V14.do_import uses utc_now(); override only in THIS process for the temp DB.
    with closing(journal.open_db(Path(db))) as conn:
        with patch.object(journal, 'utc_now', return_value=now):
            journal.do_import(conn, csvfile)
    return csvfile


def prediction_signature(rows):
    return {(r['kickoff_utc'], r['home'], r['away'], r['model']):
            (r['p_home'], r['p_draw'], r['p_away']) for r in rows}


def rehearse(db=LIVE, html=HTML):
    db, html = Path(db).resolve(), Path(html).resolve()
    if not db.is_file() or not html.is_file():
        raise FileNotFoundError('V14 SQLite or cached UEFA HTML missing')
    if db == html:
        raise ValueError('Invalid input paths')
    before = snapshot(db)
    if before['fixtures'][0] != 18 or before['predictions'][0] != 36 or before['results'][0] != 0:
        raise ValueError('Expected unscored V14 state: fixtures=18, predictions=36, results=0')
    calendar, digest = forward.certified_calendar(html, v15.SNAPSHOT/'UCL_fixtures_2627.csv', 6)
    if len(calendar[5]) != 18:
        raise ValueError('Certified round-5 calendar must contain 18 fixtures')
    groups = select_round6(calendar)
    import json
    mapping = json.loads(v15.MAPPING.read_text(encoding='utf-8'))
    print('V28 SHADOW ONLY — artificial R5 scores, synthetic UTC clock, real V23/V14 functions', flush=True)
    print('Certified UEFA calendar sha256:', digest, '| R6 dates:', ', '.join(groups))
    with tempfile.TemporaryDirectory(prefix='bubus_v28_') as temp:
        temp = Path(temp)
        baseline_db, alternate_db = temp/'baseline.sqlite3', temp/'alternate.sqlite3'
        backup(db, baseline_db)
        backup(db, alternate_db)
        if snapshot(baseline_db) != before or snapshot(alternate_db) != before:
            raise RuntimeError('SQLite backup differs from source')
        baseline_html = temp/'uefa_baseline.html'
        alternate_html = temp/'uefa_alternate.html'
        shutil.copyfile(html, baseline_html)
        shutil.copyfile(html, alternate_html)
        synthetic_results(baseline_db, calendar, mapping)
        synthetic_results(alternate_db, calendar, mapping, alternate=True)
        print('PASS: wrote 18 artificial R5 results to each temporary database')
        baseline_rows, alternate_rows = [], []
        for day, fixtures in groups.items():
            rows, now, hist = prepare_day(baseline_db, baseline_html, day, fixtures)
            alt, _, _ = prepare_day(alternate_db, alternate_html, day, fixtures)
            baseline_rows.extend(rows)
            alternate_rows.extend(alt)
            import_day(baseline_db, rows, now, temp, day)
            print(f'PASS: R6 {day}: {len(fixtures)} fixtures, {len(rows)} forecasts, history={hist}, ' 
                  f'simulated_now={now.isoformat()}')
            # True V23 duplicate gate: must reject a second run, even across versions.
            os.utime(baseline_html, (now.timestamp(), now.timestamp()))
            try:
                forward.prepare(make_args(baseline_db, baseline_html, day), now)
            except ValueError as exc:
                if 'existing forecasts' not in str(exc):
                    raise
                print(f'PASS: duplicate forecast blocked for {day}')
            else:
                raise RuntimeError(f'Duplicate gate unexpectedly accepted {day}')
        if len(baseline_rows) != 36 or len(alternate_rows) != 36:
            raise RuntimeError('Expected 36 baseline and 36 alternate R6 forecasts')
        sig_a, sig_b = prediction_signature(baseline_rows), prediction_signature(alternate_rows)
        if sig_a.keys() != sig_b.keys():
            raise RuntimeError('Fixture/model identity mismatch between scenarios')
        changed = sum(sig_a[k] != sig_b[k] for k in sig_a)
        if changed == 0:
            raise RuntimeError('MODEL SENSITIVITY FAILED: changing all R5 scores did not alter R6 probabilities')
        print(f'PASS: real Elo/Poisson12 calculations change with R5 scores: {changed}/36 forecasts differ')
        after = snapshot(baseline_db)
        if after['fixtures'][0] != 36 or after['predictions'][0] != 72 or after['results'][0] != 18:
            raise RuntimeError('Unexpected resulting shadow database sizes')
        # Validate original fixtures and predictions still present byte-for-byte at row level.
        with closing(ro_connection(db)) as src, closing(ro_connection(baseline_db)) as dst:
            for table in ('fixtures', 'predictions'):
                orig = src.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                clone_prefix = dst.execute(f'SELECT * FROM {table} ORDER BY id LIMIT ?', (len(orig),)).fetchall()
                if orig != clone_prefix:
                    raise RuntimeError(f'Original {table} records changed in shadow')
        print('PASS: shadow=36 fixtures / 72 predictions / 18 synthetic results')
        print('PASS: original 18 fixtures and 36 forecasts preserved byte-for-byte')
        print('PASS: all disposable files deleted after execution')
    if snapshot(db) != before:
        raise RuntimeError('LIVE DATABASE CHANGED during V28 rehearsal; investigate concurrent writer')
    print('SUCCESS: LIVE V14 unchanged; no permanent file, timer, or observation writes', flush=True)
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=LIVE, help='Source database (opened read-only)')
    ap.add_argument('--html', type=Path, default=HTML, help='Source UEFA cache (opened read-only)')
    args = ap.parse_args()
    try:
        rehearse(args.db, args.html)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
        ap.exit(2, f'V28 BLOCKED: {exc}\nThe tool does not intentionally write production files.\n')


if __name__ == '__main__':
    main()
