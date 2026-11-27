#!/usr/bin/env python3
"""Bubus Bundesliga DFL PDF Probe V1.

Inspect official DFL season fixture PDF.

READ ONLY for project state.
Temporary files are automatically deleted.
"""

import re
import shutil
import subprocess
import tempfile
import urllib.request

from pathlib import Path


URL = (
    "https://media.dfl.de/sites/2/2026/07/"
    "DE_s73GnueV_Bundesliga_Spielplan_2026_27.pdf"
)

MAX_PDF = 10_000_000


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():

    print("=== BUBUS BUNDESLIGA DFL PROBE V1 ===")
    print("MODE: READ ONLY")

    print("\n=== PDF TOOL ===")

    executable = shutil.which("pdftotext")

    print("pdftotext:", executable)

    if not executable:
        print(
            "ERROR: pdftotext is not installed"
        )
        print(
            "Install: sudo apt install poppler-utils"
        )
        return

    print("\n=== DOWNLOAD OFFICIAL PDF ===")

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/pdf",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=45,
    ) as response:

        require(
            response.status == 200,
            "Unexpected HTTP status",
        )

        raw = response.read(MAX_PDF + 1)

    require(
        len(raw) <= MAX_PDF,
        "PDF too large",
    )

    require(
        raw.startswith(b"%PDF-"),
        "Invalid PDF signature",
    )

    print("HTTP: 200")
    print("PDF bytes:", len(raw))

    with tempfile.TemporaryDirectory(
        prefix="bubus-dfl-"
    ) as tmp:

        root = Path(tmp)

        pdf = root / "schedule.pdf"

        pdf.write_bytes(raw)

        result = subprocess.run(
            [
                executable,
                "-layout",
                "-nopgbrk",
                "-enc",
                "UTF-8",
                str(pdf),
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

        text = result.stdout

        lines = text.splitlines()

        print("\n=== EXTRACTION ===")

        print("Text characters:", len(text))
        print("Lines:", len(lines))

        print(
            "Bundesliga mentions:",
            text.lower().count("bundesliga"),
        )

        print("\n=== FIRST 45 NONEMPTY LINES ===")

        nonempty = [
            line
            for line in lines
            if line.strip()
        ]

        for line in nonempty[:45]:
            print(repr(line[:180]))

        print("\n=== POSSIBLE FIXTURE LINES ===")

        pattern = re.compile(
            r"\b(?:FC Bayern München|"
            r"Borussia Dortmund|"
            r"RB Leipzig|"
            r"VfB Stuttgart)\b",
            re.IGNORECASE,
        )

        candidates = [
            line
            for line in lines
            if pattern.search(line)
        ]

        print(
            "Candidate lines:",
            len(candidates),
        )

        for line in candidates[:20]:
            print(repr(line[:200]))

        print("\n=== DOCUMENT END SAMPLE ===")

        for line in nonempty[-25:]:
            print(repr(line[:180]))

    print("\n=== FINAL STATUS ===")

    print("OFFICIAL PDF: ACCESSIBLE")
    print("TEMPORARY FILES: REMOVED")
    print("PROJECT ARCHIVES: UNCHANGED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
