#!/usr/bin/env python3
"""Read-only diagnostics for RPL Sport-Express match centers."""

import re
import urllib.request
from html.parser import HTMLParser

import football_rpl_se_calendar_v1 as se


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.items.append(value)


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache",
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:
        data = response.read(2_000_001)

        if len(data) > 2_000_000:
            raise RuntimeError("HTML too large")

        return data


def main():
    print("=== RPL MATCH CENTER AUDIT V1 ===")

    calendar_raw = se.fetch()

    parser = se.Parser()
    parser.feed(
        calendar_raw.decode(
            "utf-8",
            errors="replace"
        )
    )

    round15 = se.extract_round(
        parser.tokens,
        15
    )

    matches = se.parse_matches(round15)

    completed = [
        m for m in matches
        if m["score"] is not None
        and m["url"]
        and m["status"] in ("Завершен", "Завершён")
    ]

    print("Completed matches:", len(completed))

    for match in completed:
        print("\n" + "=" * 65)

        print(
            match["home"],
            match["score"],
            match["away"]
        )

        print("URL:", match["url"])

        try:
            raw = fetch(match["url"])

            text_parser = TextParser()
            text_parser.feed(
                raw.decode(
                    "utf-8",
                    errors="replace"
                )
            )

            items = text_parser.items

            print("Page bytes:", len(raw))
            print("Text tokens:", len(items))

            positions = [
                i for i, value in enumerate(items)
                if value.casefold() in (
                    "завершен",
                    "завершён",
                )
            ]

            print(
                "Finished-status tokens:",
                len(positions)
            )

            for pos in positions[:3]:
                print(
                    "CONTEXT:",
                    repr(items[
                        max(0, pos - 10):pos + 14
                    ])
                )

            scores = [
                (i, value)
                for i, value in enumerate(items)
                if re.fullmatch(
                    r"\d{1,2}\s*:\s*\d{1,2}",
                    value
                )
            ]

            print(
                "Score-like tokens:",
                len(scores)
            )

            for index, value in scores[:5]:
                print(
                    "SCORE CONTEXT:",
                    repr(items[
                        max(0, index - 4):index + 5
                    ])
                )

        except Exception as exc:
            print(
                "CHECK FAILED:",
                type(exc).__name__,
                str(exc)
            )

    print("\nREAD ONLY — NO FILES MODIFIED")


if __name__ == "__main__":
    main()
