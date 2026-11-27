#!/usr/bin/env python3

import os
import tempfile
from pathlib import Path

import football_ucl_results_v16 as observer

CACHE = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_forward/uefa_latest.html"
)

original_fetch = observer.fetch


def fetch_and_save():
    raw = original_fetch()

    if not raw or len(raw) < 10000:
        raise ValueError("UEFA HTML unexpectedly small")

    CACHE.parent.mkdir(parents=True, exist_ok=True)

    temp_name = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=CACHE.parent,
            prefix=".uefa_",
            suffix=".tmp",
            delete=False
        ) as f:
            temp_name = f.name
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_name, CACHE)

    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)

    print(
        f"UEFA HTML cached: {CACHE} "
        f"({len(raw)} bytes)",
        flush=True
    )

    return raw


observer.fetch = fetch_and_save

if __name__ == "__main__":
    observer.main()
