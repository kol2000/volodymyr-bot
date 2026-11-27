#!/usr/bin/env python3
"""Probe independent European kickoff sources. Read only."""

import hashlib
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

EVIDENCE = Path(
    "state/football_live/verified/kickoffs_v1.json"
)

MAX_BYTES = 2_000_000


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:
        content_type = response.headers.get(
            "Content-Type", ""
        )

        raw = response.read(MAX_BYTES + 1)

        if len(raw) > MAX_BYTES:
            raise RuntimeError("Page too large")

        if "html" not in content_type.lower():
            raise RuntimeError(
                f"Unexpected content type: {content_type}"
            )

        return raw


def main():
    data = json.loads(
        EVIDENCE.read_text(encoding="utf-8")
    )

    print("=== EUROPE INDEPENDENT SOURCE PROBE ===")

    successful = 0

    for match in data["matches"]:
        print("\n" + "=" * 65)

        print(
            "MATCH:",
            match["home"],
            "—",
            match["away"]
        )

        print("URL:", match["url"])

        try:
            raw = fetch(match["url"])

            sha = hashlib.sha256(raw).hexdigest()

            parser = TextParser()
            parser.feed(
                raw.decode("utf-8", errors="replace")
            )

            text = " ".join(parser.parts)

            print("HTTP: OK")
            print("Bytes:", len(raw))
            print("SHA256:", sha)
            print("Extracted text length:", len(text))

            patterns = [
                r"\b\d{1,2}:\d{2}\b",
                r"\b\d{1,2}\.\d{2}\.\d{4}\b",
                r"\b22 November\b",
                r"\b23 November\b",
                r"\b22 Nov\b",
                r"\b23 Nov\b",
            ]

            found = []

            for pattern in patterns:
                for match_obj in re.finditer(
                    pattern, text, re.IGNORECASE
                ):
                    start = max(
                        0, match_obj.start() - 100
                    )
                    end = min(
                        len(text),
                        match_obj.end() + 100
                    )

                    snippet = text[start:end]

                    if snippet not in found:
                        found.append(snippet)

            print("\nDATE/TIME FRAGMENTS:")

            for snippet in found[:12]:
                print(" ", snippet)

            if not found:
                print(" No visible date/time fragments")

            successful += 1

        except Exception as exc:
            print(
                "ERROR:",
                type(exc).__name__,
                str(exc)
            )

    print("\n=== SUMMARY ===")
    print("Pages retrieved:", successful, "/ 4")
    print("Automatic kickoff verification: NOT YET")
    print("Forecast recording: BLOCKED")
    print("READ ONLY")

if __name__ == "__main__":
    main()
