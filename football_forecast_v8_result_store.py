#!/usr/bin/env python3
"""Research-only, append-only storage of verified football results."""

import fcntl
import json
import os
import stat

from pathlib import Path


RESULTS = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_v8_shadow/results.jsonl"
)


def save_results(records):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(RESULTS, flags, 0o600)

    added = 0
    skipped = 0

    with os.fdopen(fd, "r+b") as stream:

        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError("Results journal is not a regular file")

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)

        try:
            stream.seek(0)
            data = stream.read()

            if data and not data.endswith(b"\n"):
                raise RuntimeError("Incomplete results journal")

            known = {}

            for line in data.splitlines():
                row = json.loads(line)

                key = row["match_id"]

                if key in known and known[key] != row:
                    raise RuntimeError(
                        f"Conflicting stored results: {key}"
                    )

                known[key] = row

            pending = []
            seen = dict(known)

            for row in records:

                if row.get("schema") != "bubus_v8_result_v1":
                    raise RuntimeError("Invalid result schema")

                key = row["match_id"]

                if not isinstance(key, str) or not key.startswith("s365:"):
                    raise RuntimeError("Invalid match identity")

                home = row["home_goals"]
                away = row["away_goals"]

                if not (
                    type(home) is int
                    and type(away) is int
                    and 0 <= home <= 30
                    and 0 <= away <= 30
                ):
                    raise RuntimeError("Invalid final score")

                outcome = (
                    "HOME" if home > away
                    else "AWAY" if away > home
                    else "DRAW"
                )

                if row["outcome"] != outcome:
                    raise RuntimeError("Outcome mismatch")

                if key in seen:

                    old = seen[key]

                    comparable = (
                        "provider_id",
                        "home_goals",
                        "away_goals",
                        "outcome",
                        "kickoff_utc",
                    )

                    if any(old.get(k) != row.get(k) for k in comparable):
                        raise RuntimeError(
                            f"RESULT CONFLICT: {key}"
                        )

                    skipped += 1
                    continue

                payload = (
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ) + "\n"
                ).encode("utf-8")

                pending.append(payload)
                seen[key] = row
                added += 1

            stream.seek(0, os.SEEK_END)

            for payload in pending:
                stream.write(payload)

            if pending:
                stream.flush()
                os.fsync(stream.fileno())

        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    return added, skipped
