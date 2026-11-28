#!/usr/bin/env python3
"""Standalone append-only, forward-only football forecast journal.

Does NOT generate predictions, scrape fixtures, or touch existing bot databases.
Feed this utility externally verified kickoff times and model probabilities.
"""
import argparse
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3

DEFAULT_DB = Path('state/football_forward/forward_v14.sqlite3')
COLUMNS = ('competition','kickoff_utc','home','away','model','version',
           'p_home','p_draw','p_away','fixture_source','prediction_source')


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    if not value or ('Z' not in value and '+' not in value[10:] and '-' not in value[10:]):
        raise ValueError('kickoff_utc requires explicit UTC offset, e.g. 2026-11-24T20:00:00+00:00')
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('Naive timestamp forbidden')
    return dt.astimezone(timezone.utc)


def open_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS fixtures (
      id INTEGER PRIMARY KEY,
      competition TEXT NOT NULL,
      kickoff_utc TEXT NOT NULL,
      home TEXT NOT NULL,
      away TEXT NOT NULL,
      fixture_source TEXT NOT NULL,
      registered_utc TEXT NOT NULL,
      UNIQUE(competition,kickoff_utc,home,away),
      CHECK(home<>away)
    );
    CREATE TABLE IF NOT EXISTS predictions (
      id INTEGER PRIMARY KEY,
      fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
      model TEXT NOT NULL,
      version TEXT NOT NULL,
      p_home REAL NOT NULL CHECK(p_home>=0 AND p_home<=1),
      p_draw REAL NOT NULL CHECK(p_draw>=0 AND p_draw<=1),
      p_away REAL NOT NULL CHECK(p_away>=0 AND p_away<=1),
      prediction_source TEXT NOT NULL,
      source_csv_sha256 TEXT,
      recorded_utc TEXT NOT NULL,
      UNIQUE(fixture_id,model,version)
    );
    CREATE TABLE IF NOT EXISTS results (
      fixture_id INTEGER PRIMARY KEY REFERENCES fixtures(id),
      home_goals INTEGER NOT NULL CHECK(home_goals>=0),
      away_goals INTEGER NOT NULL CHECK(away_goals>=0),
      result_source TEXT NOT NULL,
      recorded_utc TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS immutable_fixtures_update BEFORE UPDATE ON fixtures
      BEGIN SELECT RAISE(ABORT,'fixtures are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_fixtures_delete BEFORE DELETE ON fixtures
      BEGIN SELECT RAISE(ABORT,'fixtures are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_predictions_update BEFORE UPDATE ON predictions
      BEGIN SELECT RAISE(ABORT,'predictions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_predictions_delete BEFORE DELETE ON predictions
      BEGIN SELECT RAISE(ABORT,'predictions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_results_update BEFORE UPDATE ON results
      BEGIN SELECT RAISE(ABORT,'results are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_results_delete BEFORE DELETE ON results
      BEGIN SELECT RAISE(ABORT,'results are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS no_prediction_after_kickoff BEFORE INSERT ON predictions
      WHEN NEW.recorded_utc >= (SELECT kickoff_utc FROM fixtures WHERE id=NEW.fixture_id)
      BEGIN SELECT RAISE(ABORT,'forecast cannot be recorded at/after kickoff'); END;
    CREATE TRIGGER IF NOT EXISTS no_result_before_kickoff BEFORE INSERT ON results
      WHEN NEW.recorded_utc <= (SELECT kickoff_utc FROM fixtures WHERE id=NEW.fixture_id)
      BEGIN SELECT RAISE(ABORT,'result cannot be recorded before kickoff'); END;
    ''')
    conn.commit()
    return conn


def validate(row, now):
    for name in COLUMNS:
        if name not in row or not str(row[name]).strip():
            raise ValueError(f'Missing required field: {name}')
    kickoff = parse_timestamp(str(row['kickoff_utc']).strip())
    if kickoff <= now + timedelta(minutes=5):
        raise ValueError('Kickoff must be at least 5 minutes in the future')
    p = [float(row[x]) for x in ('p_home','p_draw','p_away')]
    if not all(math.isfinite(x) and 0 <= x <= 1 for x in p) or abs(sum(p)-1) > 1e-5:
        raise ValueError('Probabilities must be finite, 0..1 and sum to 1')
    strings = {k: str(row[k]).strip() for k in ('competition','home','away','model','version','fixture_source','prediction_source')}
    if strings['home'].casefold() == strings['away'].casefold():
        raise ValueError('Same home and away team')
    if any(len(x) > 600 for x in strings.values()):
        raise ValueError('Field too long')
    return kickoff.isoformat(timespec='seconds'), strings, p


def register(conn, row, now, csv_sha=None):
    kickoff, s, p = validate(row, now)
    fixture = conn.execute('''SELECT id,fixture_source FROM fixtures WHERE competition=? AND kickoff_utc=? AND home=? AND away=?''',
          (s['competition'],kickoff,s['home'],s['away'])).fetchone()
    if fixture is None:
        cur = conn.execute('''INSERT INTO fixtures(competition,kickoff_utc,home,away,fixture_source,registered_utc)
            VALUES(?,?,?,?,?,?)''', (s['competition'],kickoff,s['home'],s['away'],s['fixture_source'],now.isoformat(timespec='microseconds')))
        fixture_id=cur.lastrowid
    else:
        fixture_id=fixture['id']
        if fixture['fixture_source'] != s['fixture_source']:
            raise ValueError('Fixture source differs from original. Do not silently change fixture metadata.')
    conn.execute('''INSERT INTO predictions(fixture_id,model,version,p_home,p_draw,p_away,prediction_source,source_csv_sha256,recorded_utc)
        VALUES(?,?,?,?,?,?,?,?,?)''', (fixture_id,s['model'],s['version'],*p,s['prediction_source'],csv_sha,now.isoformat(timespec='microseconds')))
    return fixture_id


def do_import(conn, path):
    data=path.read_bytes()
    sha=hashlib.sha256(data).hexdigest()
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
            raise ValueError('CSV columns required: '+','.join(COLUMNS))
        rows=list(reader)
    if not rows:
        raise ValueError('CSV empty')
    now=utc_now()
    with conn:
        for i,row in enumerate(rows,2):
            try: register(conn,row,now,sha)
            except (ValueError,sqlite3.IntegrityError) as e: raise ValueError(f'CSV line {i}: {e}') from e
    print(f'LOCKED {len(rows)} predictions. CSV sha256={sha}. Recorded UTC={now.isoformat()}')


def add_result(conn,args):
    if args.home_goals > 30 or args.away_goals > 30 or min(args.home_goals,args.away_goals) < 0:
        raise ValueError('Invalid score')
    now=utc_now()
    with conn:
        f=conn.execute('SELECT * FROM fixtures WHERE id=?',(args.fixture_id,)).fetchone()
        if f is None: raise ValueError('Unknown fixture id')
        if now <= parse_timestamp(f['kickoff_utc']):
            raise ValueError('Cannot score before kickoff')
        conn.execute('INSERT INTO results(fixture_id,home_goals,away_goals,result_source,recorded_utc) VALUES(?,?,?,?,?)',
                     (args.fixture_id,args.home_goals,args.away_goals,args.source,now.isoformat(timespec='microseconds')))
    print(f'LOCKED result for fixture #{args.fixture_id}: {args.home_goals}:{args.away_goals}')


def report(conn):
    rows=conn.execute('''SELECT p.*,f.competition,f.kickoff_utc,f.home,f.away,r.home_goals,r.away_goals
        FROM predictions p JOIN fixtures f ON f.id=p.fixture_id LEFT JOIN results r ON r.fixture_id=f.id
        ORDER BY f.kickoff_utc,p.id''').fetchall()
    agg={}
    scored=0
    for r in rows:
        if r['home_goals'] is None: continue
        scored+=1
        y=0 if r['home_goals']>r['away_goals'] else 2 if r['home_goals']<r['away_goals'] else 1
        p=(r['p_home'],r['p_draw'],r['p_away'])
        key=(r['competition'],r['model'],r['version'])
        x=agg.setdefault(key,[0,0,0.0,0.0])
        x[0]+=1; x[1]+=int(p.index(max(p))==y)
        x[2]+=-math.log(max(1e-12,p[y])); x[3]+=sum((v-int(i==y))**2 for i,v in enumerate(p))
    print(f'Locked forecasts: {len(rows)}; scored predictions: {scored}; awaiting: {len(rows)-scored}')
    print('Competition | model | version | N | Accuracy | LogLoss | Brier')
    for key,x in sorted(agg.items()):
        print(' | '.join(map(str,key)),f'| {x[0]} | {x[1]/x[0]:.1%} | {x[2]/x[0]:.5f} | {x[3]/x[0]:.5f}')


def status(conn):
    print('Fixtures:',conn.execute('SELECT count(*) FROM fixtures').fetchone()[0])
    print('Forecasts:',conn.execute('SELECT count(*) FROM predictions').fetchone()[0])
    print('Results:',conn.execute('SELECT count(*) FROM results').fetchone()[0])
    for r in conn.execute('''SELECT f.id,f.competition,f.kickoff_utc,f.home,f.away,
      COUNT(p.id) AS preds,CASE WHEN r.fixture_id IS NULL THEN 'PENDING' ELSE 'SCORED' END AS state
      FROM fixtures f LEFT JOIN predictions p ON p.fixture_id=f.id
      LEFT JOIN results r ON r.fixture_id=f.id
      GROUP BY f.id ORDER BY f.kickoff_utc DESC LIMIT 30'''):
        print(f"#{r['id']} {r['competition']} {r['kickoff_utc']} {r['home']} - {r['away']} ({r['preds']} forecasts, {r['state']})")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('import',help='Import future forecasts as one atomic transaction')
    p.add_argument('--csv',type=Path,required=True)
    p=sub.add_parser('result',help='Record final score once, after kickoff')
    p.add_argument('--fixture-id',type=int,required=True)
    p.add_argument('--home-goals',type=int,required=True)
    p.add_argument('--away-goals',type=int,required=True)
    p.add_argument('--source',required=True)
    sub.add_parser('status');sub.add_parser('report')
    args=ap.parse_args()
    if args.db.resolve().name in ('forecasts_v7.sqlite3','bot.sqlite3'):
        ap.error('Refusing to open known operational database')
    try:
        with open_db(args.db) as conn:
            if args.command=='import':do_import(conn,args.csv)
            elif args.command=='result':add_result(conn,args)
            elif args.command=='status':status(conn)
            elif args.command=='report':report(conn)
    except (ValueError,sqlite3.Error,OSError) as e:
        ap.exit(2,f'ERROR: {e}\n')


if __name__=='__main__':main()
