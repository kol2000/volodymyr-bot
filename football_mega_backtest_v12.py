#!/usr/bin/env python3
"""Research-only, chronological RPL/UCL multi-model benchmark (V12 aliases).

Reads local 2026/27 snapshot; downloads historical archives only with --fetch.
No live forecasting, no changes to bot, V7/V8 DB, or any existing models.
All results for a given date enter history only after its predictions.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OUTCOMES = 'HDA'
SEASONS = ('2023-24','2024-25','2025-26')
URL_RPL = 'https://www.football-data.co.uk/new/RUS.csv'
URL_UCL = 'https://raw.githubusercontent.com/openfootball/champions-league/master/{}/cl.txt'
CACHE = Path('state/football_expansion/mega_v11')
DEFAULT_MAPPING = Path('state/football_expansion/ucl_team_mapping.json')
CURRENT = Path('state/football_expansion/source')

@dataclass(frozen=True)
class Match:
    day: dt.date
    league: str
    home: str
    away: str
    gh: int
    ga: int
    source: str

    @property
    def actual(self):
        return 0 if self.gh > self.ga else 1 if self.gh == self.ga else 2


def request_bytes(url):
    req = urllib.request.Request(url, headers={'User-Agent':'BubusResearchV11/1.0','Accept':'text/plain,text/csv,*/*'})
    with urllib.request.urlopen(req,timeout=35) as res:
        body = res.read(12_000_001)
        if len(body)>12_000_000: raise ValueError('Source exceeded 12 MB')
        if body.lstrip().lower().startswith((b'<!doctype html',b'<html')): raise ValueError('HTML instead of data')
    return body


def text_decode(raw):
    for encoding in ('utf-8-sig','cp1252'):
        try: return raw.decode(encoding)
        except UnicodeDecodeError: pass
    raise ValueError('Cannot decode file')


def ensure_archives(cache, fetch):
    cache.mkdir(parents=True,exist_ok=True)
    sources={'RPL_archive.csv':URL_RPL}
    for season in SEASONS:
        sources[f'UCL_{season}.txt']=URL_UCL.format(season)
    for filename,url in sources.items():
        dest=cache/filename
        if dest.is_file():
            print('CACHE',filename,'SHA256',hashlib.sha256(dest.read_bytes()).hexdigest()[:14],flush=True)
            continue
        if not fetch:
            print('MISSING',filename,'use --fetch',flush=True)
            continue
        print('DOWNLOAD',filename,url,flush=True)
        try:
            data=request_bytes(url)
            temp=dest.with_suffix(dest.suffix+'.part')
            temp.write_bytes(data)
            temp.replace(dest)
            print('SAVED',filename,len(data),'bytes',flush=True)
        except Exception as e:
            print('DOWNLOAD ERROR',filename,type(e).__name__,str(e)[:180],flush=True)
    return sources


def parse_date(x):
    x=(x or '').strip().split(' ')[0]
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d/%m/%y','%d.%m.%Y','%m/%d/%Y'):
        try:return dt.datetime.strptime(x,fmt).date()
        except ValueError: pass
    return None


def find_field(row,*names):
    index={str(k).strip().lower().replace(' ','').replace('_',''):v for k,v in row.items() if k is not None}
    for name in names:
        key=name.lower().replace(' ','').replace('_','')
        if key in index and index[key] is not None:return str(index[key]).strip()
    return ''


def parse_rus_csv(path):
    raw=path.read_bytes()
    text=text_decode(raw)
    if text.lstrip().startswith('<'):raise ValueError('RPL archive appears to be HTML')
    reader=csv.DictReader(io.StringIO(text))
    columns=reader.fieldnames or []
    print('RPL archive HEADERS',columns[:30],flush=True)
    if not {'date','home','away'} <= {str(x).strip().lower() for x in columns}:
        raise ValueError('Unexpected RPL CSV schema; see HEADERS and update parser')
    out=[];bad=collections.Counter();countries=collections.Counter();leagues=collections.Counter()
    for row in reader:
        if not any(row.values()):continue
        country=find_field(row,'Country'); league=find_field(row,'League','Div','Division')
        if country:countries[country]+=1
        if league:leagues[league]+=1
        if country and country.casefold() not in ('russia','rus','russian federation','россия'):
            bad['other country']+=1;continue
        # Russian file typically contains one first-division competition. Reject explicit lower divisions.
        if league and any(term in league.casefold() for term in ('fNL','second','2nd','first division','1. division','national league')):
            bad['lower division']+=1;continue
        day=parse_date(find_field(row,'Date','MatchDate'))
        home=find_field(row,'Home','HomeTeam','HTeam')
        away=find_field(row,'Away','AwayTeam','ATeam')
        h=find_field(row,'HG','FTHG','HomeGoals','ft_home','HomeScore')
        a=find_field(row,'AG','FTAG','AwayGoals','ft_away','AwayScore')
        if day is None or not home or not away or home==away or not h.isdigit() or not a.isdigit():
            bad['invalid/incomplete']+=1;continue
        if day > dt.date(2026,7,1) or day < dt.date(2022,7,1):
            bad['outside archive test range']+=1;continue
        if max(int(h),int(a))>30:bad['implausible score']+=1;continue
        out.append(Match(day,'RPL',home,away,int(h),int(a),str(path)))
    print('RPL archive rows:',len(out),'skips',dict(bad),'countries',dict(countries),'leagues',dict(leagues),flush=True)
    return out


# OpenFootball cl.txt: scores appear after club name and ISO country code.
GAME_RE=re.compile(r'^\s*(?:\d{1,2}:\d\d\s+)?(.+?)\s+\(([A-Z]{3})\)\s+v\s+(.+?)\s+\(([A-Z]{3})\)\s+(\d{1,2})-(\d{1,2})(?=\s|$)')
DATE_RE=re.compile(r'^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Za-z]{3})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$')

def parse_ucl_txt(path,season):
    text=text_decode(path.read_bytes())
    current=None;year=int(season[:4]);rows=[];skipped=[];headers=0
    for i,line in enumerate(text.splitlines(),1):
        d=DATE_RE.match(line)
        if d:
            month=dt.datetime.strptime(d.group(1),'%b').month
            year=int(d.group(3)) if d.group(3) else (int(season[:4])+1 if month <= 7 else int(season[:4]))
            current=dt.date(year,month,int(d.group(2)));headers+=1
            continue
        m=GAME_RE.match(line)
        if m:
            if current is None:raise ValueError(f'Found game before date {path}:{i}')
            home,_,away,_,hg,ag=m.groups()
            home=home.strip();away=away.strip()
            rows.append(Match(current,'UCL',home,away,int(hg),int(ag),f'{path}:{i}'))
        elif re.search(r'\s+v\s+',line) and re.search(r'\d+-\d+',line):
            skipped.append((i,line.strip()[:135]))
    print('UCL',season,'parsed games',len(rows),'date headings',headers,'unparsed candidate game lines',len(skipped),flush=True)
    for x in skipped[:8]:print('  UNPARSED',x,flush=True)
    return rows,skipped


def current_snapshot(base):
    options=sorted(p for p in base.glob('rpl_ucl_*') if p.is_dir() and (p/'manifest.json').exists())
    if not options:raise FileNotFoundError('No rpl_ucl_* snapshot with manifest.json')
    return options[-1]


def parse_current(path,league):
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    out=[];errors=[]
    for i,r in enumerate(rows,2):
        try:
            day=dt.date.fromisoformat(r['date'])
            h,a=r['home'].strip(),r['away'].strip()
            gh,ga=int(r['ft_home']),int(r['ft_away'])
            if not h or not a or h==a or min(gh,ga)<0:raise ValueError('bad game')
            out.append(Match(day,league,h,a,gh,ga,f'{path}:{i}'))
        except (ValueError,KeyError,TypeError) as e:errors.append((i,str(e)))
    if errors:raise ValueError(f'{league} current invalid rows: {errors[:4]}')
    print(league,'current results:',len(out),flush=True)
    return out


def canonicalize_current_ucl(current, history, mapping_path):
    """Resolve only *current* UCL team aliases; no guessing or fuzzy matches.

    None signifies no confirmed UCL history and retains the current name.
    Verify every non-null target exists in earlier imported seasons.
    """
    mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
    if not isinstance(mapping,dict):
        raise ValueError('UCL team mapping must be a JSON object')
    old_teams={t for m in history for t in (m.home,m.away)}
    now_teams={t for m in current for t in (m.home,m.away)}
    missing=sorted(now_teams-set(mapping))
    if missing:
        raise ValueError('UCL aliases missing for: '+', '.join(missing))
    bad=[]
    resolved={}
    for name in sorted(now_teams):
        target=mapping[name]
        if target is None:
            resolved[name]=name
        elif not isinstance(target,str) or not target.strip() or target not in old_teams:
            bad.append(f'{name!r} -> {target!r}')
        else:
            resolved[name]=target
    if bad:
        raise ValueError('Unverified UCL historical names: '+', '.join(bad))
    # No two current clubs may be resolved to the same canonical club.
    reverse=collections.defaultdict(list)
    for source,target in resolved.items():reverse[target].append(source)
    collisions={k:v for k,v in reverse.items() if len(v)>1}
    if collisions:
        raise ValueError('Ambiguous UCL club aliases: '+str(collisions))
    out=[Match(m.day,m.league,resolved[m.home],resolved[m.away],m.gh,m.ga,m.source)
         for m in current]
    print('UCL ALIAS AUDIT: current teams',len(now_teams),
          'historical aliases',sum(mapping[t] is not None for t in now_teams),
          'no confirmed history',sum(mapping[t] is None for t in now_teams),flush=True)
    print('UCL ALIASES: mapped current matches',len(out),flush=True)
    return out


def clean_matches(matches):
    found={};out=[];conflicts=[]
    for m in sorted(matches,key=lambda x:(x.day,x.home,x.away)):
        key=(m.league,m.day,m.home,m.away)
        if key in found:
            if (m.gh,m.ga)!=(found[key].gh,found[key].ga): conflicts.append((found[key],m))
            continue
        found[key]=m;out.append(m)
    if conflicts:
        for x,y in conflicts[:5]: print('CONFLICT:',x,y,flush=True)
        raise ValueError(f'{len(conflicts)} conflicting results in history; refusing to score')
    return out


def normalize_team(s):
    import unicodedata
    s=''.join(c for c in unicodedata.normalize('NFKD',s.casefold()) if not unicodedata.combining(c))
    s=re.sub(r'[^a-z0-9]+',' ',s)
    # conservative punctuation normalisation only; DO NOT fuzzy-merge clubs
    return ' '.join(s.split())


def metrics(p,y):
    return (-math.log(max(1e-12,p[y])),sum((p[i]-(i==y))**2 for i in range(3)),int(max(range(3),key=lambda i:p[i])==y))


def poisson1(history,match):
    prior=[m for m in history if (match.day-m.day).days<=365]
    home=[m for m in prior if match.home in (m.home,m.away)][-12:]
    away=[m for m in prior if match.away in (m.home,m.away)][-12:]
    if len(home)<5 or len(away)<5:return None
    def rate(team,items):
        v=[]
        for m in items:
            v.append((m.gh,m.ga) if m.home==team else (m.ga,m.gh))
        return (sum(a for a,b in v)/len(v),sum(b for a,b in v)/len(v))
    h=rate(match.home,home);a=rate(match.away,away)
    return poisson_probs(max(.15,min(4.5,(h[0]+a[1])/2*1.10)),max(.15,min(4.5,(a[0]+h[1])/2*.90)))


def poisson_probs(lh,la):
    ph=[math.exp(-lh)*lh**i/math.factorial(i) for i in range(12)]
    pa=[math.exp(-la)*la**i/math.factorial(i) for i in range(12)]
    out=[0.,0.,0.]
    for i,x in enumerate(ph):
        for j,y in enumerate(pa):out[0 if i>j else 1 if i==j else 2]+=x*y
    return tuple(x/sum(out) for x in out)


def elo_expected(h,a,adv=65):
    return 1/(1+10**((a-h-adv)/400))


def elo_probs(h,a):
    e=elo_expected(h,a)
    draw=max(.12,min(.31,.27*math.exp(-abs(h+65-a)/650)))
    return (e*(1-draw),draw,(1-e)*(1-draw))


def season_bucket(day):
    return f'{day.year if day.month>=7 else day.year-1}/{(day.year+1 if day.month>=7 else day.year)%100:02d}'


def run_experiment(games,division,first_day,last_day):
    history=[];ratings=collections.defaultdict(lambda:1500.0)
    by_day=collections.defaultdict(list)
    for m in games: by_day[m.day].append(m)
    output=[];counter=collections.Counter()
    for day in sorted(by_day):
        batch=by_day[day]
        for m in batch:
            if not(first_day<=m.day<=last_day):continue
            ratingh,ratinga=ratings[m.home],ratings[m.away]
            p_elo=elo_probs(ratingh,ratinga)
            p_simple=poisson1(history,m)
            # V2 mirrors the existing V2 for each competition; it requires sufficient
            # same-competition history and refuses to guess on very sparse teams.
            p_v2=None
            if len(history)>=90:
                try:
                    from football_backtest import Fixture
                    from football_compare_v2 import candidate_prediction
                    prior=[Fixture(x.day,division,x.home,x.away,(x.gh,x.ga)) for x in history]
                    p_v2=candidate_prediction(m.home,m.away,prior,division,day)
                except Exception as exc:
                    raise RuntimeError(f'V2 failed for {division} {day} {m.home} {m.away}: {exc}') from exc
            p_v3=None
            if p_v2 is not None:
                decisive=1-p_v2[1]
                e=elo_expected(ratingh,ratinga)
                p_v3=tuple(.75*p_v2[i]+.25*x for i,x in enumerate((decisive*e,p_v2[1],decisive*(1-e))))
            options={'Elo':p_elo,'Poisson12':p_simple,'V2':p_v2,'V3':p_v3}
            y=m.actual
            for model,p in options.items():
                if p is None:
                    counter[model+'_skipped']+=1
                    continue
                ll,brier,correct=metrics(p,y)
                output.append(dict(tournament=division,season=season_bucket(m.day),date=str(m.day),
                                   home=m.home,away=m.away,goals=f'{m.gh}:{m.ga}',model=model,
                                   actual=OUTCOMES[y],predicted=OUTCOMES[max(range(3),key=lambda i:p[i])],
                                   p_home=f'{p[0]:.8f}',p_draw=f'{p[1]:.8f}',p_away=f'{p[2]:.8f}',
                                   logloss=f'{ll:.8f}',brier=f'{brier:.8f}',correct=correct))
        # Same-date matches are added only AFTER all predictions on this date.
        changes=collections.defaultdict(float)
        for m in batch:
            rh,ra=ratings[m.home],ratings[m.away]
            points=(1.,.5,0.)[m.actual]
            delta=20*(points-elo_expected(rh,ra))
            changes[m.home]+=delta;changes[m.away]-=delta
        for team,change in changes.items():ratings[team]+=change
        history.extend(batch)
    print(division,'evaluated dates',first_day,'to',last_day,'rows',len(output),'coverage skips',dict(counter),flush=True)
    return output


def summarize(rows):
    print('\nSUMMARY by tournament / season / model (unpaired; compare paired below)',flush=True)
    groups=collections.defaultdict(list)
    for r in rows:
        groups[(r['tournament'],r['season'],r['model'])].append(r)
        groups[(r['tournament'],'ALL',r['model'])].append(r)
    report=[]
    for (league,season,model),rr in sorted(groups.items()):
        n=len(rr)
        vals=(sum(int(x['correct']) for x in rr)/n,sum(float(x['logloss']) for x in rr)/n,sum(float(x['brier']) for x in rr)/n)
        report.append(dict(tournament=league,season=season,model=model,matches=n,accuracy=round(vals[0],4),logloss=round(vals[1],4),brier=round(vals[2],4)))
        print(f'{league:5} {season:8} {model:10} n={n:4d} accuracy={vals[0]:.1%} LL={vals[1]:.4f} Brier={vals[2]:.4f}',flush=True)
    return report


def paired_summary(rows):
    group=collections.defaultdict(dict)
    for r in rows:
        key=(r['tournament'],r['date'],r['home'],r['away'])
        group[key][r['model']]=r
    pairs=[('V3','V2'),('V3','Elo'),('V3','Poisson12'),('V2','Elo'),('Poisson12','Elo')]
    output=[]
    print('\nPAIRED comparison: positive ΔLL = first model better',flush=True)
    for tournament in ('RPL','UCL'):
        for a,b in pairs:
            rr=[(v[a],v[b]) for key,v in group.items() if key[0]==tournament and a in v and b in v]
            if not rr:continue
            n=len(rr);ll=sum(float(y['logloss'])-float(x['logloss']) for x,y in rr)/n
            acc=sum(int(x['correct'])-int(y['correct']) for x,y in rr)/n
            print(f'{tournament} {a:9} vs {b:9} n={n:4d} ΔLL={ll:+.4f} ΔAccuracy={acc:+.1%}',flush=True)
            output.append(dict(tournament=tournament,first=a,second=b,matches=n,delta_logloss=round(ll,5),delta_accuracy=round(acc,5)))
    return output


def write_csv(path,rows):
    if not rows:return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=list(rows[0]));wr.writeheader();wr.writerows(rows)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--fetch',action='store_true',help='Fetch RPL/UCL archive to separate cache, once')
    p.add_argument('--cache',type=Path,default=CACHE)
    p.add_argument('--snapshot',type=Path,default=None)
    p.add_argument('--output',type=Path,default=None)
    p.add_argument('--no-current',action='store_true')
    p.add_argument('--ucl-mapping',type=Path,default=DEFAULT_MAPPING)
    args=p.parse_args()
    ensure_archives(args.cache,args.fetch)
    snapshot=args.snapshot or current_snapshot(CURRENT)
    out=args.output or (args.cache/'reports_v12')
    print('SNAPSHOT:',snapshot,'OUTPUT:',out,flush=True)
    games=collections.defaultdict(list)
    rpl=args.cache/'RPL_archive.csv'
    if rpl.exists():
        try:games['RPL']+=parse_rus_csv(rpl)
        except Exception as e:print('RPL HISTORY ERROR:',type(e).__name__,str(e),flush=True)
    else:print('WARNING: No RPL history',flush=True)
    for season in SEASONS:
        file=args.cache/f'UCL_{season}.txt'
        if not file.exists():continue
        try:
            rr,skipped=parse_ucl_txt(file,season)
            expected=125 if season=='2023-24' else 189
            if len(rr)!=expected:
                print(f'WARNING UCL {season}: {len(rr)} parsed vs {expected} declared, check skipped rows',flush=True)
            # Keep parsed UCL matches only when complete to avoid false evaluation claims.
            if len(rr)<expected:
                print('SKIP incomplete UCL season',season,flush=True)
                continue
            games['UCL']+=rr
        except Exception as e:print('UCL HISTORY ERROR',season,type(e).__name__,str(e),flush=True)
    if not args.no_current:
        # Fail closed if current UCL clubs cannot be safely joined to the old database.
        current_ucl=parse_current(snapshot/'UCL_results_2627.csv','UCL')
        games['UCL']+=canonicalize_current_ucl(current_ucl,games['UCL'],args.ucl_mapping)
        games['RPL']+=parse_current(snapshot/'RPL_results_2627.csv','RPL')
    rows=[]
    for comp in ('RPL','UCL'):
        found=clean_matches(games[comp])
        years=collections.Counter(season_bucket(m.day) for m in found)
        clubs=len({t for m in found for t in (m.home,m.away)})
        print('\nDATA AUDIT:',comp,'matches',len(found),'teams',clubs,'seasons',dict(sorted(years.items())),flush=True)
        if comp=='RPL' and len(found)<100:
            print('RPL dataset too small, skipping model comparison',flush=True);continue
        if comp=='UCL' and len(found)<120:
            print('UCL dataset too small, skipping model comparison',flush=True);continue
        # Test 2024/25 onward, 2023/24 warm-up; RPL archive may have earlier years as well.
        rows.extend(run_experiment(found,comp,dt.date(2024,7,1),dt.date(2026,11,22)))
    if not rows: print('NO EVALUATION: archive parser/coverage must be fixed',flush=True);return 2
    report=summarize(rows)
    paired=paired_summary(rows)
    write_csv(out/'predictions.csv',rows)
    write_csv(out/'summary.csv',report)
    write_csv(out/'paired.csv',paired)
    (out/'audit.json').write_text(json.dumps({'snapshot':str(snapshot),'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'evaluated_rows':len(rows),'ucl_mapping':str(args.ucl_mapping) if not args.no_current else None, 'ucl_mapping_sha256':hashlib.sha256(args.ucl_mapping.read_bytes()).hexdigest() if not args.no_current else None,'source_files':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [*args.cache.glob('RPL_archive.csv'),*args.cache.glob('UCL_*.txt'),*snapshot.glob('*results*.csv')]},
        'limitations':['Research only; no true unseen live evaluation','Only previous-day results used; source file availability times not reconstructible','No bookmaker baseline','UCL Elo warm-start from UCL only; weak for cross-league entrants','V12: explicit current-UCL club aliases only; unmatched clubs remain cold-start','RPL archive data must be audited for missing records and team aliases', 'UCL aliases validated against old UCL clubs; unavailable time-of-publication limits historical claims']},ensure_ascii=False,indent=2),encoding='utf-8')
    print('\nCOMPLETE. CSV reports:',out,flush=True)
    print('NO live bot, V7/V8, Telegram or existing SQLite changed.',flush=True)
    return 0

if __name__=='__main__':sys.exit(main())
