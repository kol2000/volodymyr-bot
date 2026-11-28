#!/usr/bin/env python3

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

def run(script):
    result = subprocess.run(
        [PYTHON, str(ROOT / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=150
    )

    if result.returncode:
        raise RuntimeError(
            f"{script} failed:\n{result.stdout}\n{result.stderr}"
        )

    return result.stdout


def main():
    print("=== BUBUS REAL RESULT ACCEPTANCE ===", flush=True)

    observer = run("football_ucl_results_v26.py")

    ready_match = re.search(r"^READY:\s*(\d+)\s*$", observer, re.M)
    if not ready_match:
        raise RuntimeError("V26 READY count missing")

    ready = int(ready_match.group(1))

    print(f"V26 confirmed results: {ready}")

    if ready == 0:
        print("STATUS: WAITING FOR REAL RESULTS")
        return

    shadow = run("football_ucl_shadow_v27.py")

    match = re.search(
        r"SHADOW RESULTS:\s*(\d+)\s*->\s*(\d+)",
        shadow
    )

    if not match:
        raise RuntimeError("V27 shadow result counts missing")

    before, after = map(int, match.groups())
    added = after - before

    if "SHADOW FILE DELETED; no production DB writes" not in shadow:
        raise RuntimeError("V27 did not confirm shadow cleanup")

    print(f"V27 shadow new results: {added}")

    if added != ready:
        raise RuntimeError(
            f"Mismatch: V26 READY={ready}, V27 added={added}"
        )

    print()
    print("========================================")
    print("REAL RESULT ACCEPTANCE CANDIDATE")
    print(f"Confirmed: {ready}")
    print(f"Shadow committed: {added}")
    print("Production SQLite: NOT MODIFIED")
    print("MANUAL REVIEW REQUIRED BEFORE AUTO-COMMIT")
    print("========================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ACCEPTANCE BLOCKED:", error, file=sys.stderr)
        sys.exit(1)
