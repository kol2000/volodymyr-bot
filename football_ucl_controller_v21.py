#!/usr/bin/env python3
"""V21 pilot orchestrator: V20 dual-source result gate -> V19 forward gate.

Default modes are read-only. Writes require `run --commit` explicitly.
V20 validates ONLY matchday 5 (Nov 24-25). Therefore V21 intentionally
REFUSES automatic rounds 7/8; they require extended result verification.
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
RESULTS = ROOT / 'football_ucl_results_v20.py'
FORECAST = ROOT / 'football_ucl_forward_v19.py'


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
    print('V20 pilot coverage: only matchday 5; rounds 7/8 AUTO-COMMIT DISABLED')
    print('V19 accepts only complete prior rounds and fresh certified calendar.')


def pending_dates(now, calendar, lead_min=48, lead_max=72):
    """Return entire matchdays whose every fixture is 48-72h away."""
    selected = []
    for number in (6,):  # hard safety gate; V20 only supplies results for round 5
        groups = {}
        for kickoff, home, away in calendar[number]:
            groups.setdefault(kickoff.date().isoformat(), []).append(kickoff)
        for day, kicks in sorted(groups.items()):
            remaining = [(t - now).total_seconds() / 3600 for t in kicks]
            if remaining and all(lead_min <= hours <= lead_max for hours in remaining):
                selected.append((number, day))
    return selected


def forecast_date_state(day):
    """Avoid rerunning an already locked day; refuse partial journal state."""
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as c:
        matches = c.execute("""SELECT f.id,COUNT(p.id) FROM fixtures f
          LEFT JOIN predictions p ON p.fixture_id=f.id
          WHERE f.competition='UCL' AND substr(f.kickoff_utc,1,10)=?
          GROUP BY f.id""", (day,)).fetchall()
    if not matches:
        return 'new'
    if len(matches) == 9 and all(n == 2 for fid,n in matches):
        return 'locked'
    raise ValueError(f'Partial/conflicting forecast state on {day}: {len(matches)} fixtures; refuse to run')


def certify_calendar():
    # Reuses V19's existing full 72-game check, including exact generic CET rule.
    import football_ucl_forward_v19 as v19
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

    # All modes validate source status. Only explicit run --commit may write results.
    run_program(RESULTS, ['--commit'] if commit else [], runner)

    # Calendar is certified separately. Failure blocks all future forecast writes.
    calendar = certify_calendar()
    eligible = pending_dates(now, calendar)
    if not eligible:
        print('NO FORECAST WINDOW: no complete round-6 matchday within 48-72h.')
        print('Rounds 7/8 intentionally disabled until V20 verifies their finals.')
        print('RESULT MODE:', 'COMMIT enabled for V20 matchday 5' if commit else 'READ ONLY')
        return
    for number, day in eligible:
        state = forecast_date_state(day)
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
    sub.add_parser('check', help='Read-only V20 check and V19 when 48-72h window opens')
    p = sub.add_parser('run', help='Pipeline; read-only unless explicit --commit')
    p.add_argument('--commit', action='store_true', help='Allow V20 results and V19 forecasts to be committed')
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
