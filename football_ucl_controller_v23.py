#!/usr/bin/env python3
"""V23 UCL controller: V22 dual-source result gate -> V23 forward gate.

Default modes are read-only. Writes require `run --commit` explicitly.
V22 validates all registered fixtures in rounds 5-8.
Explicit run --commit required for any journal writes.
No systemd modifications, no writes to the operational Telegram bot DB.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'state/football_forward/forward_v14.sqlite3'
HTML = ROOT / 'state/football_forward/uefa_latest.html'
RESULTS = ROOT / 'football_ucl_results_v26.py'
FORECAST = ROOT / 'football_ucl_forward_v23.py'


def run_program(filename, args, runner=subprocess.run):
    command = [sys.executable, str(filename), *args]
    print('RUN:', ' '.join(command), flush=True)
    completed = runner(command, cwd=str(ROOT), check=False, text=True)
    if completed.returncode:
        raise RuntimeError(f'{filename.name} returned {completed.returncode}; stopping pipeline')


def status(db=DB):
    if not db.is_file():
        raise FileNotFoundError(f'Journal missing: {db}')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        for table in ('fixtures', 'predictions', 'results'):
            print(f'{table}: {c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]}')
    print('V22 coverage: registered UCL rounds 5-8, independent UEFA + ESPN.')
    print('V23 generator: complete prior rounds and certified cached calendar required.')


def pending_dates(now, calendar, lead_min=48, lead_max=72):
    """Return entire matchdays whose every fixture is 48-72h away."""
    selected = []
    for number in (6, 7, 8):
        groups = {}
        for kickoff, home, away in calendar[number]:
            groups.setdefault(kickoff.date().isoformat(), []).append(kickoff)
        for day, kicks in sorted(groups.items()):
            remaining = [(t - now).total_seconds() / 3600 for t in kicks]
            if remaining and all(lead_min <= hours <= lead_max for hours in remaining):
                selected.append((number, day))
    return selected


def forecast_date_state(day, expected):
    """Avoid rerunning an already locked day; refuse partial journal state."""
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as c:
        matches = c.execute("""SELECT f.id,COUNT(p.id) FROM fixtures f
          LEFT JOIN predictions p ON p.fixture_id=f.id
          WHERE f.competition='UCL' AND substr(f.kickoff_utc,1,10)=?
          GROUP BY f.id""", (day,)).fetchall()
    if not matches:
        return 'new'
    if len(matches) == expected and all(n == 2 for fid,n in matches):
        return 'locked'
    raise ValueError(f'Partial/conflicting forecast state on {day}: {len(matches)} fixtures; refuse to run')


def certify_calendar():
    # Uses V22's complete schedule including already scored UEFA matches.
    import football_ucl_forward_v23 as v19
    import football_forward_ucl_v15 as v15
    return v19.certified_calendar(HTML, v15.SNAPSHOT / 'UCL_fixtures_2627.csv', 6)[0]


def execute(mode, commit=False, now=None, runner=subprocess.run):
    if mode not in ('check', 'run'):
        raise ValueError('Invalid mode')
    if commit and mode != 'run':
        raise ValueError('--commit is only allowed with run')
    if not DB.is_file():
        raise FileNotFoundError(f'Journal missing: {DB}')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Naive time not permitted')

    # V22 needs a first score observation followed by >=30m before locking.
    # run --commit records a fresh observation before checking mature scores.
    # check remains fully read-only (no observation cache changes).
    if commit:
        run_program(RESULTS, ['--observe'], runner)
        run_program(RESULTS, ['--commit'], runner)
    else:
        run_program(RESULTS, [], runner)

    # Calendar is certified separately. Failure blocks all future forecast writes.
    calendar = certify_calendar()
    eligible = pending_dates(now, calendar)
    if not eligible:
        print('NO FORECAST WINDOW: no full matchday in rounds 6-8 within 48-72h.')
        print('RESULT MODE:', 'V22 commit enabled' if commit else 'READ ONLY')
        return
    for number, day in eligible:
        expected = sum(kickoff.date().isoformat() == day for kickoff, _, _ in calendar[number])
        state = forecast_date_state(day, expected)
        if state == 'locked':
            print(f'ALREADY LOCKED: {day}; no duplicate predictions')
            continue
        args = ['--round', str(number), '--date', day]
        if commit:
            args.append('--commit')
        run_program(FORECAST, args, runner)
    print('PIPELINE DONE:', 'COMMIT enabled' if commit else 'READ ONLY')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='mode', required=True)
    sub.add_parser('status', help='Read-only counts and safety coverage')
    sub.add_parser('check', help='Read-only V22 check and V23 when 48-72h window opens')
    p = sub.add_parser('run', help='Pipeline; read-only unless explicit --commit')
    p.add_argument('--commit', action='store_true', help='Allow V22 verified results and V23 forecasts to be committed')
    args = ap.parse_args()
    try:
        if args.mode == 'status':
            status()
        else:
            execute(args.mode, getattr(args, 'commit', False))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        ap.exit(2, f'BLOCKED: {exc}\n')


if __name__ == '__main__':
    main()
