#!/usr/bin/env python3
"""V24: fetch and atomically cache a validated UEFA UCL schedule.

No ESPN calls, result observations, predictions, or SQLite writes.
No stale HTML overwrite on network/parse/fixture verification failure.
"""
import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import football_ucl_results_v16 as v16
import football_ucl_results_v22 as v22
import football_forward_ucl_v15 as v15

ROOT = Path(__file__).resolve().parent
HTML = ROOT / 'state/football_forward/uefa_latest.html'
METADATA = ROOT / 'state/football_forward/uefa_latest_v24.json'
SNAPSHOT = ROOT / v15.SNAPSHOT / 'UCL_fixtures_2627.csv'
MAX_BYTES = 2_000_000


def validate(raw: bytes, fixtures_csv: Path):
    if not 10_000 < len(raw) <= MAX_BYTES:
        raise ValueError('UEFA HTML length invalid')
    calendar, scores = v22.verified_calendar(raw, fixtures_csv)
    counts = {r: len(calendar[r]) for r in (5, 6, 7, 8)}
    if counts != {5: 18, 6: 18, 7: 18, 8: 18}:
        raise ValueError(f'Wrong fixture counts: {counts}')
    return counts, len(scores)


def fetch_once(url=v16.URL, timeout=22):
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f'UEFA HTTP {response.status}')
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('UEFA response >2MB')
    return raw


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         prefix='.uefa_v24_', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def cache_validated(raw: bytes, html: Path, metadata: Path, fixtures_csv: Path, now=None):
    counts, n_scores = validate(raw, fixtures_csv)
    now = now or datetime.now(timezone.utc)
    digest = hashlib.sha256(raw).hexdigest()
    # HTML is the authoritative artifact; metadata is diagnostic only.
    atomic_write(html, raw)
    info = {
        'fetched_utc': now.isoformat(timespec='seconds'),
        'source_url': v16.URL,
        'sha256': digest,
        'bytes': len(raw),
        'fixtures_per_round': {str(k): v for k, v in counts.items()},
        'scored_fixtures': n_scores,
    }
    try:
        atomic_write(metadata, (json.dumps(info, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    except OSError as exc:
        print(f'WARNING: HTML saved but metadata not updated: {exc}', flush=True)
    print(f'VALIDATED UEFA HTML: {len(raw)} bytes sha256={digest}', flush=True)
    print(f'Rounds={counts} scored={n_scores}', flush=True)
    print(f'CACHE UPDATED: {html}', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Validate existing HTML offline; no writes')
    mode.add_argument('--fetch', action='store_true', help='Fetch latest HTML (default)')
    ap.add_argument('--html', type=Path, default=HTML)
    ap.add_argument('--metadata', type=Path, default=METADATA)
    ap.add_argument('--fixtures', type=Path, default=SNAPSHOT)
    ap.add_argument('--attempts', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=22)
    args = ap.parse_args(argv)
    try:
        if args.check:
            raw = args.html.read_bytes()
            counts, scored = validate(raw, args.fixtures)
            print(f'CACHE VALID: {len(raw)} bytes sha256={hashlib.sha256(raw).hexdigest()}')
            print(f'Rounds={counts} scored={scored}; READ ONLY')
            return 0
        if not 1 <= args.attempts <= 5 or not 1 <= args.timeout <= 60:
            raise ValueError('attempts must be 1..5 and timeout 1..60 seconds')
        # Failed fetch or validation must not replace the previously verified cache.
        errors = []
        for i in range(1, args.attempts + 1):
            try:
                raw = fetch_once(timeout=args.timeout)
                validate(raw, args.fixtures)
                cache_validated(raw, args.html, args.metadata, args.fixtures)
                return 0
            except (OSError, ValueError, TimeoutError) as exc:
                errors.append(str(exc))
                print(f'ATTEMPT {i}/{args.attempts} FAILED: {exc}', flush=True)
                if i < args.attempts:
                    time.sleep(2)
        raise RuntimeError('all UEFA attempts failed; existing HTML cache unchanged: ' + '; '.join(errors))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'BLOCKED: {exc}', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
