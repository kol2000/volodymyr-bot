#!/usr/bin/env python3
"""Bubus Ligue 1 Sources Discovery V1.

Investigate 2026/27 Ligue 1 sources.

Official:
- LFP season fixture PDF

Community:
- Open Matchday fixtures CSV
- Open Matchday results CSV

Independent:
- Football-Data results CSV

STRICTLY READ ONLY.
No project state writes.
No forecast recording.
"""

import csv
import io
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from collections import Counter
from pathlib import Path


SEASON = "2026-27"

BASE = (
    "https://raw.githubusercontent.com/"
    "oladeeayo/open-matchday/master/"
    "data/FRA_1_ligue-1/"
)

SOURCES = {
    "LFP_OFFICIAL_PDF": (
        "https://www.lfp.fr/assets/"
        "L1_MD_2627_CALENDRIER_SOMBRE_920e8f5afd.pdf"
    ),
    "OPEN_MATCHDAY_FIXTURES": (
        BASE + "fixtures_2627.csv"
    ),
    "OPEN_MATCHDAY_RESULTS": (
        BASE + "results_2627.csv"
    ),
    "FOOTBALL_DATA_RESULTS": (
        "https://www.football-data.co.uk/"
        "mmz4281/2627/F1.csv"
    ),
}

MAX_BYTES = 5_000_000


def download(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Bubus-Football-Monitor/1.0",
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=25,
    ) as response:

        status = response.status

        content_type = response.headers.get(
            "Content-Type", ""
        )

        raw = response.read(MAX_BYTES + 1)

    if len(raw) > MAX_BYTES:
        raise RuntimeError(
            "Source exceeds maximum size"
        )

    return status, content_type, raw


def inspect_csv(raw):

    text = raw.decode(
        "utf-8-sig",
        errors="replace",
    )

    reader = csv.DictReader(
        io.StringIO(text)
    )

    rows = list(reader)

    print("Columns:", reader.fieldnames)
    print("Rows:", len(rows))

    print("\nFIRST 3 ROWS")

    for row in rows[:3]:
        print(row)

    if not rows:
        return

    fields = reader.fieldnames or []

    for field in (
        "gw",
        "round",
        "matchday",
        "status",
        "Div",
    ):

        if field not in fields:
            continue

        counts = Counter(
            row.get(field, "")
            for row in rows
        )

        print(
            "\nDistribution:",
            field,
        )

        for name, count in sorted(
            counts.items()
        )[:40]:

            print(
                repr(name),
                count,
            )


def inspect_pdf(raw):

    print("PDF signature:", raw[:8])

    if not raw.startswith(b"%PDF-"):
        raise RuntimeError(
            "Invalid PDF signature"
        )

    executable = shutil.which(
        "pdftotext"
    )

    print("pdftotext:", executable)

    if not executable:
        print(
            "PDF extraction unavailable"
        )
        return

    with tempfile.TemporaryDirectory(
        prefix="bubus-ligue1-pdf-"
    ) as tmp:

        path = Path(tmp) / "calendar.pdf"

        path.write_bytes(raw)

        result = subprocess.run(
            [
                executable,
                "-layout",
                "-enc",
                "UTF-8",
                str(path),
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )

        text = result.stdout

        print(
            "Extracted characters:",
            len(text),
        )

        lines = [
            line
            for line in text.splitlines()
            if line.strip()
        ]

        print(
            "Nonempty lines:",
            len(lines),
        )

        print("\nFIRST 35 LINES")

        for line in lines[:35]:
            print(repr(line[:220]))

        print("\nLAST 15 LINES")

        for line in lines[-15:]:
            print(repr(line[:220]))


def main():

    print(
        "=== BUBUS LIGUE 1 SOURCE DISCOVERY V1 ==="
    )

    print("Season:", SEASON)
    print("Mode: READ ONLY")

    results = {}

    for name, url in SOURCES.items():

        print()
        print("=" * 65)
        print("SOURCE:", name)
        print("URL:", url)
        print("=" * 65)

        try:

            status, content_type, raw = (
                download(url)
            )

            print("HTTP:", status)
            print("Content-Type:", content_type)
            print("Bytes:", len(raw))

            if name.endswith("_PDF"):

                inspect_pdf(raw)

            else:

                inspect_csv(raw)

            results[name] = "OK"

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            UnicodeError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:

            results[name] = "ERROR"

            print(
                "ERROR:",
                type(exc).__name__,
                str(exc)[:250],
            )

    print()
    print("=" * 65)

    print("=== SOURCE SUMMARY ===")

    for name, status in results.items():
        print(name + ":", status)

    print("\n=== FINAL STATUS ===")

    print("DISCOVERY COMPLETED")
    print("NO PROJECT STATE FILES MODIFIED")
    print("NO ARCHIVES CREATED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
