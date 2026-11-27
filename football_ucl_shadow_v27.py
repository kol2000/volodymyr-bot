#!/usr/bin/env python3
"""V27: rehearse V26 --commit on an ephemeral SQLite backup, NEVER production.

The source V14 database is opened read-only and copied with SQLite's backup API.
The real V26/UEFA/ESPN verification runs against the disposable clone.
No systemd changes, and no updates to production V14 or observation JSON.
"""
import argparse
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTION_DB = ROOT / 'state/football_forward/forward_v14.sqlite3'
V26 = ROOT / 'football_ucl_results_v26.py'
TABLES = ('fixtures', 'predictions', 'results')


def counts(db_path):
    db_path = Path(db_path).resolve()
    with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as c:
        return {name: c.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                for name in TABLES}


def backup_to_shadow(source, destination):
    """SQLite consistent backup; source is never opened for writing."""
    source = Path(source).resolve()
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            dst.execute('PRAGMA foreign_keys=ON')
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite backup integrity check failed')


def rehearse(source=PRODUCTION_DB, program=V26, runner=subprocess.run):
    source, program = Path(source).resolve(), Path(program).resolve()
    if not source.is_file() or not program.is_file():
        raise FileNotFoundError('Production DB or V26 script missing')
    before = counts(source)
    with tempfile.TemporaryDirectory(prefix='bubus_v27_shadow_') as temporary:
        shadow = Path(temporary) / 'v14_shadow.sqlite3'
        backup_to_shadow(source, shadow)
        if counts(shadow) != before:
            raise RuntimeError('Shadow database differs from source before test')
        cmd = [sys.executable, str(program), '--commit', '--db', str(shadow)]
        print('SHADOW ONLY: testing V26 --commit against a disposable SQLite backup', flush=True)
        result = runner(cmd, cwd=str(ROOT), check=False)
        after = counts(shadow)
        if result.returncode:
            print('SHADOW BLOCKED: V26 failed with exit code', result.returncode)
        if any(after[t] != before[t] for t in ('fixtures', 'predictions')):
            raise RuntimeError('V26 unexpectedly changed shadow fixtures or predictions')
        if after['results'] < before['results']:
            raise RuntimeError('V26 removed shadow results')
        print('SHADOW RESULTS:', before['results'], '->', after['results'],
              '(would add', after['results'] - before['results'], ')', flush=True)
        print('SHADOW FORECASTS:', after['predictions'], '(unchanged)', flush=True)
        print('SHADOW FILE DELETED; no production DB writes', flush=True)
    # Informational parity is checked after the shadow file is closed.
    current = counts(source)
    if current != before:
        print('NOTE: live V14 changed during shadow test, perhaps from another process:',
              before, '->', current, flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PRODUCTION_DB,
                        help='Read-only source SQLite. The V26 commit targets only a temporary clone.')
    args = parser.parse_args()
    try:
        raise SystemExit(rehearse(source=args.db))
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(2, f'SHADOW FAILED: {exc}\nNo intentional production writes.\n')


if __name__ == '__main__':
    main()
