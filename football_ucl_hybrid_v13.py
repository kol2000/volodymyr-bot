#!/usr/bin/env python3
"""V13 research-only UCL routing benchmark on immutable V12 prediction CSV.

Every hybrid has complete Elo fallback coverage. No training, network,
future outcomes in choosing a model, or writes to operational databases.
This does NOT join domestic histories; it is an honest full-coverage baseline
before attempting cross-league models.
"""
import argparse
import collections
import csv
import json
import math
from pathlib import Path

MODELS = ('Elo', 'Poisson12', 'V2', 'V3')
HYBRIDS = {
    'Elo': ('Elo', 0.0),
    'Poisson12_or_Elo': ('Poisson12', 1.0),
    'V2_or_Elo': ('V2', 1.0),
    'V3_or_Elo': ('V3', 1.0),
    'Half_Poisson_Elo': ('Poisson12', 0.5),
    'Half_V2_Elo': ('V2', 0.5),
    'Half_V3_Elo': ('V3', 0.5),
}
DEFAULT_INPUT = Path('state/football_expansion/mega_v11/reports_v12/predictions.csv')
DEFAULT_OUT = Path('state/football_expansion/mega_v11/reports_v13_hybrid')


def read_v12(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    if not rows: raise ValueError('No prediction rows')
    games = collections.defaultdict(dict)
    for row in rows:
        if row['tournament'] != 'UCL': continue
        key = (row['season'], row['date'], row['home'], row['away'])
        model = row['model']
        if model not in MODELS: continue
        if model in games[key]: raise ValueError(f'Duplicate model on match: {key} {model}')
        probs = tuple(float(row['p_'+label]) for label in ('home', 'draw', 'away'))
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in probs) or abs(sum(probs)-1)>0.00001:
            raise ValueError(f'Invalid probabilities: {key} {model}: {probs}')
        outcome = row['actual']
        if outcome not in ('H','D','A'): raise ValueError(f'Invalid outcome: {key}')
        games[key][model] = (probs, outcome)
    for key, data in games.items():
        if 'Elo' not in data: raise ValueError(f'No Elo fallback for: {key}')
        if len({v[1] for v in data.values()}) != 1: raise ValueError(f'Conflicting results: {key}')
    return games


def predict(game, preferred, weight):
    elo, outcome = game['Elo']
    if preferred == 'Elo' or preferred not in game:
        return elo, 'Elo'
    probs, _ = game[preferred]
    result = tuple(weight*p+(1-weight)*e for p,e in zip(probs,elo))
    return result, preferred


def score(probs, actual):
    y='HDA'.index(actual)
    accuracy=int(max(range(3), key=lambda i: probs[i])==y)
    ll=-math.log(max(1e-12,probs[y]))
    brier=sum((probs[i]-(i==y))**2 for i in range(3))
    return accuracy,ll,brier


def compute(games):
    details=[]
    for key in sorted(games):
        game=games[key]; season,date,home,away=key
        for label,(preferred,weight) in HYBRIDS.items():
            probs, used=predict(game,preferred,weight)
            a,ll,brier=score(probs,game['Elo'][1])
            details.append(dict(season=season,date=date,home=home,away=away,model=label,
                used=used,actual=game['Elo'][1],correct=a,logloss=ll,brier=brier,
                p_home=probs[0],p_draw=probs[1],p_away=probs[2]))
    return details


def summarize(details):
    groups=collections.defaultdict(list)
    for d in details:
        groups[(d['season'],d['model'])].append(d)
        groups[('ALL',d['model'])].append(d)
    output=[]
    for (season,model),rows in sorted(groups.items()):
        n=len(rows); used=sum(r['used']!='Elo' for r in rows)
        output.append(dict(season=season,model=model,matches=n,used_preferred=used,
            accuracy=sum(r['correct'] for r in rows)/n,
            logloss=sum(r['logloss'] for r in rows)/n,
            brier=sum(r['brier'] for r in rows)/n))
    return output


def csv_save(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,default=DEFAULT_INPUT)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUT)
    args=parser.parse_args()
    if args.output.resolve() == args.input.parent.resolve():
        raise SystemExit('Refusing to overwrite V12 report folder')
    games=read_v12(args.input)
    detail=compute(games); summary=summarize(detail)
    seasons=collections.Counter(k[0] for k in games)
    print('V13a — UCL hybrid benchmark (Elo fallback, same matches)')
    print('Unique games:',len(games),'seasons:',dict(sorted(seasons.items())))
    print('Important: no domestic league joining yet; this is a 100% coverage baseline.')
    print('Models use only existing V12 probabilities; no retraining or future-outcome routing.')
    print(f"{'Season':9} {'Model':22} {'N':>5} {'Special':>7} {'Accuracy':>9} {'LL':>9} {'Brier':>9}")
    for r in summary:
        print(f"{r['season']:9} {r['model']:22} {r['matches']:5d} {r['used_preferred']:7d} "
              f"{r['accuracy']:8.2%} {r['logloss']:9.5f} {r['brier']:9.5f}")
    csv_save(args.output/'hybrid_summary.csv',summary)
    csv_save(args.output/'hybrid_predictions.csv',detail)
    (args.output/'audit.json').write_text(json.dumps({
        'parent_v12_predictions':str(args.input),
        'games':len(games),'seasons':dict(seasons),
        'limitations':['Historical test, not true live','All hybrid predictions inherit V12 historical assumptions',
                       'Missing domestic-league integration','No parameter tuning or unseen test',
                       'Fallback is Elo; other models are only applied where they already produce a forecast']
    },ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved research output:',args.output)

if __name__=='__main__':main()
