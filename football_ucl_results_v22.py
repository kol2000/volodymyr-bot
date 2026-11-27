#!/usr/bin/env python3
"""V22: conservative UEFA+ESPN results observer for UCL rounds 5-8.

Registered V14 fixtures only. Read-only by default; --observe updates only an
independent observation JSON file; --commit appends final results to V14.
Both write modes require fresh cached UEFA HTML, independently finished ESPN
matches, matching teams/kickoffs/scores, >=12h since kickoff, and (for commit)
>=30m since the first matching observation. Never overwrites any V14 record.

Do not enable auto-commit before real-match acceptance checks.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import football_ucl_results_v16 as v16
import football_ucl_results_v20 as v20
import football_ucl_schedule_gate_v18 as v18
import football_forward_ucl_v15 as v15
import football_ucl_schedule_audit_v17 as v17

BASE = Path('state/football_forward')
HTML = BASE / 'uefa_latest.html'
DB = BASE / 'forward_v14.sqlite3'
OBS = BASE / 'uefa_observations_v22.json'
LEGACY = BASE / 'uefa_observations_v16.json'
UTC = timezone.utc
DATE_RE = v17.DATE_RE
ROUND_RE = v17.ROUND_RE
SCORE = re.compile(r'^(\d{1,2})\s*[-–:]\s*(\d{1,2})$')


def verified_calendar(raw, csv_path):
    """Verify the 72 official fixtures, whether still listed as VS or as scores.

    Crucial: V19's future-only fixture parser stops recognizing games after
    UEFA replaces 'A vs B' with 'A 2-1 B'. Never depend on it after a round.
    """
    parser = v16.TextParser()
    parser.feed(raw.decode('utf-8', errors='replace'))
    if 'Kick-offs are 21:00 unless stated; all times CET.' not in ' '.join(parser.parts):
        raise ValueError('UEFA global kickoff rule absent')
    fixtures = v18.extract_strict(parser.parts)
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        records = list(csv.DictReader(f))
    if not records or not {'gw','date','time','home','away'}.issubset(records[0]):
        raise ValueError('Invalid snapshot format')
    calendar = {i: [] for i in range(5, 9)}
    csv_keys = set()
    for row in records:
        rnd = int(row['gw'])
        if rnd not in calendar:
            continue
        home = row['home'].strip()
        away = row['away'].strip()
        key = (rnd, row['date'].strip(), v17.NAMES[home], v17.NAMES[away])
        if key in csv_keys:
            raise ValueError('Duplicate CSV fixture ' + str(key))
        csv_keys.add(key)
    scored = official_scores(raw, csv_keys)
    page_keys = set(fixtures) | set(scored)
    if page_keys != csv_keys or any(sum(x[0] == rnd for x in csv_keys) != 18 for rnd in calendar):
        raise ValueError('UEFA does not certify exactly 72 matchday 5-8 fixtures/results')
    if set(fixtures) & set(scored):
        raise ValueError('UEFA displays same fixture as future and scored')
    for row in records:
        rnd = int(row['gw'])
        if rnd not in calendar:
            continue
        raw_home, raw_away = row['home'].strip(), row['away'].strip()
        key = (rnd,row['date'].strip(),v17.NAMES[raw_home],v17.NAMES[raw_away])
        clock = row['time'].strip()
        if key in fixtures:
            official_clock = fixtures[key] or '21:00'
            if official_clock != clock:
                raise ValueError('UEFA kickoff mismatch ' + str(key))
        elif clock not in ('18:45','21:00'):
            # The exact clock is no longer shown after UEFA publishes the score.
            # Its original time must therefore have been certified at V14 entry.
            raise ValueError('Unexpected scored-game clock ' + str(key))
        day = date.fromisoformat(key[1])
        if day.month not in (11, 12, 1):
            raise ValueError('CET validation limited to Nov/Dec/Jan')
        kickoff = datetime.combine(day, time.fromisoformat(clock), ZoneInfo('Europe/Paris'))
        if kickoff.utcoffset() != timedelta(hours=1):
            raise ValueError('CET offset changed')
        calendar[rnd].append((kickoff.astimezone(UTC), raw_home, raw_away))
    return calendar, scored


def registered_fixtures(conn, calendar, mapping):
    """Validate every registered UCL fixture against the certified UTC schedule."""
    expected = {}
    for rnd in range(5, 9):
        for kickoff, raw_home, raw_away in calendar[rnd]:
            home, away = (mapping[x] or x for x in (raw_home, raw_away))
            if home == away:
                raise ValueError('Mapping alias collision')
            key = (kickoff.isoformat(timespec='seconds'), home, away)
            if key in expected:
                raise ValueError('Duplicate certified fixture')
            expected[key] = (rnd, raw_home, raw_away)
    rows = conn.execute('''SELECT f.id, f.kickoff_utc, f.home, f.away, r.fixture_id, f.fixture_source
        FROM fixtures f LEFT JOIN results r ON r.fixture_id=f.id
        WHERE f.competition='UCL' ORDER BY f.id''').fetchall()
    output = []
    seen = set()
    for fid, when, home, away, result_id, fixture_source in rows:
        if not isinstance(fixture_source, str) or not fixture_source.startswith('UEFA '):
            raise ValueError(f'Uncertified fixture provenance for #{fid}')
        kickoff = datetime.fromisoformat(when.replace('Z', '+00:00'))
        if kickoff.tzinfo is None:
            raise ValueError(f'Naive DB kickoff for #{fid}')
        key = (kickoff.astimezone(UTC).isoformat(timespec='seconds'), home, away)
        if key not in expected:
            raise ValueError(f'UNVERIFIED registered fixture #{fid}: {key}')
        if key in seen:
            raise ValueError('Duplicate registered fixture: ' + str(key))
        seen.add(key)
        rnd, display_home, display_away = expected[key]
        output.append((fid, rnd, kickoff.astimezone(UTC), home, away,
                       display_home, display_away, result_id is not None))
    return output


def official_scores(raw, expected):
    """Extract only exact dated scores for expected official (round, date, team) keys.

    An article sentence containing a team and a score cannot match a complete
    expected fixture string. Scores on the wrong round/date are rejected.
    """
    parser = v16.TextParser()
    parser.feed(raw.decode('utf-8', errors='replace'))
    keys = set(expected)
    by_day = defaultdict(list)
    for key in keys:
        by_day[(key[0], key[1])].append(key)
    current_round = None
    current_day = None
    found = {}
    for line in parser.parts:
        match = ROUND_RE.fullmatch(line)
        if match:
            current_round = int(match.group(1))
            current_day = None
            continue
        if current_round is None:
            continue
        match = DATE_RE.fullmatch(line)
        if match:
            parsed = datetime.strptime(' '.join(match.groups()[1:]), '%d %B %Y').date()
            if parsed.strftime('%A') != match.group(1):
                raise ValueError('UEFA score date has wrong weekday: ' + line)
            current_day = parsed.isoformat()
            continue
        if current_day is None:
            continue
        for key in by_day.get((current_round, current_day), ()):  # exact pair
            _, _, home, away = key
            prefix = home + ' '
            suffix = ' ' + away
            if not (line.startswith(prefix) and line.endswith(suffix)):
                continue
            between = line[len(prefix):-len(suffix)]
            score = SCORE.fullmatch(between)
            if not score:
                continue
            gh, ga = (int(score.group(1)), int(score.group(2)))
            if gh > 30 or ga > 30:
                raise ValueError('Unreasonable UEFA score for ' + str(key))
            if key in found:
                raise ValueError('Duplicate UEFA score for ' + str(key))
            found[key] = (gh, ga)
    return found


def get_espn(dates, offline=None):
    if offline:
        doc = json.loads(offline.read_text(encoding='utf-8'))
        if not isinstance(doc, dict) or not isinstance(doc.get('events'), list):
            raise ValueError('Offline ESPN JSON requires events array')
        return [doc], ['offline']
    docs, digests = [], []
    for day in sorted(dates):
        # Unlike V20, the date set is derived from certified registered fixtures.
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        url = v20.ESPN_BASE + '?' + urlencode({'dates': day.replace('-', ''), 'limit': '100'})
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urlopen(req, timeout=25) as response:
            if response.status != 200:
                raise ValueError(f'ESPN HTTP {response.status} for {day}')
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('ESPN response too large')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get('events'), list):
            raise ValueError('Invalid ESPN response for ' + day)
        docs.append(doc)
        digests.append(hashlib.sha256(raw).hexdigest())
    return docs, digests


def load_observations(path, legacy=None):
    observations = {}
    if legacy and legacy.is_file():
        observations.update(v16.load_observations(legacy))
    if path.is_file():
        observations.update(v16.load_observations(path))
    return observations


def evaluate(fixtures, published, espn, observations, now):
    """Compute candidate observations and commits without writing anything."""
    ready = []
    new_obs = dict(observations)
    messages = []
    for fid, rnd, kickoff, home, away, display_home, display_away, done in fixtures:
        label = f'#{fid} R{rnd} {home} - {away}'
        if done:
            messages.append(label + ' ALREADY RECORDED')
            continue
        date = kickoff.date().isoformat()
        key = (rnd, date, display_home, display_away)
        score = published.get(key)
        if score is None:
            messages.append(label + ' WAIT UEFA score')
            continue
        if now < kickoff + timedelta(hours=12):
            messages.append(label + ' WAIT >=12h after kickoff')
            continue
        provider = espn.get((date, display_home, display_away))
        if provider is None:
            messages.append(label + ' WAIT ESPN STATUS_FULL_TIME')
            continue
        espn_kickoff, espn_score, event_id = provider
        if abs((espn_kickoff - kickoff).total_seconds()) > 30 * 60:
            messages.append(label + ' BLOCK kickoff discrepancy')
            continue
        if score != espn_score:
            messages.append(label + f' BLOCK score discrepancy UEFA={score}, ESPN={espn_score}')
            continue
        token = '|'.join((date, home, away))
        prior = new_obs.get(token)
        since = None
        if isinstance(prior, dict) and prior.get('score') == list(score):
            try:
                since = datetime.fromisoformat(prior['first_seen_utc'])
                if since.tzinfo is None or since > now:
                    since = None
            except (KeyError, ValueError, TypeError):
                since = None
        if since is None:
            new_obs[token] = {'score': list(score), 'first_seen_utc': now.isoformat()}
            messages.append(label + ' OBSERVE first confirmed score; wait 30m')
            continue
        if now - since < timedelta(minutes=30):
            messages.append(label + ' WAIT 30m stable score')
            continue
        ready.append((fid, score[0], score[1], str(event_id), rnd))
        messages.append(label + f' READY {score[0]}:{score[1]} ESPN={event_id}')
    return ready, new_obs, messages


def save_observations(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.v22_', suffix='.tmp', delete=False) as f:
            name = f.name
            json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def commit_results(db, ready, now, html_hash, espn_hashes):
    if not ready:
        return 0
    conn = sqlite3.connect(f'file:{db.resolve()}?mode=rw', uri=True)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    count = 0
    try:
        with conn:
            for fid, hg, ag, eid, rnd in ready:
                if conn.execute('SELECT 1 FROM results WHERE fixture_id=?', (fid,)).fetchone():
                    continue
                source = (f'UEFA {v16.URL} round={rnd} sha256={html_hash}; '
                          f'ESPN {v20.ESPN_BASE} event_id={eid} '
                          f'scoreboard_sha256={",".join(espn_hashes)}; '
                          'dual-source final; >=30m repeated observation; >=12h after kickoff')
                conn.execute('''INSERT INTO results
                    (fixture_id,home_goals,away_goals,result_source,recorded_utc)
                    VALUES(?,?,?,?,?)''', (fid, hg, ag, source, now.isoformat(timespec='microseconds')))
                count += 1
    finally:
        conn.close()
    return count


def run(args):
    now = datetime.now(UTC)
    if args.commit and (args.offline_espn or args.observe):
        raise ValueError('--commit cannot use offline ESPN or --observe')
    if args.observe and args.offline_espn:
        raise ValueError('--observe cannot use offline ESPN')
    if not args.db.is_file():
        raise FileNotFoundError(args.db)
    raw = args.html.read_bytes()
    if not 10000 < len(raw) < 2_000_000:
        raise ValueError('UEFA HTML length invalid')
    html_hash = hashlib.sha256(raw).hexdigest()
    if args.observe or args.commit:
        age = now - datetime.fromtimestamp(args.html.stat().st_mtime, UTC)
        if age > timedelta(hours=2) or age < -timedelta(minutes=5):
            raise ValueError('UEFA HTML cache stale or future: writes blocked')
    calendar, all_scored = verified_calendar(raw, args.snapshot / 'UCL_fixtures_2627.csv')
    mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
    if not isinstance(mapping, dict):
        raise ValueError('Invalid mapping')
    conn = sqlite3.connect(f'file:{args.db.resolve()}?mode=ro', uri=True)
    try:
        fixtures = registered_fixtures(conn, calendar, mapping)
    finally:
        conn.close()
    print(f'V22 registered UCL games: {len(fixtures)}; UEFA sha256={html_hash}')
    keys = {(rnd, kickoff.date().isoformat(), dh, da)
            for _, rnd, kickoff, _, _, dh, da, _ in fixtures}
    published = {k: v for k, v in all_scored.items() if k in keys}
    dates = {kickoff.date().isoformat() for _, _, kickoff, _, _, _, _, done in fixtures if not done}
    docs, hashes = get_espn(dates, args.offline_espn) if dates else ([], [])
    espn = {}
    for doc in docs:
        # Match pairs may recur across rounds; date is part of identity.
        for pair, value in v20.parse_espn([doc]).items():
            event_day = value[0].date().isoformat()
            key = (event_day, *pair)
            if key in espn:
                raise ValueError('Duplicate ESPN full-time fixture '+str(key))
            espn[key] = value
    prior = load_observations(args.observations, args.legacy)
    ready, updated, messages = evaluate(fixtures, published, espn, prior, now)
    print('UEFA scores:', len(published), 'ESPN mapped full-time:', len(espn))
    for item in messages:
        print(item)
    print('READY:', len(ready))
    if args.observe:
        # Observation cache only, never a V14 DB modification.
        save_observations(args.observations, updated)
        print('OBSERVED: cache updated; 0 SQLite writes')
    elif args.commit:
        count = commit_results(args.db, ready, now, html_hash, hashes)
        print('COMMITTED:', count, 'results; previous predictions untouched')
    else:
        print('DRY RUN: 0 SQLite writes, 0 cache writes')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--observe', action='store_true', help='Update V22 observation JSON only (no SQLite writes)')
    ap.add_argument('--commit', action='store_true', help='Append verified final scores to immutable V14')
    ap.add_argument('--db', type=Path, default=DB)
    ap.add_argument('--html', type=Path, default=HTML)
    ap.add_argument('--observations', type=Path, default=OBS)
    ap.add_argument('--legacy', type=Path, default=LEGACY)
    ap.add_argument('--snapshot', type=Path, default=v15.SNAPSHOT)
    ap.add_argument('--mapping', type=Path, default=Path('state/football_expansion/ucl_ledger_mapping.json'))
    ap.add_argument('--offline-espn', type=Path, help='Dry-run-only local ESPN JSON for tests')
    args = ap.parse_args()
    try:
        run(args)
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        ap.exit(2, f'BLOCKED: {exc}\nNo result writes completed (check database if --commit used).\n')


if __name__ == '__main__':
    main()
