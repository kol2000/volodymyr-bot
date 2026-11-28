#!/usr/bin/env python3
"""V17: safe read-only UEFA future schedule versus existing UCL fixture snapshot.

Never guesses kickoff times, never stores forecasts or modifies SQLite.
"""
import argparse
import csv
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

URL = ('https://www.uefa.com/uefachampionsleague/news/'
       '02a8-2174c9e9019d-f909a77bd77a-1000--'
       '2026-27-champions-league-all-the-league-phase-fixtures/')
SNAPSHOT = Path('state/football_expansion/source/rpl_ucl_20261122T015615Z/UCL_fixtures_2627.csv')
# Exact dictionary ONLY: current CSV club name -> official UEFA page name.
NAMES = {
    'AEK Athens':'AEK Athens', 'Arsenal':'Arsenal', 'Aston Villa':'Aston Villa',
    'Atleti':'Atlético de Madrid', 'B. Dortmund':'Borussia Dortmund',
    'Barcelona':'Barcelona', 'Bayern München':'Bayern München',
    'Bodø/Glimt':'Bodø/Glimt', 'Club Brugge':'Club Brugge', 'Como':'Como',
    'Fenerbahçe':'Fenerbahçe', 'Feyenoord':'Feyenoord',
    'Galatasaray':'Galatasaray', 'Inter':'Inter', 'LASK':'LASK',
    'Leipzig':'Leipzig', 'Lens':'Lens', 'Lille':'Lille',
    'Liverpool':'Liverpool', 'Man City':'Manchester City',
    'Man Utd':'Manchester United', 'Napoli':'Napoli', 'Paris':'Paris Saint-Germain',
    'Porto':'Porto', 'PSV':'PSV Eindhoven', 'Real Betis':'Real Betis',
    'Real Madrid':'Real Madrid', 'Roma':'Roma',
    'S. Bratislava':'Slovan Bratislava', 'Sabah':'Sabah',
    'Shakhtar':'Shakhtar Donetsk', 'Slavia Praha':'Slavia Praha',
    'Sporting CP':'Sporting CP', 'Stuttgart':'Stuttgart',
    'Viking':'Viking', 'Villarreal':'Villarreal',
}
DATE_RE = re.compile(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+([0-9]{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20[0-9]{2})$')
ROUND_RE = re.compile(r'^Matchday\s+([1-8])$')
SCORE_RE = re.compile(r'\s+\d{1,2}\s*[-–:]\s*\d{1,2}\s+')

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.lines=[]
    def handle_data(self, data):
        s=' '.join(data.split())
        if s: self.lines.append(s)

def extract(lines):
    """Return article fixtures as (round,date,home,away), no time assumptions."""
    rounds=defaultdict(set); errors=[]
    current_round=None; current_day=None; saw={}
    # Skip early article references to round headings: only accept dated sections.
    for line in lines:
        rm=ROUND_RE.fullmatch(line)
        if rm:
            current_round=int(rm.group(1));current_day=None
            continue
        if current_round is None: continue
        dm=DATE_RE.fullmatch(line)
        if dm:
            raw=' '.join(dm.groups()[1:])
            d=datetime.strptime(raw,'%d %B %Y').date()
            if d.strftime('%A') != dm.group(1):
                errors.append(f'Date weekday inconsistent: {line}')
            current_day=d.isoformat()
            continue
        if current_day is None: continue
        if line.startswith('(') and line.endswith(')'): continue
        if SCORE_RE.search(line): continue
        if ' vs ' not in line: continue
        pair=line.split(' vs ')
        if len(pair)!=2: continue
        home,away=(x.strip() for x in pair)
        if not home or not away: continue
        item=(current_round,current_day,home,away)
        if item in rounds[current_round]: continue
        rounds[current_round].add(item)
    return rounds,errors

def load_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    if not rows or not {'gw','date','time','home','away'}.issubset(rows[0]):
        raise ValueError('Missing expected CSV columns')
    observed=set(); items=[]
    for r in rows:
        gw=int(r['gw'])
        d=date.fromisoformat(r['date'].strip()).isoformat()
        a,b=r['home'].strip(),r['away'].strip()
        if a not in NAMES or b not in NAMES:
            raise ValueError(f'Unknown club in snapshot: {a!r} / {b!r}')
        key=(gw,d,NAMES[a],NAMES[b])
        if key in observed: raise ValueError(f'Duplicate fixture in snapshot: {key}')
        observed.add(key)
        items.append((key,r['time'].strip()))
    return items

def audit(snapshot_items, uefa_rounds, today):
    official=set().union(*(uefa_rounds.get(i,set()) for i in range(5,9)))
    future=[(key,t) for key,t in snapshot_items if key[1]>=today and key[0]>=5]
    print('UEFA article future-round counts:',dict(sorted((k,len(v)) for k,v in uefa_rounds.items() if k>=5)))
    print('CSV future fixtures:',len(future))
    matches=[]; missing=[]; time_missing=[]
    for key, clock in future:
        if key not in official: missing.append(key)
        else:
            matches.append(key)
            if not clock: time_missing.append(key)
    print('EXACT MATCHES (date, round, clubs):',len(matches))
    print('SNAPSHOT NOT VERIFIED BY UEFA:',len(missing))
    for k in missing[:30]: print(' UNVERIFIED:',k)
    print('MATCHES WITHOUT CSV TIME:',len(time_missing))
    for k in time_missing[:15]: print(' NO TIME:',k)
    print('UEFA fixtures not in snapshot:',len([x for x in official if x[1]>=today and x not in {k for k,t in future}]))
    for x in sorted(x for x in official if x[1]>=today and x not in {k for k,t in future})[:30]:
        print(' UEFA ONLY:',x)
    print('\nNo kickoff is certified by this audit: official page times must be verified separately.')
    print('NO DATABASE WRITES. NO FORECASTS SAVED.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot',type=Path,default=SNAPSHOT)
    ap.add_argument('--html',type=Path,help='Offline HTML fixture for tests / reproducibility')
    args=ap.parse_args()
    if args.html:
        raw=args.html.read_bytes(); print('Offline HTML:',args.html)
    else:
        req=Request(URL,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html'})
        with urlopen(req,timeout=30) as res:
            if res.status!=200:raise RuntimeError(f'UEFA HTTP {res.status}')
            raw=res.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('UEFA page exceeds 2MB')
        print('UEFA page:',URL)
    print('UEFA sha256:',hashlib.sha256(raw).hexdigest())
    parser=Text();parser.feed(raw.decode('utf-8',errors='replace'))
    found,errors=extract(parser.lines)
    if errors:raise ValueError('; '.join(errors))
    if not any(found.get(i) for i in range(5,9)):
        raise ValueError('No future rounds found; page layout changed or content unavailable')
    audit(load_csv(args.snapshot),found,datetime.now(timezone.utc).date().isoformat())

if __name__=='__main__':main()
