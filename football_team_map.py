#!/usr/bin/env python3
"""Audit explicit Football-Data -> OpenFootball names; never guess missing identities."""
import csv
import json
from pathlib import Path

ROOT = Path('state/football_backtest/data')
CURRENT = Path('state/football_live/source')
OUT = Path('state/football_live/team_mapping.json')
SEASONS = ('2023-24', '2024-25', '2025-26')

# Explicit candidates. Every non-null value MUST exist in historical files.
# null = no matching club in the three historic first-division seasons (or uncertain identity).
ALIASES = {
 'EPL': {
  'Arsenal':'Arsenal FC','Aston Villa':'Aston Villa FC','Bournemouth':'AFC Bournemouth',
  'Brentford':'Brentford FC','Brighton':'Brighton & Hove Albion FC','Chelsea':'Chelsea FC',
  'Coventry':None,'Crystal Palace':'Crystal Palace FC','Everton':'Everton FC',
  'Fulham':'Fulham FC','Hull':None,'Ipswich':'Ipswich Town FC','Leeds':'Leeds United FC',
  'Liverpool':'Liverpool FC','Man City':'Manchester City FC','Man United':'Manchester United FC',
  'Newcastle':'Newcastle United FC',"Nott'm Forest":'Nottingham Forest FC',
  'Sunderland':'Sunderland AFC','Tottenham':'Tottenham Hotspur FC',
 },
 'LaLiga': {
  'Alaves':'Deportivo Alavés','Ath Bilbao':'Athletic Club',
  'Ath Madrid':'Club Atlético de Madrid','Barcelona':'FC Barcelona',
  'Betis':'Real Betis Balompié','Celta':'RC Celta de Vigo','Elche':'Elche CF',
  'Espanol':'RCD Espanyol de Barcelona','Getafe':'Getafe CF',
  'La Coruna':None,'Levante':'Levante UD','Malaga':None,
  'Osasuna':'CA Osasuna','Real Madrid':'Real Madrid CF','Santander':None,
  'Sevilla':'Sevilla FC','Sociedad':'Real Sociedad de Fútbol',
  'Valencia':'Valencia CF','Vallecano':'Rayo Vallecano de Madrid',
  'Villarreal':'Villarreal CF',
 },
 'Bundesliga': {
  'Augsburg':'FC Augsburg','Bayern Munich':'FC Bayern München',
  'Dortmund':'Borussia Dortmund','Ein Frankfurt':'Eintracht Frankfurt',
  'Elversberg':None,'FC Koln':'1. FC Köln','Freiburg':'SC Freiburg',
  'Hamburg':'Hamburger SV','Hoffenheim':'TSG 1899 Hoffenheim',
  'Leverkusen':'Bayer 04 Leverkusen',"M'gladbach":'Borussia Mönchengladbach',
  'Mainz':'1. FSV Mainz 05','Paderborn':None,'RB Leipzig':'RB Leipzig',
  'Schalke 04':None,'Stuttgart':'VfB Stuttgart',
  'Union Berlin':'1. FC Union Berlin','Werder Bremen':'SV Werder Bremen',
 },
 'SerieA': {
  'Atalanta':'Atalanta BC','Bologna':'Bologna FC 1909',
  'Cagliari':'Cagliari Calcio','Como':'Como 1907',
  'Fiorentina':'ACF Fiorentina','Frosinone':'Frosinone Calcio',
  'Genoa':'Genoa CFC','Inter':'FC Internazionale Milano',
  'Juventus':'Juventus FC','Lazio':'SS Lazio','Lecce':'US Lecce',
  'Milan':'AC Milan','Monza':'AC Monza','Napoli':'SSC Napoli',
  'Parma':'Parma Calcio 1913','Roma':'AS Roma',
  'Sassuolo':'US Sassuolo Calcio','Torino':'Torino FC',
  'Udinese':'Udinese Calcio','Venezia':'Venezia FC',
 },
 'Ligue1': {
  'Angers':'Angers SCO','Auxerre':'AJ Auxerre',
  'Brest':'Stade Brestois 29','Le Havre':'Le Havre AC',
  'Le Mans':None,'Lens':'Racing Club de Lens','Lille':'Lille OSC',
  'Lorient':'FC Lorient','Lyon':'Olympique Lyonnais',
  'Marseille':'Olympique de Marseille','Monaco':'AS Monaco FC',
  'Nice':'OGC Nice','Paris FC':'Paris FC',
  'Paris SG':'Paris Saint-Germain FC','Rennes':'Stade Rennais FC 1901',
  'Strasbourg':'RC Strasbourg Alsace','Toulouse':'Toulouse FC',
  'Troyes':None,
 },
}


def hist_name(item):
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get('name') or item.get('title') or '').strip()
    return ''


def run():
    output = {}
    issues = 0
    for league, mapping in ALIASES.items():
        with (CURRENT / f'{league}_2026-27.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        teams = {name.strip() for row in rows for name in (row.get('HomeTeam'), row.get('AwayTeam')) if name}
        historical = set()
        for season in SEASONS:
            doc = json.loads((ROOT / f'{league}_{season}.json').read_text(encoding='utf-8-sig'))
            for game in doc['matches']:
                historical.update(filter(None, (hist_name(game.get('team1')),hist_name(game.get('team2')))))
        extra = set(mapping) - teams
        missing = teams - set(mapping)
        if extra or missing:
            print(f'ERROR {league}: unexpected aliases={sorted(extra)}; unmapped current teams={sorted(missing)}')
            issues += len(extra) + len(missing)
        valid = {}
        unresolved = []
        for team in sorted(teams):
            alias = mapping.get(team)
            if alias is None:
                unresolved.append(team)
                continue
            if alias not in historical:
                print(f'ERROR {league}: {team!r} -> {alias!r} is not present in historical records')
                issues += 1
                continue
            valid[team] = alias
        if len(valid.values()) != len(set(valid.values())):
            print(f'ERROR {league}: two Football-Data teams map to the same historical identity')
            issues += 1
        output[league] = {'verified': valid, 'no_top_division_history': unresolved}
        print(f'{league}: {len(teams)} current; {len(valid)} mapped; {len(unresolved)} without mapping')
        if unresolved:
            print('  Need current-season-only initialization:', ', '.join(unresolved))
    if issues:
        raise SystemExit(f'STOP: {issues} mapping problems; no file written. Confirm spellings against raw JSON.')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # This is a mapping audit only, not a prediction. Explicitly labelled historical-coverage status.
    OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'OK: saved {OUT}. No Telegram, live DB, or model was modified.')

if __name__ == '__main__':
    run()
