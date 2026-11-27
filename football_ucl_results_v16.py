#!/usr/bin/env python3
"""Conservative official UEFA article result watcher for locked V14 UCL fixtures.

Dry-run by default. Requires two identical observations >=30 min apart,
>=12 hours after scheduled kickoff, and explicit --commit to insert results.
Does not change fixtures or predictions. Standard library only.
"""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

URL = ('https://www.uefa.com/uefachampionsleague/news/'
       '02a8-2174c9e9019d-f909a77bd77a-1000--'
       '2026-27-champions-league-all-the-league-phase-fixtures/')
BASE = Path('state/football_forward')
DB = BASE / 'forward_v14.sqlite3'
CACHE = BASE / 'uefa_observations_v16.json'

# Exact UEFA display names -> exact identities stored by V15. Never fuzzy match.
PAIRS = (
 ('Bodø/Glimt','LASK','FK Bodø/Glimt','LASK'),
 ('Galatasaray','Aston Villa','Galatasaray SK','Aston Villa FC'),
 ('Arsenal','Borussia Dortmund','Arsenal FC','Borussia Dortmund'),
 ('Como','AEK Athens','Como','AEK Athens'),
 ('Feyenoord','Porto','Feyenoord Rotterdam','Porto'),
 ('Manchester City','Napoli','Manchester City FC','SSC Napoli'),
 ('Leipzig','Lens','RB Leipzig','Lens'),
 ('Real Madrid','PSV Eindhoven','Real Madrid CF','PSV'),
 ('Slovan Bratislava','Real Betis','ŠK Slovan Bratislava','Real Betis'),
 ('Slavia Praha','Villarreal','SK Slavia Praha','Villarreal CF'),
 ('Sabah','Barcelona','Sabah','FC Barcelona'),
 ('Atlético de Madrid','Viking','Club Atlético de Madrid','Viking'),
 ('Club Brugge','Liverpool','Club Brugge KV','Liverpool FC'),
 ('Inter','Stuttgart','FC Internazionale Milano','VfB Stuttgart'),
 ('Shakhtar Donetsk','Fenerbahçe','FK Shakhtar Donetsk','Fenerbahçe'),
 ('Lille','Bayern München','Lille OSC','FC Bayern München'),
 ('Paris Saint-Germain','Roma','Paris Saint-Germain FC','Roma'),
 ('Sporting CP','Manchester United','Sporting Clube de Portugal','Man Utd'),
)

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        value = ' '.join(html.unescape(data).split())
        if value:
            self.parts.append(value)

def parse_page(raw):
    p = TextParser()
    p.feed(raw.decode('utf-8', errors='replace'))
    lines = p.parts
    # Restrict to matchday 5 block. In particular, do not accept earlier round scores.
    indices = [i for i, x in enumerate(lines) if x == 'Matchday 5']
    if len(indices) != 1:
        raise ValueError(f'Expected exactly one Matchday 5 heading, got {len(indices)}')
    start = indices[0]
    ends = [i for i in range(start+1, len(lines)) if lines[i] == 'Matchday 6']
    if not ends:
        raise ValueError('Missing Matchday 6 heading; aborting')
    block = lines[start+1:ends[0]]
    found = {}
    active_day = None
    expected_dates = {
        'Tuesday 24 November 2026': '2026-11-24',
        'Wednesday 25 November 2026': '2026-11-25',
    }
    for line in block:
        if line in expected_dates:
            active_day = expected_dates[line]
            continue
        for display_home, display_away, home, away in PAIRS:
            pattern = re.compile(r'^'+re.escape(display_home)+r'\s+(\d{1,2})\s*[-–:]\s*(\d{1,2})\s+'+re.escape(display_away)+r'$')
            match = pattern.fullmatch(line)
            if match:
                if active_day is None:
                    raise ValueError(f'Score outside dated section: {line}')
                key = (active_day,home,away)
                if key in found:
                    raise ValueError(f'Duplicate published result: {key}')
                found[key] = (int(match.group(1)),int(match.group(2)))
    if len(found) > len(PAIRS):
        raise ValueError('More results than scheduled games')
    return found

def fetch():
    request = Request(URL, headers={'User-Agent':'Mozilla/5.0','Accept':'text/html'})
    with urlopen(request,timeout=30) as response:
        if response.status != 200:
            raise ValueError(f'Unexpected HTTP status: {response.status}')
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('UEFA response too large')
        return data

def load_observations(path):
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj,dict):
        raise ValueError('Observation cache is not an object')
    return obj

def save_observations(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8')
    os.replace(tmp,path)

def process(conn, raw, prior, now, commit=False, observe=False):
    published=parse_page(raw)
    rows = conn.execute("""SELECT f.id,f.competition,f.kickoff_utc,f.home,f.away,
                    CASE WHEN r.fixture_id IS NOT NULL THEN 1 ELSE 0 END
                    FROM fixtures f LEFT JOIN results r ON f.id=r.fixture_id
                    WHERE f.competition='UCL' ORDER BY f.id""").fetchall()
    expected={(a,b) for _,_,a,b in PAIRS}
    if len(rows)!=18 or {(row[3],row[4]) for row in rows}!=expected:
        raise ValueError('V14 UCL fixture identities differ from verified V15 pilot; abort')
    next_cache = dict(prior)
    ready=[]
    status=[]
    for fid,competition,kickoff_txt,home,away,done in rows:
        kickoff=datetime.fromisoformat(kickoff_txt.replace('Z','+00:00'))
        if kickoff.tzinfo is None:
            raise ValueError('Naive kickoff timestamp')
        date=kickoff.astimezone(timezone.utc).date().isoformat()
        if date not in ('2026-11-24','2026-11-25'):
            raise ValueError(f'Unexpected kickoff date: {fid} {date}')
        key=(date,home,away)
        label=f'#{fid} {date} {home} - {away}'
        if done:
            status.append(f'{label}: already recorded')
            continue
        if key not in published:
            status.append(f'{label}: no published score')
            continue
        score=published[key]
        if now<kickoff.astimezone(timezone.utc)+timedelta(hours=12):
            status.append(f'{label}: published {score[0]}:{score[1]}, waiting 12h after kickoff')
            continue
        token='|'.join(map(str,key))
        observed=next_cache.get(token)
        since=None
        if isinstance(observed,dict) and observed.get('score')==list(score):
            try: since=datetime.fromisoformat(observed['first_seen_utc'])
            except (ValueError, KeyError, TypeError): pass
        if since is None or since>now:
            if observe:
                next_cache[token]={'score':list(score),'first_seen_utc':now.isoformat()}
            status.append(f'{label}: first observation {score[0]}:{score[1]}; needs second check in 30m')
            continue
        if now-since<timedelta(minutes=30):
            status.append(f'{label}: unchanged {score[0]}:{score[1]}, waiting 30m')
            continue
        ready.append((fid,score[0],score[1],URL+' #Matchday5'))
        status.append(f'{label}: READY {score[0]}:{score[1]}')
    if commit and ready:
        with conn:
            for fid, hg, ag, source in ready:
                conn.execute('''INSERT INTO results
                    (fixture_id,home_goals,away_goals,result_source,recorded_utc)
                    VALUES(?,?,?,?,?)''',(fid,hg,ag,source,now.isoformat()))
    return next_cache, status, len(ready), len(published)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',action='store_true',help='Insert confirmed and mature results')
    parser.add_argument('--observe',action='store_true',help='Cache first confirmed observations only; no DB results')
    parser.add_argument('--db',type=Path,default=DB)
    parser.add_argument('--cache',type=Path,default=CACHE)
    args=parser.parse_args()
    if args.commit and args.observe:
        parser.error('choose either --observe or --commit')
    if not args.db.is_file():
        parser.error(f'Database missing: {args.db}')
    now=datetime.now(timezone.utc)
    raw=fetch()
    print('UEFA fetched:',len(raw),'bytes sha256:',hashlib.sha256(raw).hexdigest())
    with sqlite3.connect(f'file:{args.db}?mode=rw',uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        prior=load_observations(args.cache)
        updated, status, ready, published=process(conn,raw,prior,now,args.commit,args.observe)
    print('Published final-looking scores in round 5:',published)
    for line in status:print(line)
    if args.observe:
        save_observations(args.cache,updated)
        print('OBSERVED: only observation cache changed, no DB results')
    elif args.commit:
        print('COMMITTED:',ready,'new results; forecasts untouched')
    else:
        print('DRY RUN: 0 writes; ready:',ready)

if __name__=='__main__':
    main()
