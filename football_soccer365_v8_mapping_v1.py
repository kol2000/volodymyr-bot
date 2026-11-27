#!/usr/bin/env python3
"""Explicit Soccer365 Russian names -> V8 European calendar names.

Research-only identity bridge.

No fuzzy matching.
No automatic translation.
No database writes.
No Telegram messages.
"""

from collections import Counter
from datetime import date, timedelta

import soccer365_football as soccer
import football_europe_team_bridge_v1 as bridge_module
import football_europe_adapter_v4 as adapter
import football_europe_ligue1_monitor_v2 as ligue
import football_forecast_research_v8 as v8


LEAGUES = {
    "Чемпионат Англии": "EPL",
    "Чемпионат Испании": "LaLiga",
    "Чемпионат Италии": "SerieA",
    "Чемпионат Германии": "Bundesliga",
    "Чемпионат Франции": "Ligue1",
}


RAW_MAPPING = {
    "EPL": """
Арсенал|Arsenal
Астон Вилла|Aston Villa
Борнмут|AFC Bournemouth
Брайтон|Brighton & Hove Albion
Брентфорд|Brentford
Ипсвич Таун|Ipswich Town
Ковентри Сити|Coventry City
Кристал Пэлас|Crystal Palace
Ливерпуль|Liverpool
Лидс Юнайтед|Leeds United
Манчестер Сити|Manchester City
Манчестер Юнайтед|Manchester United
Ноттингем Форест|Nottingham Forest
Ньюкасл Юнайтед|Newcastle United
Сандерленд|Sunderland
Тоттенхэм Хотспур|Tottenham Hotspur
Фулхэм|Fulham
Халл Сити|Hull City
Челси|Chelsea
Эвертон|Everton
""",

    "LaLiga": """
Алавес|Deportivo Alavés
Атлетик Бильбао|Athletic Club
Атлетико Мадрид|Atlético de Madrid
Барселона|FC Barcelona
Валенсия|Valencia CF
Вильярреал|Villarreal CF
Депортиво А-Корунья|RC Deportivo
Леванте|Levante UD
Малага|Málaga CF
Осасуна|CA Osasuna
Райо Вальекано|Rayo Vallecano
Расинг Сантандер|R. Racing Club
Реал Бетис|Real Betis
Реал Мадрид|Real Madrid
Реал Сосьедад|Real Sociedad
Севилья|Sevilla FC
Сельта|Celta
Хетафе|Getafe CF
Эльче|Elche CF
Эспаньол|RCD Espanyol de Barcelona
""",

    "SerieA": """
Аталанта|Atalanta
Болонья|Bologna
Венеция|Venezia
Дженоа|Genoa
Интер|Internazionale
Кальяри|Cagliari
Комо|Como
Лацио|Lazio
Лечче|Lecce
Милан|Milan
Монца|Monza
Наполи|Napoli
Парма|Parma
Рома|Roma
Сассуоло|Sassuolo
Торино|Torino
Удинезе|Udinese
Фиорентина|Fiorentina
Фрозиноне|Frosinone
Ювентус|Juventus
""",

    "Bundesliga": """
Айнтрахт Франкфурт|Eintracht Frankfurt
Аугсбург|FC Augsburg
Бавария Мюнхен|FC Bayern München
Байер 04|Bayer 04 Leverkusen
Боруссия Дортмунд|Borussia Dortmund
Боруссия Мёнхенгладбах|Borussia Mönchengladbach
Вердер Бремен|SV Werder Bremen
Гамбург|Hamburger SV
Кёльн|1. FC Köln
Майнц 05|1. FSV Mainz 05
Падерборн 07|SC Paderborn 07
РБ Лейпциг|RB Leipzig
Унион Берлин|1. FC Union Berlin
Фрайбург|SC Freiburg
Хоффенхайм|TSG Hoffenheim
Шальке 04|FC Schalke 04
Штутгарт|VfB Stuttgart
Эльферсберг|SV 07 Elversberg
""",

    "Ligue1": """
Анже|angers sco
Брест|stade brestois 29
Гавр|le havre
Ланс|rc lens
Ле-Ман|le mans fc
Лилль|losc
Лион|olympique lyonnais
Лорьян|fc lorient
Марсель|olympique de marseille
Монако|as monaco
Ницца|ogc nice
Осер|aj auxerre
ПСЖ|paris saint-germain
Париж|paris fc
Ренн|stade rennais fc
Страсбур|rc strasbourg alsace
Труа|estac troyes
Тулуза|toulouse fc
""",
}


def load_mapping():
    """Validate explicit names against the V8 team bridge."""

    bridge = bridge_module.load_bridge()
    result = {}

    for league, raw in RAW_MAPPING.items():

        mapping = {}

        for line in raw.strip().splitlines():

            ru, calendar = (
                value.strip()
                for value in line.split("|", 1)
            )

            key = soccer.identity(ru)

            if key in mapping:
                raise RuntimeError(
                    f"Duplicate Russian identity: {league}/{key}"
                )

            mapping[key] = calendar

        expected = set(bridge[league])
        actual = set(mapping.values())

        if actual != expected:
            raise RuntimeError(
                f"{league}: mapping coverage mismatch\n"
                f"Missing: {sorted(expected - actual)}\n"
                f"Extra: {sorted(actual - expected)}"
            )

        if len(mapping) != len(expected):
            raise RuntimeError(
                f"{league}: ambiguous mapping"
            )

        result[league] = mapping

    if sum(len(x) for x in result.values()) != 96:
        raise RuntimeError("Expected 96 mapped teams")

    return result


def fixture_date(row, ligue_dates):

    if row["competition"] == "Ligue1":

        return ligue_dates.get(
            str(row["match_id"])
        )

    return v8.parse_date(row.get("date"))


def main():

    print("=== SOCCER365 -> V8 FIXTURE BRIDGE V1 ===")
    print("MODE: RESEARCH / NETWORK READ ONLY")

    mapping = load_mapping()

    print("\n=== TEAM MAPPING ===")

    for league in v8.LEAGUES:
        print(
            f"{league:12s}",
            len(mapping[league]),
        )

    fixtures, _ = adapter.load_all()

    _, _, provider, _ = (
        ligue.load_previous_observation()
    )

    ligue_dates = {
        str(row["match_id"]): v8.parse_date(
            row.get("date")
        )
        for row in provider["matches"]
    }

    start = date(2026, 11, 27)
    end = date(2026, 11, 30)

    # Build a strict index from Adapter V4.
    index = {}

    for row in fixtures:

        league = row["competition"]

        if league not in v8.LEAGUES:
            continue

        day = fixture_date(row, ligue_dates)

        if day is None or not start <= day <= end:
            continue

        if row["state_status"] in v8.ACCEPTED_RESULTS:
            continue

        key = (
            day,
            league,
            row["home"],
            row["away"],
        )

        if key in index:
            raise RuntimeError(
                f"Duplicate V8 fixture: {key}"
            )

        index[key] = row

    print("\nV8 fixtures in period:", len(index))

    matched = {}
    unmatched = []
    ambiguous = []
    source_counts = Counter()

    for offset in range(4):

        day = start + timedelta(days=offset)

        print()
        print("=== SOCCER365 DATE:", day, "===")

        url = (
            "https://soccer365.ru/online/&date="
            + day.isoformat()
        )

        rows = soccer.listing(
            soccer.fetch(url)
        )

        for item in rows:

            competition = item["competition"]

            if competition not in LEAGUES:
                continue

            league = LEAGUES[competition]

            source_counts[league] += 1

            home_key = soccer.identity(item["home"])
            away_key = soccer.identity(item["away"])

            home = mapping[league].get(home_key)
            away = mapping[league].get(away_key)

            if not home or not away:

                unmatched.append((
                    day,
                    league,
                    item["home"],
                    item["away"],
                    "TEAM_NOT_MAPPED",
                ))

                continue

            key = (day, league, home, away)

            if key not in index:

                unmatched.append((
                    day,
                    league,
                    item["home"],
                    item["away"],
                    "FIXTURE_NOT_FOUND",
                ))

                continue

            if key in matched:

                ambiguous.append((
                    key,
                    item["id"],
                ))

                continue

            matched[key] = item["id"]

            print(
                "MATCHED",
                item["id"],
                "|",
                item["home"],
                "—",
                item["away"],
            )

    missing_v8 = (
        set(index) - set(matched)
    )

    print("\n=== RECONCILIATION ===")

    print("V8 fixtures:", len(index))
    print("Soccer365 matches:", sum(source_counts.values()))
    print("Matched fixtures:", len(matched))
    print("Unmatched Soccer365:", len(unmatched))
    print("Ambiguous:", len(ambiguous))
    print("Missing V8 matches:", len(missing_v8))

    print("\n=== SOURCE COVERAGE ===")

    for league in v8.LEAGUES:

        print(
            f"{league:12s}",
            source_counts[league],
        )

    print("\n=== UNMATCHED ===")

    for item in unmatched:
        print(item)

    print("\n=== AMBIGUOUS ===")

    for item in ambiguous:
        print(item)

    print("\n=== MISSING V8 ===")

    for item in sorted(missing_v8):
        print(item)

    print("\n=== FINAL STATUS ===")

    if (
        len(index) == 48
        and sum(source_counts.values()) == 48
        and len(matched) == 48
        and not unmatched
        and not ambiguous
        and not missing_v8
    ):
        print("SOCCER365 TO V8 BRIDGE: PASSED")
    else:
        print("SOCCER365 TO V8 BRIDGE: REVIEW REQUIRED")

    print("KICKOFF TIMES: NOT VERIFIED")
    print("HISTORICAL AVAILABILITY: NOT VERIFIED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS RECORDED")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
