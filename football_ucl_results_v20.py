#!/usr/bin/env python3
"""V20: dual-source UCL Matchday 5 results verification (UEFA article + ESPN final status).

Read-only by default. Commit requires all independent conditions to pass:
V16's 30-minute-stable UEFA observation, >=12h after kickoff, *fresh* UEFA
cache, and ESPN event explicitly marked completed/full-time with matching
teams, kickoff and score. Writes only V14 results; no changes to forecasts.

ESPN endpoint is undocumented; missing data / unknown aliases FAIL CLOSED.
Requires Python stdlib and football_ucl_results_v16.py.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import football_ucl_results_v16 as v16

BASE = Path('state/football_forward')
HTML = BASE / 'uefa_latest.html'
OBSERVATIONS = BASE / 'uefa_observations_v16.json'
DB = BASE / 'forward_v14.sqlite3'
ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard'
DATES = ('20261124', '20261125')

# UEFA display name -> explicitly accepted ESPN names (no fuzzy search).
# Initial candidates are not evidence of feed coverage. --probe shows the actual names.
ALIASES = {
 'Bodø/Glimt': ['Bodø/Glimt','Bodo/Glimt','Bodo Glimt','FK Bodo/Glimt'],
 'LASK': ['LASK','LASK Linz'],
 'Galatasaray': ['Galatasaray','Galatasaray SK'],
 'Aston Villa': ['Aston Villa','Aston Villa FC'],
 'Arsenal': ['Arsenal','Arsenal FC'],
 'Borussia Dortmund': ['Borussia Dortmund','Borussia Dortmund (BVB)','Dortmund'],
 'Como': ['Como','Como 1907'],
 'AEK Athens': ['AEK Athens','AEK Athens FC'],
 'Feyenoord': ['Feyenoord','Feyenoord Rotterdam'],
 'Porto': ['FC Porto','Porto'],
 'Manchester City': ['Manchester City','Manchester City FC'],
 'Napoli': ['Napoli','SSC Napoli'],
 'Leipzig': ['RB Leipzig','Leipzig'],
 'Lens': ['Lens','RC Lens'],
 'Real Madrid': ['Real Madrid','Real Madrid CF'],
 'PSV Eindhoven': ['PSV Eindhoven','PSV'],
 'Slovan Bratislava': ['Slovan Bratislava','SK Slovan Bratislava'],
 'Real Betis': ['Real Betis','Real Betis Balompié'],
 'Slavia Praha': ['Slavia Prague','Slavia Praha','SK Slavia Praha'],
 'Villarreal': ['Villarreal','Villarreal CF'],
 'Sabah': ['Sabah','Sabah FK'],
 'Barcelona': ['Barcelona','FC Barcelona'],
 'Atlético de Madrid': ['Atletico Madrid','Atlético Madrid','Atlético de Madrid'],
 'Viking': ['Viking','Viking FK'],
 'Club Brugge': ['Club Brugge','Club Brugge KV'],
 'Liverpool': ['Liverpool','Liverpool FC'],
 'Inter': ['Internazionale','Inter Milan','Inter','Internazionale Milano'],
 'Stuttgart': ['VfB Stuttgart','Stuttgart'],
 'Shakhtar Donetsk': ['Shakhtar Donetsk','FC Shakhtar Donetsk'],
 'Fenerbahçe': ['Fenerbahce','Fenerbahçe'],
 'Lille': ['Lille','Lille OSC'],
 'Bayern München': ['Bayern Munich','Bayern München','FC Bayern Munich'],
 'Paris Saint-Germain': ['Paris Saint-Germain','Paris Saint-Germain FC','PSG'],
 'Roma': ['AS Roma','Roma'],
 'Sporting CP': ['Sporting CP','Sporting Lisbon','Sporting Clube de Portugal'],
 'Manchester United': ['Manchester United','Manchester United FC','Man United'],
}


def alias_index(aliases=ALIASES):
    expected={x for a,b,_,_ in v16.PAIRS for x in (a,b)}
    if set(aliases)!=expected:
        raise ValueError('ESPN alias list does not exactly cover the 36 UEFA clubs')
    found={}
    for official, names in aliases.items():
        for name in [official,*names]:
            key=name.strip().casefold()
            if not key or (key in found and found[key]!=official):
                raise ValueError('Ambiguous ESPN name alias '+repr(name))
            found[key]=official
    return found


def fetch_espn(day):
    if day not in DATES: raise ValueError('Unexpected ESPN request date')
    url=ESPN_BASE+'?'+urlencode({'dates': day, 'limit':'100'})
    req=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    with urlopen(req,timeout=25) as response:
        if response.status!=200: raise ValueError('ESPN HTTP '+str(response.status))
        data=response.read(5_000_001)
        if len(data)>5_000_000: raise ValueError('ESPN response exceeds 5MB')
    doc=json.loads(data)
    if not isinstance(doc,dict) or not isinstance(doc.get('events'),list):
        raise ValueError('ESPN scoreboard missing events array')
    return doc,hashlib.sha256(data).hexdigest()


def load_espn(path=None):
    if path:
        doc=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(doc,dict) or not isinstance(doc.get('events'),list):
            raise ValueError('Offline ESPN JSON needs {"events": [...]}')
        return [doc],['offline']
    docs=[]; hashes=[]
    for day in DATES:
        doc,digest=fetch_espn(day)
        docs.append(doc);hashes.append(digest)
    return docs,hashes


def describe_event(event):
    teams=[]
    for c in event.get('competitions',[{}])[0].get('competitors',[]):
        t=c.get('team') or {}
        teams.append(f"{c.get('homeAway','?')}={t.get('displayName','?')}({c.get('score','?')})")
    status=(event.get('status') or {}).get('type') or {}
    return (f"{event.get('date','?')} {event.get('id','?')} "
            f"{status.get('name','?')}/{status.get('state','?')}/completed={status.get('completed','?')} "
            + ' '.join(teams))


def parse_score(value):
    if isinstance(value,bool): return None
    if isinstance(value,int) and 0<=value<=30: return value
    if isinstance(value,str) and re.fullmatch(r'\d{1,2}',value.strip()):
        val=int(value.strip());return val if val<=30 else None
    return None


def parse_espn(docs, aliases=ALIASES):
    """Return exact (home,away) -> (kickoff,score,event_id). Only final games.

    Non-final and unknown-name events ignored; actual provider coverage exposed by --probe.
    """
    idx=alias_index(aliases)
    out={}; seen_ids=set()
    for doc in docs:
        for event in doc.get('events',[]):
            eid=str(event.get('id',''))
            if not eid: continue
            if eid in seen_ids: continue
            seen_ids.add(eid)
            typ=(event.get('status') or {}).get('type') or {}
            if not (typ.get('completed') is True and typ.get('state')=='post'
                    and typ.get('name')=='STATUS_FULL_TIME'):
                continue
            competitions=event.get('competitions')
            if not isinstance(competitions,list) or len(competitions)!=1: continue
            comp=competitions[0]
            members=comp.get('competitors')
            if not isinstance(members,list) or len(members)!=2: continue
            by_side={}
            for c in members:
                side=c.get('homeAway')
                names=(c.get('team') or {})
                official=idx.get(str(names.get('displayName','')).strip().casefold())
                score=parse_score(c.get('score'))
                if side not in ('home','away') or side in by_side or not official or score is None:
                    break
                by_side[side]=(official,score)
            if set(by_side)!={'home','away'}: continue
            home,gh=by_side['home'];away,ga=by_side['away']
            if home==away: raise ValueError('ESPN alias collision in '+eid)
            kickoff=datetime.fromisoformat(str(event.get('date','')).replace('Z','+00:00'))
            if kickoff.tzinfo is None: raise ValueError('ESPN has naive kickoff '+eid)
            key=(home,away)
            if key in out: raise ValueError('Duplicate final ESPN game for '+repr(key))
            out[key]=(kickoff.astimezone(timezone.utc),(gh,ga),eid)
    return out


def get_uefa_raw(path,now,max_age=timedelta(hours=2)):
    stat=path.stat()
    age=now-datetime.fromtimestamp(stat.st_mtime,timezone.utc)
    if age < -timedelta(minutes=5) or age>max_age:
        raise ValueError(f'UEFA cache stale/future ({age}); --commit blocked')
    raw=path.read_bytes()
    if not 10000<len(raw)<2_000_000: raise ValueError('UEFA cache unreasonable size')
    return raw,hashlib.sha256(raw).hexdigest()


def check(conn,raw,observations,espn,now):
    published=v16.parse_page(raw)
    # Do not use the duplicated date cross-product to choose scores: DB date is authoritative.
    rows=conn.execute('''SELECT f.id,f.kickoff_utc,f.home,f.away,r.fixture_id
      FROM fixtures f LEFT JOIN results r ON r.fixture_id=f.id
      WHERE f.competition='UCL' AND substr(f.kickoff_utc,1,10) IN ('2026-11-24','2026-11-25')
      ORDER BY f.id''').fetchall()
    if len(rows)!=18 or {(r[2],r[3]) for r in rows}!={(h,a) for _,_,h,a in v16.PAIRS}:
        raise ValueError('Pilot round 5 V14 fixture list differs; refusing to process')
    ready=[]; messages=[]
    rev={(h,a):(uh,ua) for uh,ua,h,a in v16.PAIRS}
    for fid,when,home,away,existing in rows:
        label=f'#{fid} {home} - {away}'
        if existing is not None:
            messages.append(label+' ALREADY RECORDED');continue
        kickoff=datetime.fromisoformat(when.replace('Z','+00:00'))
        if kickoff.tzinfo is None:raise ValueError('Naive V14 kickoff')
        kickoff=kickoff.astimezone(timezone.utc)
        date=kickoff.date().isoformat()
        score=published.get((date,home,away))
        if score is None:
            messages.append(label+' WAIT UEFA score');continue
        if now<kickoff+timedelta(hours=12):
            messages.append(label+' WAIT >=12h after kickoff');continue
        token='|'.join((date,home,away))
        observation=observations.get(token)
        if not isinstance(observation,dict) or observation.get('score')!=list(score):
            messages.append(label+' WAIT V16 stable observation');continue
        try:
            since=datetime.fromisoformat(observation['first_seen_utc'])
            if since.tzinfo is None or since>now:raise ValueError('Bad observation time')
        except (TypeError,KeyError,ValueError):
            messages.append(label+' WAIT valid observation time');continue
        if now-since<timedelta(minutes=30):
            messages.append(label+' WAIT 30min observation');continue
        official=rev[(home,away)]
        espn_result=espn.get(official)
        if espn_result is None:
            messages.append(label+' WAIT ESPN final + exact team mapping');continue
        espn_time,espn_score,eid=espn_result
        if abs((espn_time-kickoff).total_seconds())>30*60:
            messages.append(label+' BLOCK ESPN kickoff mismatch');continue
        if espn_score!=score:
            messages.append(f'{label} BLOCK score mismatch UEFA={score}, ESPN={espn_score}');continue
        ready.append((fid,*score,eid))
        messages.append(f'{label} READY {score[0]}:{score[1]} ESPN id={eid}')
    return ready,messages


# Constant for the fixed pilot only. Additional rounds require a new observer.
DATES_ISO=('2026-11-24','2026-11-25')


def commit_results(db,ready,now,html_hash,espn_hashes):
    if not ready:return 0
    conn=sqlite3.connect(f'file:{db.resolve()}?mode=rw',uri=True)
    conn.execute('PRAGMA foreign_keys=ON'); conn.execute('PRAGMA busy_timeout=10000')
    try:
        with conn:
            for fid,hg,ag,eid in ready:
                if conn.execute('SELECT 1 FROM results WHERE fixture_id=?',(fid,)).fetchone():
                    continue
                source=(f'UEFA {v16.URL} sha256={html_hash}; '
                        f'ESPN {ESPN_BASE} event_id={eid} scoreboard_sha256={",".join(espn_hashes)}; '
                        'V16 stable observation >=30min; ESPN STATUS_FULL_TIME')
                conn.execute('''INSERT INTO results(fixture_id,home_goals,away_goals,result_source,recorded_utc)
                    VALUES(?,?,?,?,?)''',(fid,hg,ag,source,now.isoformat(timespec='microseconds')))
    finally:conn.close()
    return len(ready)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--probe',action='store_true',help='Show ESPN fixture names/status, no database changes')
    ap.add_argument('--commit',action='store_true',help='Register independently confirmed finals in immutable V14')
    ap.add_argument('--espn-json',type=Path,help='Offline ESPN JSON fixture for inspection/testing, never allowed with --commit')
    ap.add_argument('--html',type=Path,default=HTML)
    ap.add_argument('--observations',type=Path,default=OBSERVATIONS)
    ap.add_argument('--db',type=Path,default=DB)
    args=ap.parse_args()
    if args.commit and (args.probe or args.espn_json):
        ap.error('--commit requires live ESPN and cannot combine with --probe / --espn-json')
    now=datetime.now(timezone.utc)
    docs,hashes=load_espn(args.espn_json)
    print('ESPN scoreboard hashes:',', '.join(hashes))
    if args.probe:
        for doc in docs:
            print('ESPN events:',len(doc['events']))
            for event in doc['events']:
                print(' ',describe_event(event))
        print('READ ONLY. Review names and ESPN coverage before enabling any commit.')
        return
    espn=parse_espn(docs)
    print('ESPN exact-mapped FULL_TIME games:',len(espn))
    if not args.db.is_file():raise FileNotFoundError(args.db)
    # Dry runs may use older locally cached UEFA pages. Commit requires freshness.
    if args.commit:
        raw,html_hash=get_uefa_raw(args.html,now)
    else:
        raw=args.html.read_bytes();html_hash=hashlib.sha256(raw).hexdigest()
    obs=v16.load_observations(args.observations)
    conn=sqlite3.connect(f'file:{args.db.resolve()}?mode=ro',uri=True)
    try: ready,messages=check(conn,raw,obs,espn,now)
    finally: conn.close()
    for line in messages:print(line)
    print('READY:',len(ready))
    if args.commit:
        count=commit_results(args.db,ready,now,html_hash,hashes)
        print('COMMITTED:',count,'results; fixtures and predictions untouched')
    else:
        print('DRY RUN: 0 SQLite writes')


if __name__=='__main__':main()
