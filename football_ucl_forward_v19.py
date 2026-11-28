#!/usr/bin/env python3
"""UCL forward-only V19. Read-only by default; explicit --commit only.

Requires UEFA cached page with an explicit generic CET kickoff rule, an exact
72-fixture schedule and complete previous rounds' scored V14 fixtures.
No result-scraping, no updates to bot DB or original CSVs.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import football_forward_ucl_v15 as v15
import football_forward_journal_v14 as journal
import football_mega_backtest_v12 as m
import football_ucl_schedule_audit_v17 as v17
import football_ucl_schedule_gate_v18 as v18

HTML = Path('state/football_forward/uefa_latest.html')
OUT = Path('state/football_expansion/forward_v19')
MODEL_VERSION = 'v19-ucl-20261122'
RULE = 'Kick-offs are 21:00 unless stated; all times CET.'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def certified_calendar(html, csv_path, round_no):
    raw = html.read_bytes()
    if len(raw) < 10000 or len(raw) > 2_000_000:
        raise ValueError('UEFA cache invalid size')
    parser = v17.Text(); parser.feed(raw.decode('utf-8', errors='replace'))
    full_text = ' '.join(parser.lines)
    if RULE not in full_text:
        raise ValueError('UEFA generic 21:00 CET rule missing; fail closed')
    official = v18.extract_strict(parser.lines)
    if {k: sum(key[0] == k for key in official) for k in range(5,9)} != {5:18,6:18,7:18,8:18}:
        raise ValueError('Incomplete UEFA fixture blocks; expected 18 games in each round 5-8')
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        records = list(csv.DictReader(f))
    if not records or not {'gw','date','time','home','away'}.issubset(records[0]):
        raise ValueError('Unexpected snapshot columns')
    by_round = {i: [] for i in range(5,9)}
    seen = set()
    for r in records:
        gw = int(r['gw'])
        if gw not in by_round: continue
        raw_home, raw_away = r['home'].strip(), r['away'].strip()
        official_key = (gw,r['date'].strip(),v17.NAMES[raw_home],v17.NAMES[raw_away])
        if official_key in seen: raise ValueError('Duplicate CSV fixture '+str(official_key))
        seen.add(official_key)
        if official_key not in official: raise ValueError('Unverified UEFA pairing '+str(official_key))
        explicit = official[official_key]
        expected = explicit or '21:00'
        if r['time'].strip() != expected:
            raise ValueError(f'Kickoff conflict {official_key}: csv={r["time"]!r}, official={expected!r}')
        day = date.fromisoformat(official_key[1])
        if day.month not in (11,12,1):
            raise ValueError('CET-to-UTC shortcut only validated for Nov, Dec, Jan')
        kickoff = datetime.combine(day,time.fromisoformat(expected),ZoneInfo('Europe/Paris'))
        if kickoff.utcoffset() != timedelta(hours=1):
            raise ValueError('CET offset mismatch')
        by_round[gw].append((kickoff.astimezone(timezone.utc),raw_home,raw_away))
    for gw in by_round:
        if len(by_round[gw]) != 18: raise ValueError(f'Round {gw} snapshot count {len(by_round[gw])}, expected 18')
        if sum(key[0] == gw for key in seen) != sum(key[0] == gw for key in official):
            raise ValueError('UEFA-only fixtures remain in round '+str(gw))
    return by_round, digest(raw)


def completed_prior_rounds(conn, calendar, target_round, mapping, now):
    """Return scored past-round Match objects, fail if any score missing.

    Requires prior rounds 5..target_round-1 to be explicitly registered in V14.
    Uses no future results and no automatic guesses.
    """
    extra=[]
    for round_no in range(5,target_round):
        for kickoff,raw_home,raw_away in calendar[round_no]:
            home,away = (mapping[name] or name for name in (raw_home,raw_away))
            row = conn.execute('''SELECT f.id,f.kickoff_utc,r.home_goals,r.away_goals,r.recorded_utc,r.result_source
                FROM fixtures f LEFT JOIN results r ON f.id=r.fixture_id
                WHERE f.competition='UCL' AND f.kickoff_utc=? AND f.home=? AND f.away=?''',
                (kickoff.isoformat(timespec='seconds'),home,away)).fetchone()
            if row is None or row['home_goals'] is None:
                raise ValueError(f'BLOCK: missing V14 result from round {round_no}: {kickoff.date()} {home} - {away}')
            if not row['result_source']:
                raise ValueError('Missing result source for '+str(row['id']))
            record_time = datetime.fromisoformat(row['recorded_utc'])
            if record_time.tzinfo is None or record_time > now:
                raise ValueError('Invalid or future result record time')
            if record_time < kickoff + timedelta(hours=2):
                raise ValueError('Result registered too soon after kickoff; manual verification needed')
            if now <= kickoff + timedelta(hours=2):
                raise ValueError('Result not sufficiently mature')
            gh,ga=row['home_goals'],row['away_goals']
            if min(gh,ga)<0 or max(gh,ga)>30:
                raise ValueError('Unreasonable result')
            extra.append(m.Match(kickoff.date(),'UCL',home,away,gh,ga,'V14 final '+row['result_source']))
    return extra


def prevent_duplicate(conn, rows):
    """Refuse any forecast for a match/model already journaled, across versions."""
    for r in rows:
        match = conn.execute('''SELECT f.id,f.fixture_source FROM fixtures f
          WHERE f.competition=? AND f.kickoff_utc=? AND f.home=? AND f.away=?''',
          (r['competition'],r['kickoff_utc'],r['home'],r['away'])).fetchone()
        if match is None: continue
        earlier=conn.execute('SELECT model,version FROM predictions WHERE fixture_id=?',(match['id'],)).fetchall()
        if earlier:
            raise ValueError(f'BLOCK existing forecasts for {r["home"]} - {r["away"]}: '+str([(x['model'],x['version']) for x in earlier]))
        if match['fixture_source'] != r['fixture_source']:
            raise ValueError('Existing fixture has different provenance')


def prepare(args, now):
    if args.round not in (6,7,8): raise ValueError('Only rounds 6-8 supported')
    calendar, html_sha = certified_calendar(args.html,args.snapshot/'UCL_fixtures_2627.csv',args.round)
    mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
    if not isinstance(mapping,dict): raise ValueError('Invalid team mapping')
    fixtures=[]
    for kickoff,raw_home,raw_away in calendar[args.round]:
        if raw_home not in mapping or raw_away not in mapping:
            raise ValueError('Unmapped club')
        home,away = (mapping[name] or name for name in (raw_home,raw_away))
        if home==away: raise ValueError('Team alias collision')
        fixtures.append((kickoff,home,away))
    if len(fixtures)!=18 or len(set(fixtures)) != 18: raise ValueError('Invalid target round')
    if args.date:
        fixtures=[item for item in fixtures if item[0].date().isoformat()==args.date]
        if not fixtures: raise ValueError('Specified date has no fixtures in this round')
    if args.commit:
        if not args.date: raise ValueError('Commit requires --date: two matchdays cannot both fit a 48-72h window')
        if now - datetime.fromtimestamp(args.html.stat().st_mtime, timezone.utc)>timedelta(hours=6):
            raise ValueError('UEFA HTML cache older than 6h; no committing')
        if datetime.fromtimestamp(args.html.stat().st_mtime, timezone.utc)>now+timedelta(minutes=5):
            raise ValueError('HTML cache timestamp in future')
        for kickoff,home,away in fixtures:
            hours=(kickoff-now).total_seconds()/3600
            if not 48<=hours<=72:
                raise ValueError(f'Commit window 48-72h not reached for {home} - {away}: {hours:.1f}h')
    conn=sqlite3.connect(f'file:{args.db.resolve()}?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        extra=completed_prior_rounds(conn,calendar,args.round,mapping,now)
        history=v15.load_archives(args.cache,args.snapshot,args.mapping)
        history.extend(extra)
        keys=[(x.day,x.home,x.away) for x in history]
        if len(keys)!=len(set(keys)): raise ValueError('Duplicate historical matches after V14 append')
        result_sha=digest((args.snapshot/'UCL_results_2627.csv').read_bytes())
        mapping_sha=digest(args.mapping.read_bytes())
        fixture_sha=digest((args.snapshot/'UCL_fixtures_2627.csv').read_bytes())
        rows=v15.make_predictions(history,fixtures,now,fixture_sha,result_sha,mapping_sha)
        if len(rows)!=2*len(fixtures): raise ValueError(f'Expected {2*len(fixtures)} forecasts, got {len(rows)}')
        for row in rows:
            row['version']=MODEL_VERSION
            row['fixture_source']=f'UEFA round {args.round} CET-rule html_sha256={html_sha}'
            row['prediction_source']+=f'; v14_verified_prior_results={len(extra)}; uefa_html_sha256={html_sha}'
            journal.validate(row,now)
        prevent_duplicate(conn,rows)
        return rows,len(history),len(extra),html_sha
    finally:
        conn.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--round',type=int,default=6)
    ap.add_argument('--date',help='Filter a calendar date (YYYY-MM-DD); mandatory for --commit')
    ap.add_argument('--html',type=Path,default=HTML)
    ap.add_argument('--snapshot',type=Path,default=v15.SNAPSHOT)
    ap.add_argument('--mapping',type=Path,default=v15.MAPPING)
    ap.add_argument('--cache',type=Path,default=v15.CACHE)
    ap.add_argument('--db',type=Path,default=journal.DEFAULT_DB)
    ap.add_argument('--output',type=Path,default=OUT)
    ap.add_argument('--commit',action='store_true',help='Permanently lock verified forecasts in V14')
    args=ap.parse_args()
    now=datetime.now(timezone.utc)
    try:
        rows,count,extra,html_sha=prepare(args,now)
        print(f'CHECK OK: round={args.round}; history={count}; verified prior results={extra}; page sha256={html_sha}')
        for row in rows:
            print(f"{row['kickoff_utc']} {row['home']} - {row['away']} [{row['model']}] H={float(row['p_home']):.1%} D={float(row['p_draw']):.1%} A={float(row['p_away']):.1%}")
        if not args.commit:
            print('DRY-RUN: no database or output file changes')
            return
        args.output.mkdir(parents=True,exist_ok=True)
        name=f'ucl_v19_round{args.round}_{args.date}_{now.strftime("%Y%m%dT%H%M%SZ")}.csv'
        target=args.output/name
        with target.open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=journal.COLUMNS)
            writer.writeheader();writer.writerows(rows)
        try:
            with journal.open_db(args.db) as conn:
                # Re-check after acquiring write lock, so no accidental duplication.
                conn.execute('BEGIN IMMEDIATE')
                prevent_duplicate(conn,rows)
                # do_import uses same connection and an internal transaction context.
                journal.do_import(conn,target)
        except Exception:
            print('CSV retained for audit:',target)
            raise
        print(f'COMMITTED {len(rows)} predictions. Provenance CSV:',target)
    except (ValueError,FileNotFoundError,KeyError,sqlite3.Error) as exc:
        ap.exit(2, f'BLOCKED: {exc}\nNo forecast writes completed (verify status if --commit was used).\n')

if __name__=='__main__':main()
