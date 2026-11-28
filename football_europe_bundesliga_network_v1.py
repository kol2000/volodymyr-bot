#!/usr/bin/env python3
"""Bubus Bundesliga Network V1.

Reliable OpenLigaDB download with retries.

READ ONLY.
No archive writes.
No forecast recording.
"""

import socket
import time
import urllib.error

import football_europe_bundesliga_season_v1 as season


DELAYS = (0, 3, 7, 15)


def download():

    last_error = None

    for attempt, delay in enumerate(DELAYS, 1):

        if delay:
            print(
                f"Waiting {delay}s before retry...",
                flush=True,
            )
            time.sleep(delay)

        print(
            f"OpenLigaDB request {attempt}/{len(DELAYS)}",
            flush=True,
        )

        try:

            raw, data = season.download()

            print(
                f"OpenLigaDB OK: {len(raw)} bytes",
                flush=True,
            )

            return raw, data

        except (
            TimeoutError,
            socket.timeout,
            ConnectionError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ) as exc:

            last_error = exc

            print(
                "Network error:",
                type(exc).__name__,
                str(exc)[:200],
                flush=True,
            )

    raise RuntimeError(
        "OpenLigaDB unavailable after "
        f"{len(DELAYS)} attempts: {last_error}"
    )


if __name__ == "__main__":

    raw, data = download()

    print("Matches:", len(data))
    print("READ ONLY — NO FILES MODIFIED")
