#!/usr/bin/env python3
"""V15 UCL Nov 24-25 forward forecasts; read-only by default.

Only independently checked kickoff schedule; no invented future scores;
uses V12 historical Elo/Poisson12 and V14 forward-only journal.
"""
import argparse
import collections
import csv
from datetime import datetime, date, time, timezone, timedelta
import hashlib
import json
from pathlib import Path

import football_mega_backtest_v12 as m
import football_forward_journal_v14 as journal

ROOT = Path('state/football_expansion')
SNAPSHOT = ROOT / 'source/rpl_ucl_20261122T015615Z'
CACHE = ROOT / 'mega_v11'
MAPPING = ROOT / 'ucl_team_mapping.json'
OUT = ROOT / 'forward_v15'
UEFA = ('https://www.uefa.com/uefachampionsleague/news/'
        '02a8-2174c9e9019d-f909a77bd77a-1000--2026-27-champions-league-all-the-league-phase-fixtures/')
# These 18 pairings and kickoffs were cross-checked with UEFA's matchday-5 schedule.
# All times here are CET (UTC+01:00 on these November dates).
OFFICIAL = {
    '2026-11-24': {
        ('Bodø/Glimt', 'LASK'): '18:45',
        ('Galatasaray', 'Aston Villa'): '18:45',
        ('Arsenal', 'B. Dortmund'): '21:00',
        ('Como', 'AEK Athens'): '21:00',
        ('Feyenoord', 'Porto'): '21:00',
        ('Man City', 'Napoli'): '21:00',
        ('Leipzig', 'Lens'): '21:00',
        ('Real Madrid', 'PSV'): '21:00',
        ('S. Bratislava', 'Real Betis'): '21:00',
    },
    '2026-11-25': {
        ('Sabah', 'Barcelona'): '18:45',
        ('Slavia Praha', 'Villarreal'): '18:45',
        ('Atleti', 'Viking'): '21:00',
        ('Club Brugge', 'Liverpool'): '21:00',
        ('Inter', 'Stuttgart'): '21:00',
        ('Shakhtar', 'Fenerbahçe'): '21:00',
        ('Lille', 'Bayern München'): '21:00',
        ('Paris', 'Roma'): '21:00',
        ('Sporting CP', 'Man Utd'): '21:00',
    },
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_archives(cache=CACHE, snapshot=SNAPSHOT, mapping=MAPPING):
    history=[]
    for season in m.SEASONS:
        path=cache/f'UCL_{season}.txt'
        if not path.is_file():
            raise FileNotFoundError(path)
        rows, skipped = m.parse_ucl_txt(path,season)
        expected=125 if season=='2023-24' else 189
        if len(rows)!=expected or skipped:
            raise ValueError(f'Historical UCL {season}: {len(rows)} games; skipped {len(skipped)}')
        history.extend(rows)
    resultpath=snapshot/'UCL_results_2627.csv'
    current=m.parse_current(resultpath,'UCL')
    if len(current)!=72:
        raise ValueError(f'Unexpected current UCL result count {len(current)}; review data snapshot')
    current=m.canonicalize_current_ucl(current,history,mapping)
    combined=history+current
    keys=[(x.day,x.home,x.away) for x in combined]
    if len(keys)!=len(set(keys)):
        raise ValueError('Duplicate matches in history')
    return sorted(combined,key=lambda x:(x.day,x.home,x.away))


def ratings_before(history, target):
    ratings=collections.defaultdict(lambda:1500.0)
    by_day=collections.defaultdict(list)
    for row in history:
        if row.day < target:
            by_day[row.day].append(row)
    for day in sorted(by_day):
        changes=collections.defaultdict(float)
        for row in by_day[day]:
            rh,ra=ratings[row.home],ratings[row.away]
            points=(1.,.5,0.)[row.actual]
            delta=20*(points-m.elo_expected(rh,ra))
            changes[row.home]+=delta
            changes[row.away]-=delta
        for team,change in changes.items():
            ratings[team]+=change
    return ratings


def checked_fixtures(snapshot=SNAPSHOT,mapping_path=MAPPING):
    with mapping_path.open(encoding='utf-8') as f:
        mapping=json.load(f)
    with (snapshot/'UCL_fixtures_2627.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    chosen={}
    for row in rows:
        day=row['date'].strip()
        if day not in OFFICIAL:
            continue
        raw_pair=(row['home'].strip(),row['away'].strip())
        if raw_pair not in OFFICIAL[day]:
            raise ValueError(f'UNVERIFIED UCL FIXTURE {day} {raw_pair}; refuse entire batch')
        key=(day,raw_pair)
        if key in chosen:
            raise ValueError(f'Duplicate future fixture {key}')
        official_time=OFFICIAL[day][raw_pair]
        csv_time=row.get('time','').strip()
        if csv_time and csv_time != official_time:
            raise ValueError(f'Kickoff discrepancy {key}: CSV {csv_time}, UEFA {official_time}')
        if not all(team in mapping for team in raw_pair):
            raise ValueError(f'Unmapped current team in fixture {key}')
        home,away=(mapping[team] or team for team in raw_pair)
        if home==away:
            raise ValueError(f'Alias collision in {key}')
        # Explicit CET to UTC conversion, no assumption about server timezone.
        kickoff=datetime.combine(date.fromisoformat(day),time.fromisoformat(official_time),tzinfo=timezone(timedelta(hours=1)))
        chosen[key]=(kickoff.astimezone(timezone.utc),home,away)
    expected=sum(map(len,OFFICIAL.values()))
    if len(chosen)!=expected:
        missing=[(day,pair) for day, games in OFFICIAL.items() for pair in games if (day,pair) not in chosen]
        raise ValueError(f'Missing {len(missing)} UEFA matchday-5 games in snapshot: {missing}')
    return sorted(chosen.values())


def make_predictions(history,fixtures,now,fixture_hash,result_hash,mapping_hash):
    rows=[]
    for kickoff,home,away in fixtures:
        if kickoff<=now+timedelta(minutes=5):
            print('SKIP already started/too close:',kickoff.isoformat(),home,away)
            continue
        before=[x for x in history if x.day<kickoff.date()]
        ratings=ratings_before(history,kickoff.date())
        # Dummy scores are never included in the past and are never used for updating ratings.
        candidate=m.Match(kickoff.date(),'UCL',home,away,0,0,'future-fixture-only')
        poisson=m.poisson1(before,candidate)
        elo=m.elo_probs(ratings[home],ratings[away])
        for name, probs, actual_model in (
            ('Elo',elo,'Elo'),
            ('Poisson12_or_Elo',poisson if poisson is not None else elo,
             'Poisson12' if poisson is not None else 'Elo-fallback'),
        ):
            if len(probs)!=3 or abs(sum(probs)-1)>1e-6:
                raise ValueError(f'Invalid model probabilities for {home} vs {away}')
            rows.append({
                'competition':'UCL',
                'kickoff_utc':kickoff.isoformat(timespec='seconds'),
                'home':home,'away':away,'model':name,'version':'v15-ucl-20261122',
                'p_home':f'{probs[0]:.10f}','p_draw':f'{probs[1]:.10f}','p_away':f'{probs[2]:.10f}',
                'fixture_source':'UEFA 2026/27 matchday 5 verified; '+UEFA,
                'prediction_source':f'V12 {actual_model}; local snapshot results_sha256={result_hash}; mapping_sha256={mapping_hash}; fixtures_sha256={fixture_hash}',
            })
    return rows


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--commit',action='store_true',help='Permanently lock predictions in V14 database')
    ap.add_argument('--cache',type=Path,default=CACHE)
    ap.add_argument('--snapshot',type=Path,default=SNAPSHOT)
    ap.add_argument('--mapping',type=Path,default=MAPPING)
    ap.add_argument('--output',type=Path,default=OUT)
    ap.add_argument('--db',type=Path,default=journal.DEFAULT_DB)
    args=ap.parse_args()
    now=datetime.now(timezone.utc)
    matches=load_archives(args.cache,args.snapshot,args.mapping)
    fixtures=checked_fixtures(args.snapshot,args.mapping)
    rows=make_predictions(matches,fixtures,now,
                          sha(args.snapshot/'UCL_fixtures_2627.csv'),
                          sha(args.snapshot/'UCL_results_2627.csv'),sha(args.mapping))
    print(f'UTC now={now.isoformat()} historical games={len(matches)} checked upcoming games={len(fixtures)} candidate predictions={len(rows)}')
    for r in rows:
        print(f"{r['kickoff_utc']} {r['home']} - {r['away']} [{r['model']}] "
              f"H={float(r['p_home']):.1%} D={float(r['p_draw']):.1%} A={float(r['p_away']):.1%}")
    if not rows:
        print('Nothing to record. No files changed.')
        return
    if not args.commit:
        print('DRY-RUN: no files or databases changed. Use --commit only after reviewing pairings and probabilities.')
        return
    # Existing predictions stay immutable. Do not update or silently duplicate them.
    conn=journal.open_db(args.db)
    fresh=[]
    for r in rows:
        kickoff,s,_=journal.validate(r,datetime.now(timezone.utc))
        found=conn.execute('''SELECT p.id,f.fixture_source FROM predictions p JOIN fixtures f ON f.id=p.fixture_id
            WHERE f.competition=? AND f.kickoff_utc=? AND f.home=? AND f.away=? AND p.model=? AND p.version=?''',
            (s['competition'],kickoff,s['home'],s['away'],s['model'],s['version'])).fetchone()
        if found:
            if found['fixture_source'] != s['fixture_source']:
                raise ValueError('Existing fixture provenance conflict')
            print('ALREADY LOCKED:',r['home'],r['away'],r['model'])
        else:
            fresh.append(r)
    if not fresh:
        print('All forecasts already locked. No changes made.')
        return
    args.output.mkdir(parents=True,exist_ok=True)
    target=args.output/('ucl_forward_'+now.strftime('%Y%m%dT%H%M%SZ')+'.csv')
    if target.exists():
        raise FileExistsError(target)
    with target.open('x',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=journal.COLUMNS)
        w.writeheader();w.writerows(fresh)
    journal.do_import(conn,target)
    print('LOCKED',len(fresh),'forecasts; preserved source CSV:',target)

if __name__=='__main__':
    main()
