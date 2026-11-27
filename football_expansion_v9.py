#!/usr/bin/env python3
"""Bubus V9: isolated data-source onboarding for national-team matches and UCL audit.
No changes to the Telegram bot, V7 forecasts DB, or existing datasets.
National-team CSV is historical RESULTS only: it has dates, not kickoff times.
"""
import argparse
import csv
import hashlib
import io
import json
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path('state/football_expansion')
INTERNATIONAL_CSV = BASE / 'source' / 'international_results.csv'
INTERNATIONAL_DB = BASE / 'international_results.sqlite3'
RESULTS_URL = 'https://raw.githubusercontent.com/martj42/international_results/master/results.csv'
UCL_API = 'https://api.github.com/repos/openfootball/champions-league/contents/2025-26'
REQUIRED = ('date', 'home_team', 'away_team', 'home_score', 'away_score',
            'tournament', 'city', 'country', 'neutral')


def fetch(url, max_bytes=10_000_000):
    req = urllib.request.Request(url, headers={'User-Agent':'Bubus-Source-Audit/1.0', 'Accept':'text/csv, application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('Source larger than safety limit')
        return data


def parse_internationals(raw):
    content = raw.decode('utf-8-sig')
    rdr = csv.DictReader(io.StringIO(content))
    if not set(REQUIRED).issubset(rdr.fieldnames or []):
        raise ValueError(f'Unexpected CSV fields: {rdr.fieldnames}')
    out = []
    seen = set()
    repeated_fixture_keys = set()
    fixture_counts = Counter()
    for line, row in enumerate(rdr, 2):
        try:
            day = date.fromisoformat(row['date'])
            home, away = row['home_team'].strip(), row['away_team'].strip()
            h, a = int(row['home_score']), int(row['away_score'])
            neutral = {'TRUE':1, 'FALSE':0}[row['neutral'].strip().upper()]
            tournament = row['tournament'].strip()
            if not home or not away or home == away or h < 0 or a < 0 or h > 40 or a > 40 or not tournament:
                raise ValueError('invalid team/score/tournament')
            if day > date.today():
                raise ValueError('future result')
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f'Malformed row {line}: {exc}') from exc
        # Same teams can play more than once on a calendar date.
        # Reject exact duplicate rows, not distinct matches with the same fixture key.
        key = (day.isoformat(), home, away, tournament)
        record = (day.isoformat(), home, away, h, a, tournament,
                  row['city'].strip(), row['country'].strip(), neutral)
        if record in seen:
            raise ValueError(f'Exact duplicate match row {line}: {record}')
        seen.add(record)
        fixture_counts[key] += 1
        if fixture_counts[key] > 1:
            repeated_fixture_keys.add(key)
        out.append(record)
    if not out:
        raise ValueError('Empty dataset')
    if repeated_fixture_keys:
        print(f'INFO: {len(repeated_fixture_keys)} fixture keys occur more than once '               '(same date/teams/tournament); distinct source rows preserved.')
        for key in sorted(repeated_fixture_keys)[:10]:
            print('  Repeated fixture key:', key, 'rows:', fixture_counts[key])
    return out


def write_database(target, rows, checksum, obtained):
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic rebuild of this *new, isolated* dataset. No change to forecast DB.
    tmp = target.with_name(target.name + '.tmp')
    if tmp.exists():
        tmp.unlink()
    try:
        with sqlite3.connect(tmp) as db:
            db.executescript('''
                CREATE TABLE matches (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  match_date TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL,
                  home_goals INTEGER NOT NULL, away_goals INTEGER NOT NULL,
                  competition TEXT NOT NULL, city TEXT, country TEXT,
                  neutral INTEGER NOT NULL CHECK (neutral IN (0,1)),
                  UNIQUE(match_date, home, away, home_goals, away_goals, competition, city, country, neutral)
                );
                CREATE INDEX idx_matches_date ON matches(match_date);
                CREATE INDEX idx_matches_comp ON matches(competition, match_date);
                CREATE TABLE provenance (source_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL, downloaded_utc TEXT NOT NULL,
                    rows_count INTEGER NOT NULL);
            ''')
            db.executemany('''INSERT INTO matches
                (match_date, home, away, home_goals, away_goals, competition, city, country, neutral)
                VALUES (?,?,?,?,?,?,?,?,?)''', rows)
            db.execute('INSERT INTO provenance VALUES (?,?,?,?)',
                       (RESULTS_URL, checksum, obtained, len(rows)))
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()


def show_summary(rows):
    print(f'Сборные: всего результатов {len(rows)}; первый {rows[0][0]}; последний {max(r[0] for r in rows)}')
    by_year = Counter(r[0][:4] for r in rows)
    for year in ('2023', '2024', '2025', '2026'):
        print(f'  {year}: {by_year[year]} матчей')
    recent = [r for r in rows if r[0] >= '2024-01-01']
    def count(p):
        return sum(p(r[5].casefold()) for r in recent)
    for label, pred in [
        ('Лига наций', lambda s:'nations league' in s),
        ('Товарищеские', lambda s:'friendly' in s),
        ('Отбор ЧМ', lambda s:'fifa world cup qualification' in s),
        ('Отбор Евро', lambda s:'uefa euro qualification' in s or 'uefa european championship qualification' in s),
        ('Чемпионат мира', lambda s:'fifa world cup' in s and 'qualification' not in s),
    ]:
        print(f'  {label} (с 2024): {count(pred)}')
    print('Последние 8 игр сборной России в данном источнике:')
    ru = [r for r in rows if r[1] == 'Russia' or r[2] == 'Russia']
    for r in sorted(ru, reverse=True)[:8]:
        print(f'  {r[0]} {r[1]} {r[3]}:{r[4]} {r[2]} ({r[5]})')


def audit_ucl():
    print('\n=== ЛЧ: наличие исторического источника ===')
    try:
        data = json.loads(fetch(UCL_API, 1_000_000))
        if not isinstance(data, list):
            raise ValueError('Unexpected GitHub API response')
        for entry in data[:30]:
            if entry.get('type') == 'file':
                print(' ', entry.get('name'), entry.get('size'), 'bytes')
        if not data:
            print('  Каталог пуст.')
        print('  Это только проверка наличия файлов; данные ЛЧ ещё НЕ подключены к V3.')
    except Exception as exc:
        print(f'  Не удалось проверить каталог: {type(exc).__name__}: {exc}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-internationals', action='store_true', help='Fetch one CC0 historical CSV snapshot')
    parser.add_argument('--audit-ucl', action='store_true', help='Read-only audit of openfootball UCL folder on GitHub')
    parser.add_argument('--csv', type=Path, default=INTERNATIONAL_CSV)
    parser.add_argument('--db', type=Path, default=INTERNATIONAL_DB)
    args = parser.parse_args()

    if args.download_internationals:
        raw = fetch(RESULTS_URL)
        rows = parse_internationals(raw)  # validate before writing
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        # Source snapshot is never overwritten: keep old file with matching hash.
        digest = hashlib.sha256(raw).hexdigest()
        snapshot = args.csv.with_name(f'international_results_{digest[:12]}.csv')
        if not snapshot.exists():
            snapshot.write_bytes(raw)
        print('Исходный снимок:', snapshot)
    elif args.csv.exists():
        raw = args.csv.read_bytes()
        rows = parse_internationals(raw)
        digest = hashlib.sha256(raw).hexdigest()
    else:
        rows = None
        digest = None
        print('Нет локального CSV. Для получения используйте --download-internationals')

    if rows is not None:
        show_summary(rows)
        if args.download_internationals:
            write_database(args.db, rows, digest, datetime.now(timezone.utc).isoformat())
            print('Создана отдельная база сборных:', args.db)
            print('SHA-256:', digest)
        else:
            print('Режим просмотра: база не изменена.')
    if args.audit_ucl:
        audit_ucl()
    print('\nРПЛ: Football-Data прекратил обновление после 2025/26; нужен иной актуальный источник.')
    print('ВАЖНО: для матчей сборных есть только даты, а не UTC kickoff; автофиксация LIVE прогнозов запрещена.')
    print('V7/V8, Telegram и state/football_live/forecasts_v7.sqlite3 не изменены.')


if __name__ == '__main__':
    main()
