#!/usr/bin/env python3

import csv
import re
import urllib.request

from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser
from collections import Counter

URL = "https://rplstat.ru/kalendar/"

ROOT = Path(__file__).resolve().parent

HISTORY = (
    ROOT /
    "state/football_expansion/rpl_verified/"
    "RPL_results_2627_merged.csv"
)

ALIASES = {
    "Ахмат": "Akhmat Grozny",
    "Акрон": "Akron Togliatti",
    "Балтика": "Baltika",
    "ЦСКА": "CSKA Moscow",
    "Динамо Мх": "Dynamo Makhachkala",
    "Динамо": "Dynamo Moscow",
    "Ростов": "FK Rostov",
    "Факел": "Fakel Voronezh",
    "Краснодар": "Krasnodar",
    "Крылья Советов": "Krylya Sovetov",
    "Локомотив": "Lokomotiv Moscow",
    "Оренбург": "Orenburg",
    "Родина": "Rodina Moscow",
    "Рубин": "Rubin Kazan",
    "Спартак": "Spartak Moscow",
    "Зенит": "Zenit",
}

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


class TextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.items = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.items.append(value)


def fetch():

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 Bubus-RPL/1.0"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:

        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        raw = response.read()

    parser = TextParser()
    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    return parser.items


def parse_matches(lines):

    matches = []
    current_round = None
    year = None

    round_re = re.compile(
        r"^Тур\s+(\d+)\s*[·—-].*?\b(20\d{2})\b"
    )

    date_re = re.compile(
        r"^(\d{1,2})\s+([а-яё]+),\s*"
        r"(\d{1,2}):(\d{2})\s+мск"
    )

    score_re = re.compile(
        r"^(\d+)\s*:\s*(\d+)$"
    )

    i = 0

    while i < len(lines):

        line = lines[i]

        round_match = round_re.match(line)

        if round_match:

            current_round = int(
                round_match.group(1)
            )

            year = int(
                round_match.group(2)
            )

            i += 1
            continue

        date_match = date_re.match(line)

        if not date_match or current_round is None:
            i += 1
            continue

        day = int(date_match.group(1))
        month_name = date_match.group(2)
        hour = int(date_match.group(3))
        minute = int(date_match.group(4))

        if month_name not in MONTHS:
            raise ValueError(
                f"Unknown month: {month_name}"
            )

        if i + 3 >= len(lines):
            raise ValueError(
                f"Incomplete match near: {line}"
            )

        home_raw = lines[i + 1]
        score_raw = lines[i + 2]
        away_raw = lines[i + 3]

        # Не пропускаем незнакомые клубы молча.
        if home_raw not in ALIASES:
            raise ValueError(
                f"Unknown home team: {home_raw}"
            )

        if away_raw not in ALIASES:
            raise ValueError(
                f"Unknown away team: {away_raw}"
            )

        score = score_re.match(score_raw)

        if score:
            goals_home = int(score.group(1))
            goals_away = int(score.group(2))

        elif score_raw in ("—", "-", "–"):
            goals_home = None
            goals_away = None

        else:
            raise ValueError(
                f"Unexpected score: {score_raw}"
            )

        kickoff = datetime(
            year,
            MONTHS[month_name],
            day,
            hour,
            minute
        )

        matches.append({
            "gw": current_round,
            "kickoff_msk": kickoff,
            "home": ALIASES[home_raw],
            "away": ALIASES[away_raw],
            "gh": goals_home,
            "ga": goals_away,
        })

        i += 4

    return matches


def load_history():

    with HISTORY.open(
        encoding="utf-8-sig",
        newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    result = {}

    for row in rows:

        key = (
            row["date"],
            row["home"],
            row["away"]
        )

        if key in result:
            raise ValueError(
                f"Duplicate history: {key}"
            )

        result[key] = row

    return result


def main():

    print("=== RPL SOURCE V1 ===")
    print("Fetching:", URL)

    lines = fetch()
    matches = parse_matches(lines)

    history = load_history()

    print("Parsed matches:", len(matches))

    selected = [
        m for m in matches
        if m["gw"] in (15, 16)
    ]

    counts = Counter(
        m["gw"] for m in selected
    )

    print("Round 15:", counts[15])
    print("Round 16:", counts[16])

    if counts[15] != 8 or counts[16] != 8:
        raise RuntimeError(
            "Incomplete RPL calendar"
        )

    keys = set()

    new_results = 0
    confirmed = 0
    conflicts = 0

    for m in selected:

        key = (
            m["kickoff_msk"].date().isoformat(),
            m["home"],
            m["away"]
        )

        if key in keys:
            raise ValueError(
                f"Duplicate fixture: {key}"
            )

        keys.add(key)

        result = (
            f'{m["gh"]}:{m["ga"]}'
            if m["gh"] is not None
            else "UPCOMING"
        )

        print(
            f'GW{m["gw"]:02d}',
            m["kickoff_msk"].strftime("%Y-%m-%d %H:%M"),
            m["home"],
            result,
            m["away"],
        )

        if m["gh"] is None:
            continue

        old = history.get(key)

        if old is None:
            new_results += 1

        elif (
            int(old["ft_home"]) == m["gh"]
            and int(old["ft_away"]) == m["ga"]
        ):
            confirmed += 1

        else:
            conflicts += 1

            print(
                "  !!! SCORE CONFLICT !!!",
                old["ft_home"],
                old["ft_away"],
                "vs",
                m["gh"],
                m["ga"]
            )

    print("\n=== SUMMARY ===")
    print("Matches:", len(selected))
    print("New finished results:", new_results)
    print("Matching existing results:", confirmed)
    print("Score conflicts:", conflicts)
    print("Files modified: NO")

    if conflicts:
        raise RuntimeError(
            "Score conflicts require review"
        )


if __name__ == "__main__":
    main()
