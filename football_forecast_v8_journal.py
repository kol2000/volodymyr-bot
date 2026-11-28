#!/usr/bin/env python3
# Single locked writer for Bubus V8 research observations.
# Identity: match_id + snapshot_id + data_fingerprint.
# batch_id is provenance, not part of the deduplication key.
# A later evaluated status upgrades NOT_EVALUATED for the same identity.
# Historical V1 records are preserved.

import fcntl
import json
import os
import re
import stat
import tempfile

from pathlib import Path


JOURNAL = Path(
    "/home/botadmin/volodymyr_bot/"
    "state/football_v8_shadow/observations.jsonl"
)

SCHEMA = "bubus_v8_observation_v2"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


def _identity(row):
    if row.get("schema") != SCHEMA:
        return None
    key = (
        row.get("match_id"),
        row.get("snapshot_id"),
        row.get("data_fingerprint"),
    )
    if not all(isinstance(value, str) and value for value in key):
        raise RuntimeError("Invalid existing V2 observation identity")
    if not FINGERPRINT_RE.fullmatch(key[2]):
        raise RuntimeError("Invalid existing V2 data fingerprint")
    return key


def _serialize(row):
    return (
        json.dumps(
            row,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")


def _read_journal(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return [], []
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Observation journal is not a regular file")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)

    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError("Observation journal is not a regular file")
        data = stream.read()

    if data and not data.endswith(b"\n"):
        raise RuntimeError("Journal has an incomplete final line")

    lines = data.splitlines(keepends=True)
    rows = []
    for number, raw in enumerate(lines, 1):
        try:
            row = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Invalid journal JSON at line {number}"
            ) from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"Journal line {number} is not an object")
        _identity(row)
        rows.append(row)
    return lines, rows


def _atomic_replace(path, lines):
    fd, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            for line in lines:
                stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, path)

        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        directory_fd = os.open(path.parent, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_observations(observations, journal_path=None):
    # Returns (added, updated, skipped). journal_path is for isolated tests.
    path = Path(journal_path) if journal_path is not None else JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = list(observations)

    for row in incoming:
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            raise RuntimeError("Unsupported observation record")
        key = (
            row.get("match_id"),
            row.get("snapshot_id"),
            row.get("data_fingerprint"),
        )
        if not all(isinstance(value, str) and value for value in key):
            raise RuntimeError("Invalid observation identity")
        if not re.fullmatch(r"s365:\d+", key[0]):
            raise RuntimeError("Invalid observation match ID")
        if not FINGERPRINT_RE.fullmatch(key[2]):
            raise RuntimeError("Invalid observation fingerprint")
        if not isinstance(row.get("batch_id"), str) or not row["batch_id"]:
            raise RuntimeError("Invalid observation batch ID")
        if not isinstance(row.get("legacy_status"), str):
            raise RuntimeError("Invalid legacy status")
        _serialize(row)

    lock_path = path.with_name(path.name + ".lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    lock_fd = os.open(lock_path, flags, 0o600)

    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise RuntimeError("Journal lock is not a regular file")
        with os.fdopen(lock_fd, "r+b", closefd=False) as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                lines, rows = _read_journal(path)
                positions = {}
                for index, existing in enumerate(rows):
                    identity = _identity(existing)
                    if identity is not None:
                        positions[identity] = index

                added = updated = skipped = 0
                for row in incoming:
                    key = (
                        row["match_id"],
                        row["snapshot_id"],
                        row["data_fingerprint"],
                    )
                    if key not in positions:
                        positions[key] = len(rows)
                        rows.append(dict(row))
                        lines.append(_serialize(row))
                        added += 1
                        continue

                    index = positions[key]
                    existing = rows[index]
                    if (
                        existing.get("legacy_status") == "NOT_EVALUATED"
                        and row.get("legacy_status") != "NOT_EVALUATED"
                    ):
                        merged = dict(existing)

                        # Preserve the original V8 forecast and provenance.
                        for field in (
                            "legacy_status",
                            "legacy_reason",
                            "legacy_probabilities",
                        ):
                            merged[field] = row.get(field)

                        rows[index] = merged
                        lines[index] = _serialize(merged)
                        updated += 1
                    else:
                        skipped += 1

                if added or updated:
                    _atomic_replace(path, lines)
                return added, updated, skipped
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
