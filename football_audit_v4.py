#!/usr/bin/env python3
"""Read-only paired audit of V1/V2/V3. No optimization or production changes.

Bootstraps by league rather than individual fixtures to preserve some within-league
structure; 5 league clusters makes the uncertainty estimate approximate only.
For more useful uncertainty, bootstrap *match dates* within seasons, stratified
by season, across all leagues. This remains exploratory, not a prospective test.
"""
import argparse
from collections import defaultdict
from datetime import date
import random
from pathlib import Path
from football_backtest import LEAGUES, SEASONS, loss, read_fixtures, OUTCOME
from football_compare_v3 import compare_three

PERIODS = [('2024/25', date(2024,7,1), date(2025,7,1)),
           ('2025/26', date(2025,7,1), date(2026,7,1))]


def score(p, actual):
    return loss(p, actual)[0], int(OUTCOME[max(range(3), key=lambda i:p[i])] == actual)


def delta(rows, a, b):
    """Mean improvements: logloss(b)-logloss(a), accuracy(a)-accuracy(b)."""
    n=len(rows)
    if not n: raise ValueError('Empty paired sample')
    return (sum(score(r[b],r[1])[0]-score(r[a],r[1])[0] for r in rows)/n,
            100*sum(score(r[a],r[1])[1]-score(r[b],r[1])[1] for r in rows)/n)


def bootstrap(rows, a, b, samples=2000, seed=20261122):
    """Stratified bootstrap over league subsets (observational approximation)."""
    rng=random.Random(seed)
    grouped=defaultdict(list)
    for r in rows:grouped[r[0]].append(r)
    chunks=list(grouped.values())
    estimates=[]
    for _ in range(samples):
        draw=[]
        for chunk in chunks:
            draw.extend(chunk[rng.randrange(len(chunk))] for _ in chunk)
        estimates.append(delta(draw,a,b)[0])
    estimates.sort()
    return estimates[int(.025*samples)],estimates[min(samples-1,int(.975*samples))]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-dir',type=Path,default=Path('state/football_backtest/data'))
    ap.add_argument('--samples',type=int,default=2000)
    args=ap.parse_args()
    if not 100 <= args.samples <= 10000: ap.error('--samples: 100..10000')
    fixtures, incomplete=read_fixtures(args.data_dir,list(LEAGUES),list(SEASONS))
    print('Неполные исходные записи:',dict(incomplete))
    combined=[]
    for label,start,end in PERIODS:
        rows, skipped, n=compare_three(fixtures,start,end)
        combined.extend(rows)
        print(f'\n{label}: сравнимых матчей {len(rows)} (V1={n}, пропуски V2/V3={sum(skipped.values())})')
        for name,a,b in [('V2 против V1',3,2),('V3 против V2',4,3),('V3 против V1',4,2)]:
            ll,accuracy=delta(rows,a,b)
            lo,hi=bootstrap(rows,a,b,args.samples)
            print(f'  {name:16} ΔLogLoss={ll:+.5f} (95% bootstrap [{lo:+.5f}, {hi:+.5f}]), ΔAccuracy={accuracy:+.2f} п.п.')
    print(f'\nСуммарно: {len(combined)} матчей')
    for name,a,b in [('V2 против V1',3,2),('V3 против V2',4,3),('V3 против V1',4,2)]:
        ll,accuracy=delta(combined,a,b)
        lo,hi=bootstrap(combined,a,b,args.samples)
        print(f'  {name:16} ΔLogLoss={ll:+.5f} (95% bootstrap [{lo:+.5f}, {hi:+.5f}]), ΔAccuracy={accuracy:+.2f} п.п.')
    print('\nВАЖНО: bootstrap предполагает независимость матчей внутри лиги и не доказывает будущую точность.')
    print('Оба сезона уже использовались при проектировании; выводы исследовательские.')

if __name__=='__main__': main()
