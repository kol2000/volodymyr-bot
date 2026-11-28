#!/usr/bin/env python3

import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import football_rpl_source_v1 as source

MSK = ZoneInfo("Europe/Moscow")

def main():

    print("=== BUBUS RPL MONITOR ===")

    now = datetime.now(timezone.utc)

    print(
        "Checked MSK:",
        now.astimezone(MSK).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    request = urllib.request.Request(
        source.URL,
        headers={
            "User-Agent": "Mozilla/5.0 Bubus-RPL-Monitor/1.0",
            "Cache-Control": "no-cache",
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=25
    ) as response:

        modified = response.headers.get(
            "Last-Modified"
        )

        raw = response.read()

    parser = source.TextParser()

    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    matches = source.parse_matches(
        parser.items
    )

    round15 = [
        m for m in matches
        if m["gw"] == 15
    ]

    print("Source Last-Modified:", modified)

    print("Round 15 matches:", len(round15))

    finished = 0

    for m in round15:

        if m["gh"] is None:
            result = "NO RESULT"
        else:
            finished += 1
            result = f'{m["gh"]}:{m["ga"]}'

        print(
            m["kickoff_msk"],
            m["home"],
            result,
            m["away"]
        )

    print("\nFinished:", f"{finished}/8")

    if finished == 8:
        print("STATUS: ROUND 15 COMPLETE")
    else:
        print("STATUS: WAITING FOR RESULTS")

    print("Files modified: NO")


if __name__ == "__main__":
    main()
