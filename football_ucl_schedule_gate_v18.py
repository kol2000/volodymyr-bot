#!/usr/bin/env python3
"""V18: fail-closed, read-only UCL schedule and kickoff audit.

Fixes V17 article-text false positive with an exact club whitelist. Certifies
only *explicit* match-specific (HH:MM) lines from the UEFA article; a CSV clock
alone is NOT official confirmation. No imports into V14 and no DB writes.
"""
import argparse
import csv
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import football_ucl_schedule_audit_v17 as v17

CLOCK = re.compile(r'^\(([01]\d|2[0-3]):([0-5]\d)\)$')
CLUBS = set(v17.NAMES.values())


def extract_strict(lines):
    """Produce {(round,date,official_home,official_away): explicit_clock|None}."""
    result = {}
    errors = []
    round_no = None
    current_day = None
    previous_key = None
    for text in lines:
        rm = v17.ROUND_RE.fullmatch(text)
        if rm:
            round_no = int(rm.group(1))
            current_day = previous_key = None
            continue
        if round_no is None:
            continue
        dm = v17.DATE_RE.fullmatch(text)
        if dm:
            parsed = datetime.strptime(' '.join(dm.groups()[1:]), '%d %B %Y').date()
            if parsed.strftime('%A') != dm.group(1):
                errors.append(f'Wrong weekday: {text}')
            current_day = parsed.isoformat()
            previous_key = None
            continue
        if current_day is None:
            continue
        cm = CLOCK.fullmatch(text)
        if cm and previous_key is not None:
            if result[previous_key] is not None:
                errors.append(f'Duplicate clock for {previous_key}')
            else:
                result[previous_key] = text[1:-1]
            previous_key = None
            continue
        previous_key = None
        if ' vs ' not in text or v17.SCORE_RE.search(text):
            continue
        pair = text.split(' vs ')
        if len(pair) != 2:
            continue
        home, away = [x.strip() for x in pair]
        # Exact known-club match only. Article prose like 'Aston Villa: Their...'
        # cannot become a fixture, irrespective of the matchday heading.
        if home not in CLUBS or away not in CLUBS or home == away:
            continue
        key = (round_no, current_day, home, away)
        if key in result:
            errors.append(f'Duplicate fixture: {key}')
        else:
            result[key] = None
            previous_key = key
    if errors:
        raise ValueError('; '.join(errors))
    return result


def check_snapshot(snapshot, official, today):
    with snapshot.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows or not {'gw','date','time','home','away'}.issubset(rows[0]):
        raise ValueError('Unexpected fixture CSV format')
    keys = set()
    checked = []
    problems = []
    for r in rows:
        key = (int(r['gw']), r['date'].strip(), v17.NAMES[r['home'].strip()], v17.NAMES[r['away'].strip()])
        if key in keys:
            raise ValueError(f'Duplicate CSV fixture {key}')
        keys.add(key)
        if key[0] < 5 or key[1] < today:
            continue
        if key not in official:
            problems.append(f'NOT ON UEFA PAGE {key}')
            continue
        csv_clock = r['time'].strip()
        explicit = official[key]
        if explicit is not None and csv_clock != explicit:
            problems.append(f'TIME CONFLICT: {key} CSV={csv_clock} UEFA={explicit}')
        checked.append((key, csv_clock, explicit))
    future_official = {k for k in official if k[0]>=5 and k[1]>=today}
    extra = future_official - {k for k, _, _ in checked}
    for key in sorted(extra):
        problems.append(f'UEFA ONLY {key}')
    return checked, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot', type=Path, default=v17.SNAPSHOT)
    ap.add_argument('--html', type=Path, help='use saved HTML (no network)')
    args = ap.parse_args()
    if args.html:
        raw = args.html.read_bytes()
    else:
        req = Request(v17.URL, headers={'User-Agent':'Mozilla/5.0', 'Accept':'text/html'})
        with urlopen(req, timeout=30) as response:
            if response.status != 200: raise RuntimeError(f'UEFA HTTP {response.status}')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000: raise RuntimeError('UEFA page exceeds 2MB')
    parser = v17.Text()
    parser.feed(raw.decode('utf-8', errors='replace'))
    official = extract_strict(parser.lines)
    today = datetime.now(timezone.utc).date().isoformat()
    checked, problems = check_snapshot(args.snapshot, official, today)
    print('V18 UEFA article SHA256:', hashlib.sha256(raw).hexdigest())
    print('UEFA exact-club fixtures by round:', dict(sorted(Counter(k[0] for k in official).items())))
    print('Current/future CSV fixture pairs checked:', len(checked))
    explicit = [r for r in checked if r[2] is not None]
    unverified = [r for r in checked if r[2] is None]
    print('Explicit UEFA match-specific kickoff clocks:', len(explicit))
    print('Kickoff clocks NOT independently certified:', len(unverified))
    for key, csv_clock, official_clock in checked:
        if official_clock is not None:
            print('CLOCK CONFIRMED', key, official_clock)
    for key, csv_clock, official_clock in unverified:
        print('CLOCK NEEDS VERIFICATION', key, 'CSV='+csv_clock)
    for issue in problems:
        print('BLOCKER:', issue)
    print('READ ONLY: no SQLite writes, no predictions registered, no files changed.')
    if problems:
        raise SystemExit(2)
    if unverified:
        print('NOT READY for automatic forward locking: unverified kickoffs remain.')
    else:
        print('All clocks explicitly stated; further timezone and freshness audit still required.')


if __name__ == '__main__':
    main()
