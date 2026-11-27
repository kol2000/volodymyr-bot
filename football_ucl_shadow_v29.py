#!/usr/bin/env python3
"""V29: isolated controller.execute(run, commit=True) rehearsal.

This is an IN-PROCESS integration test with an injected subprocess runner.
It does NOT run the live V26 CLI, retrieve ESPN, or authenticate synthetic
scores as real football scores. It runs the real V26.evaluate(), V23.prepare(),
V14.do_import(), and V23 controller orchestration against a temporary V14 DB.
All fake scores have unmistakably SYNTHETIC provenance. Never schedule V29.
"""
import argparse
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import football_ucl_controller_v23 as controller
import football_ucl_results_v26 as v26
import football_ucl_forward_v23 as forward
import football_forward_ucl_v15 as v15
import football_ucl_shadow_v28 as v28

ROOT = Path(__file__).resolve().parent


def build_scores(db, calendar, mapping):
    """Synthetic first/second dual-source observations, not network evidence."""
    with closing(v28.ro_connection(db)) as conn:
        fixtures = []
        published = {}
        espn = {}
        for kickoff, raw_home, raw_away in calendar[5]:
            ledger=json.loads(
                Path('state/football_expansion/ucl_ledger_mapping.json')
                .read_text(encoding='utf-8')
            )
            home, away = (
                ledger[n] or n for n in (raw_home, raw_away)
            )
            row = conn.execute('''SELECT f.id, r.fixture_id FROM fixtures f
                LEFT JOIN results r ON f.id=r.fixture_id
                WHERE f.competition='UCL' AND kickoff_utc=? AND home=? AND away=?''',
                (kickoff.isoformat(timespec='seconds'), home, away)).fetchone()
            if not row:
                raise ValueError(f'R5 fixture missing: {home} vs {away}')
            fixtures.append((row[0], 5, kickoff, home, away, raw_home, raw_away, row[1] is not None))
            published[(5, kickoff.date().isoformat(), raw_home, raw_away)] = (2, 0)
            espn[(kickoff.date().isoformat(), raw_home, raw_away)] = (kickoff, (2, 0), f'V29-FAKE-{row[0]}')
    return fixtures, published, espn


def synthetic_result_write(db, ready, now):
    """Never call the real V22 result writer with made-up source hashes."""
    if not ready:
        return 0
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with conn:
            for fid, gh, ga, _event_id, _round in ready:
                conn.execute('''INSERT INTO results(fixture_id,home_goals,away_goals,result_source,recorded_utc)
                    VALUES(?,?,?,?,?)''',
                    (fid, gh, ga, 'V29 SYNTHETIC SHADOW ONLY: MOCK UEFA+ESPN; NOT REAL',
                     now.isoformat(timespec='microseconds')))
    return len(ready)


class ShadowRunner:
    """Adapter implementing subprocess.run API but routing writes to a clone."""
    def __init__(self, db, html, calendar, mapping, temp, now, fail_result=False,
                 partial_results=False, fail_forecast=False):
        self.db = Path(db)
        self.html = Path(html)
        self.calendar = calendar
        self.mapping = mapping
        self.temp = Path(temp)
        self.now = now
        self.fail_result = fail_result
        self.partial_results = partial_results
        self.fail_forecast = fail_forecast
        self.calls = []
        self.observations = {}
        self.fixtures, self.published, self.espn = build_scores(db, calendar, mapping)
        # A past observation does not authorize writing; require continuity check.
        first_time = now - timedelta(minutes=40)
        _, self.observations, _ = v26.evaluate(
            self.fixtures, self.published, self.espn, {}, first_time)

    def __call__(self, command, cwd=None, check=False, text=True):
        name = Path(command[1]).name
        args = command[2:]
        self.calls.append((name, tuple(args)))
        try:
            if name == 'football_ucl_results_v26.py':
                if self.fail_result:
                    raise RuntimeError('INJECTED result gate failure')
                if args == ['--observe']:
                    _, self.observations, _ = v26.evaluate(
                        self.fixtures, self.published, self.espn, self.observations, self.now)
                    print('V29 OBSERVE: simulated agreement only; no live evidence')
                elif args == ['--commit']:
                    ready, _, _ = v26.evaluate(
                        self.fixtures, self.published, self.espn, self.observations, self.now)
                    if self.partial_results:
                        ready = ready[:-1]
                    inserted = synthetic_result_write(self.db, ready, self.now)
                    print(f'V29 RESULT GATE: {inserted} synthetic scores committed to TEMP DB')
                else:
                    raise ValueError('Unexpected V26 args: ' + repr(args))
            elif name == 'football_ucl_forward_v23.py':
                if self.fail_forecast:
                    raise RuntimeError('INJECTED forecast failure')
                rnd = int(args[args.index('--round') + 1])
                day = args[args.index('--date') + 1]
                assert rnd == 6 and '--commit' in args
                selected = [f for f in self.calendar[rnd] if f[0].date().isoformat() == day]
                rows, simulated_now, _history = v28.prepare_day(self.db, self.html, day, selected)
                if simulated_now != self.now:
                    raise ValueError('Simulated clock mismatch')
                v28.import_day(self.db, rows, self.now, self.temp, day)
                print(f'V29 FORECAST: {len(rows)} real V23/V14 forecasts added to TEMP DB')
            else:
                raise RuntimeError(f'Unrecognized subprocess {name}')
            return SimpleNamespace(returncode=0)
        except Exception as exc:
            print(f'V29 BLOCKED SUBPROCESS: {exc}')
            return SimpleNamespace(returncode=2)


def verified_execute(runner, now, db, html, calendar):
    """Run real controller.execute with isolated references and injected runner."""
    with patch.object(controller, 'DB', Path(db)), \
         patch.object(controller, 'HTML', Path(html)), \
         patch.object(controller, 'RESULTS', ROOT/'football_ucl_results_v26.py'), \
         patch.object(controller, 'FORECAST', ROOT/'football_ucl_forward_v23.py'), \
         patch.object(controller, 'certify_calendar', return_value=calendar):
        return controller.execute('run', commit=True, now=now, runner=runner)


def counts(db):
    return {t: v[0] for t, v in v28.snapshot(db).items()}


def rehearse(source_db=v28.LIVE, source_html=v28.HTML):
    source_db = Path(source_db).resolve()
    source_html = Path(source_html).resolve()
    if not source_db.is_file() or not source_html.is_file():
        raise FileNotFoundError('Required V14 database or UEFA HTML missing')
    before = v28.snapshot(source_db)
    if counts(source_db) != {'fixtures': 18, 'predictions': 36, 'results': 0}:
        raise ValueError('V29 expects exactly 18 fixtures / 36 forecasts / 0 results')
    calendar, sha = forward.certified_calendar(
        source_html, v15.SNAPSHOT/'UCL_fixtures_2627.csv', 6)
    groups = v28.select_round6(calendar)
    mapping = json.loads(v15.MAPPING.read_text(encoding='utf-8'))
    print('V29 SHADOW ONLY — controller.run --commit, injected subprocess runner, fake UEFA/ESPN scores')
    print('Certified cached calendar:', sha, '| days:', ', '.join(groups), flush=True)
    with tempfile.TemporaryDirectory(prefix='bubus_v29_') as td:
        tmp = Path(td)
        clone = tmp/'shadow.sqlite3'
        html = tmp/'uefa.html'
        v28.backup(source_db, clone)
        shutil.copyfile(source_html, html)
        if v28.snapshot(clone) != before:
            raise RuntimeError('Backup mismatch')
        first_day = next(iter(groups))
        now = v28.clock_for_day(groups[first_day])
        runner = ShadowRunner(clone, html, calendar, mapping, tmp, now, fail_result=True)
        try:
            verified_execute(runner, now, clone, html, calendar)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Result gate failure did not stop controller')
        if v28.snapshot(clone) != before or len(runner.calls) != 1:
            raise RuntimeError('Result gate failure wrote to DB or continued pipeline')
        print('PASS: injected result gate failure blocks forecasts and leaves temp DB unchanged')

        runner = ShadowRunner(clone, html, calendar, mapping, tmp, now, partial_results=True)
        try:
            verified_execute(runner, now, clone, html, calendar)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Missing R5 result did not block forecasts')
        if counts(clone) != {'fixtures': 18, 'predictions': 36, 'results': 17}:
            raise RuntimeError('Partial result gate unexpectedly wrote forecasts')
        print('PASS: 17/18 prior results never authorize R6 forecasts')
        # A fresh clone to test complete scenario; partial results are not edited.
        clone.unlink()
        v28.backup(source_db, clone)
        for day, fixtures in groups.items():
            now = v28.clock_for_day(fixtures)
            runner = ShadowRunner(clone, html, calendar, mapping, tmp, now,
                                  fail_forecast=(day == first_day))
            # V23 must stop if forecast subprocess fails after results commit.
            if day == first_day:
                try:
                    verified_execute(runner, now, clone, html, calendar)
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError('Forecast failure not propagated')
                if counts(clone) != {'fixtures': 18, 'predictions': 36, 'results': 18}:
                    raise RuntimeError('Result and forecast failure recovery state unexpected')
                print('PASS: forecast crash leaves 18 synthetic results but no new forecasts')
                runner = ShadowRunner(clone, html, calendar, mapping, tmp, now)
            verified_execute(runner, now, clone, html, calendar)
            print(f'PASS: day {day} forecast state: {counts(clone)}')
            existing = v28.snapshot(clone)
            # Replay the same day: should not append duplicate forecasts.
            retry = ShadowRunner(clone, html, calendar, mapping, tmp, now)
            verified_execute(retry, now, clone, html, calendar)
            if v28.snapshot(clone) != existing:
                raise RuntimeError('Idempotency failed on retry')
            print(f'PASS: retry on {day} is idempotent')
        expected = {'fixtures': 36, 'predictions': 72, 'results': 18}
        if counts(clone) != expected:
            raise RuntimeError(f'Unexpected final temp state {counts(clone)} != {expected}')
        with closing(v28.ro_connection(source_db)) as original, closing(v28.ro_connection(clone)) as shadow:
            for table in ('fixtures', 'predictions'):
                orig_rows = original.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                new_rows = shadow.execute(f'SELECT * FROM {table} ORDER BY id LIMIT ?',
                                          (len(orig_rows),)).fetchall()
                if orig_rows != new_rows:
                    raise RuntimeError(f'Original {table} rows modified')
        print('PASS: final shadow contains 18 synthetic R5 results + 36 genuine new R6 forecasts')
        print('PASS: initial 36 predictions untouched, temporary work isolated')
    if v28.snapshot(source_db) != before:
        raise RuntimeError('SOURCE DATABASE CHANGED: inspect concurrent writer!')
    print('SUCCESS: original V14 untouched; NO network verification, NO real auto-commit approval')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=v28.LIVE)
    ap.add_argument('--html', type=Path, default=v28.HTML)
    args = ap.parse_args()
    try:
        rehearse(args.db, args.html)
    except (ValueError, OSError, RuntimeError, sqlite3.Error, KeyError, AssertionError) as exc:
        ap.exit(2, f'V29 BLOCKED: {exc}\nNo intentional production writes.\n')


if __name__ == '__main__':
    main()
