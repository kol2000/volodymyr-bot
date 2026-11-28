#!/usr/bin/env python3
"""Read-only Sport-Express RPL calendar discovery."""

import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

URL = (
    "https://www.sport-express.net/"
    "football/n/russia/premier/2026-2027/calendar/"
)

DATE_RE = re.compile(
    r"^\d{1,2}\s+"
    r"(?:января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)"
    r"\s+\d{2}:\d{2}$"
)

SCORE_RE = re.compile(r"^\d{1,2}:\d{1,2}$")

TEAMS = {
    "Ростов": "FK Rostov",
    "Крылья Советов": "Krylya Sovetov",
    "Оренбург": "Orenburg",
    "Балтика": "Baltika",
    "Акрон": "Akron Togliatti",
    "Факел": "Fakel Voronezh",
    "Рубин": "Rubin Kazan",
    "Зенит": "Zenit",
    "ЦСКА": "CSKA Moscow",
    "Ахмат": "Akhmat Grozny",
    "Динамо": "Dynamo Moscow",
    "Динамо Мх": "Dynamo Makhachkala",
    "Краснодар": "Krasnodar",
    "Спартак": "Spartak Moscow",
    "Родина": "Rodina Moscow",
    "Локомотив": "Lokomotiv Moscow",
}


class Parser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.tokens = []
        self.href = None
        self.href_stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href_stack.append(
                self.href
            )
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "a":
            self.href = (
                self.href_stack.pop()
                if self.href_stack
                else None
            )

    def handle_data(self, data):
        value = " ".join(data.split())

        if value:
            self.tokens.append({
                "text": value,
                "href": self.href
            })


def fetch():
    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:
        raw = response.read(3_000_001)

    if len(raw) > 3_000_000:
        raise RuntimeError("HTML exceeds size limit")

    return raw


def extract_round(tokens, number):

    start_label = f"{number}-й тур"
    end_label = f"{number + 1}-й тур"

    starts = [
        i for i, token in enumerate(tokens)
        if token["text"] == start_label
    ]

    print(
        f"Found '{start_label}' headings:",
        len(starts)
    )

    candidates = []

    for start in starts:

        ends = [
            i for i in range(start + 1, len(tokens))
            if tokens[i]["text"] == end_label
        ]

        if not ends:
            continue

        end = ends[0]
        chunk = tokens[start + 1:end]

        dates = [
            token for token in chunk
            if DATE_RE.fullmatch(token["text"])
        ]

        print(
            "Candidate:",
            "start=", start,
            "end=", end,
            "dates=", len(dates)
        )

        if len(dates) == 8:
            candidates.append(chunk)

    if len(candidates) != 1:
        raise RuntimeError(
            "Cannot uniquely identify round "
            f"{number}: {len(candidates)} "
            "candidate blocks with 8 match dates"
        )

    print(
        "Selected round:",
        number,
        "| 8 match dates found"
    )

    return candidates[0]


def parse_matches(tokens):

    positions = [
        i for i, token in enumerate(tokens)
        if DATE_RE.fullmatch(token["text"])
    ]

    matches = []

    for n, start in enumerate(positions):

        end = (
            positions[n + 1]
            if n + 1 < len(positions)
            else len(tokens)
        )

        chunk = tokens[start:end]

        date_text = chunk[0]["text"]

        if len(chunk) < 4:
            raise RuntimeError(
                f"Too few tokens: {date_text}"
            )

        status = chunk[1]["text"]

        if status not in (
            "Завершен",
            "Завершён",
            "Не начался",
            "Идет",
            "Идёт",
            "Перерыв",
        ):
            raise RuntimeError(
                f"Unknown status: {status}"
            )

        teams = [
            token for token in chunk
            if token["text"] in TEAMS
        ]

        if len(teams) != 2:
            raise RuntimeError(
                f"Expected 2 teams at {date_text}"
            )

        home = TEAMS[teams[0]["text"]]
        away = TEAMS[teams[1]["text"]]

        score = None
        url = None

        completed = status in (
            "Завершен",
            "Завершён"
        )

        if completed:

            if len(chunk) != 7:
                raise RuntimeError(
                    f"Unexpected completed-match "
                    f"structure at {date_text}: "
                    f"{[x['text'] for x in chunk]}"
                )

            home_goals = chunk[3]
            separator = chunk[4]
            away_goals = chunk[5]

            if not (
                home_goals["text"].isdigit()
                and separator["text"] == ":"
                and away_goals["text"].isdigit()
            ):
                raise RuntimeError(
                    f"Invalid score at {date_text}"
                )

            hrefs = [
                home_goals["href"],
                separator["href"],
                away_goals["href"],
            ]

            if not (
                hrefs[0]
                and hrefs[0] == hrefs[1] == hrefs[2]
            ):
                raise RuntimeError(
                    f"Score links mismatch: {date_text}"
                )

            url = urljoin(URL, hrefs[0])

            from urllib.parse import urlparse

            parsed = urlparse(url)

            if (
                parsed.scheme != "https"
                or parsed.hostname not in (
                    "www.sport-express.net",
                    "sport-express.net",
                )
                or not parsed.path.startswith(
                    "/football/n/russia/premier/match-"
                )
            ):
                raise RuntimeError(
                    f"Unexpected match URL: {url}"
                )

            score = (
                f"{home_goals['text']}:"
                f"{away_goals['text']}"
            )

        matches.append({
            "date": date_text,
            "status": status,
            "home": home,
            "away": away,
            "score": score,
            "url": url,
        })

    return matches


def main():

    print("=== BUBUS RPL SE CALENDAR V1 ===")

    raw = fetch()

    print("Downloaded bytes:", len(raw))

    parser = Parser()
    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    print("HTML tokens:", len(parser.tokens))

    round15 = extract_round(
        parser.tokens,
        15
    )

    matches = parse_matches(round15)

    print("Round 15 matches:", len(matches))

    if len(matches) != 8:
        raise RuntimeError(
            f"Expected 8 matches, found {len(matches)}"
        )

    for match in matches:

        print()
        print(
            match["date"],
            match["home"],
            "—",
            match["away"]
        )
        print("Status:", match["status"])
        print("Score:", match["score"])
        print("Match URL:", match["url"])

    print("\nREAD ONLY")
    print("No files modified")


if __name__ == "__main__":
    main()
