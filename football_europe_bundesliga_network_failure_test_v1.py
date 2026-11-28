#!/usr/bin/env python3
"""Bundesliga HTTP 503 safety test.

Run actual Monitor V3 with mocked HTTP 503.

All writable paths are redirected to a temporary
directory.

NO REAL NETWORK REQUESTS.
NO PRODUCTION WRITES.
"""

import contextlib
import io
import tempfile

from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import football_europe_bundesliga_monitor_v3 as monitor
import football_europe_bundesliga_monitor_v2 as previous


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():

    print("=== BUNDESLIGA HTTP 503 SAFETY TEST ===")
    print("MODE: OFFLINE / ISOLATED")

    with tempfile.TemporaryDirectory(
        prefix="bubus-bundesliga-http503-"
    ) as tmp:

        root = Path(tmp)

        states = root / "states"
        provider = root / "provider"
        observations = root / "observations"

        error = HTTPError(
            url="https://api.openligadb.de/",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=None,
        )

        with contextlib.ExitStack() as stack:

            stack.enter_context(
                patch.object(
                    monitor,
                    "STATES",
                    states,
                )
            )

            stack.enter_context(
                patch.object(
                    monitor,
                    "PROVIDER",
                    provider,
                )
            )

            stack.enter_context(
                patch.object(
                    monitor,
                    "OBSERVATIONS",
                    observations,
                )
            )

            stack.enter_context(
                patch.object(
                    previous,
                    "STATES",
                    states,
                )
            )

            stack.enter_context(
                patch.object(
                    previous,
                    "PROVIDER",
                    provider,
                )
            )

            stack.enter_context(
                patch.object(
                    previous,
                    "OBSERVATIONS",
                    observations,
                )
            )

            downloader = stack.enter_context(
                patch.object(
                    monitor.network,
                    "download",
                    side_effect=error,
                )
            )

            output = io.StringIO()

            print("\n=== TEST 1: HTTP 503 ===")

            blocked = False

            try:

                with contextlib.redirect_stdout(output):
                    monitor.run(True)

            except HTTPError as exc:

                blocked = exc.code == 503

            require(
                blocked,
                "HTTP 503 was not propagated",
            )

            require(
                downloader.call_count == 1,
                "Unexpected download call count",
            )

            print("PASS: HTTP 503 detected")

            print("\n=== TEST 2: PRE-WRITE AUDIT ===")

            require(
                "PRE-WRITE AUDIT: PASSED"
                in output.getvalue(),
                "Pre-write audit did not execute",
            )

            print("PASS: pre-write audit executed")

            print("\n=== TEST 3: NO SNAPSHOTS ===")

            require(
                not list(root.rglob("*.json")),
                "Unexpected JSON files created",
            )

            print("PASS: no snapshots created")

            print("\n=== TEST 4: NO OBSERVATIONS ===")

            require(
                not observations.exists()
                or not list(
                    observations.glob("*.json")
                ),
                "Observation created after HTTP 503",
            )

            print("PASS: no observations created")

    print("\n=== FINAL STATUS ===")

    print("ALL 4 TESTS PASSED")
    print("HTTP 503: SAFE FAILURE")
    print("TEMPORARY FILES: REMOVED")
    print("PRODUCTION STATE: UNCHANGED")
    print("NO REAL NETWORK REQUESTS")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
