#!/usr/bin/env python3
"""Register immutable experimental EPL V8 observations.

Default mode: read-only dry run.

--write:
    Register one observation per match.

Never modifies:
    - original forecast batches
    - kickoff audits
    - historical CSV
    - Bundesliga V8 cache
    - Telegram messages
"""

import argparse
import hashlib
import json
import math
import os
import tempfile

from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent

DATA = ROOT / "state/football_v8_epl_shadow"

OBSERVATIONS = DATA / "observations"

MIN_LEAD = timedelta(minutes=30)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_latest(pattern):

    files = sorted(DATA.glob(pattern))

    require(
        bool(files),
        f"No files matching {pattern}",
    )

    path = files[-1]
    raw = path.read_bytes()

    return (
        path,
        raw,
        json.loads(raw),
    )


def parse_utc(value):

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    require(
        dt.tzinfo is not None,
        "Timestamp without timezone",
    )

    return dt.astimezone(timezone.utc)


def register():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print("=== BUBUS V8 EPL REGISTRATION ===")

    print(
        "MODE:",
        "WRITE" if args.write else "DRY RUN",
    )

    batch_path, batch_raw, batch = load_latest(
        "batch_*.json"
    )

    require(
        batch.get("schema")
        == "bubus_v8_epl_research_batch_v1",
        "Invalid batch schema",
    )

    require(
        batch.get("production_eligible") is False,
        "Unexpected production authorization",
    )

    require(
        batch.get("candidate_results_authoritative")
        is False,
        "Unexpected candidate authorization",
    )

    require(
        batch.get("kickoff_verified") is False,
        "Unexpected batch kickoff flag",
    )

    audits = sorted(
        DATA.glob("kickoff_audit_*.json"),
        reverse=True,
    )

    require(
        bool(audits),
        "No kickoff audits",
    )

    audit_path = None
    audit_raw = None
    audit = None

    for path in audits:

        raw = path.read_bytes()
        candidate = json.loads(raw)

        if (
            candidate.get("batch_filename")
            == batch_path.name
            and candidate.get("batch_sha256")
            == sha(batch_raw)
        ):
            audit_path = path
            audit_raw = raw
            audit = candidate
            break

    require(
        audit is not None,
        "No audit matching the selected batch",
    )

    require(
        audit.get("schema")
        == "bubus_v8_epl_kickoff_audit_v1",
        "Invalid audit schema",
    )

    require(
        audit.get("calendar_checked") is True,
        "Calendar was not checked",
    )

    require(
        audit.get("historical_results_verified")
        is False,
        "Unexpected historical verification flag",
    )

    require(
        audit.get("production_eligible") is False,
        "Unexpected audit authorization",
    )

    forecasts = batch["forecasts"]
    matches = audit["matches"]

    require(
        len(forecasts) == batch["forecast_count"],
        "Invalid forecast count",
    )

    require(
        len(matches) == audit["match_count"],
        "Invalid audit count",
    )

    require(
        len(forecasts) == 30
        and len(matches) == 30,
        "Unexpected fixture count",
    )

    audit_index = {
        str(row["match_id"]): row
        for row in matches
    }

    require(
        len(audit_index) == len(matches),
        "Duplicate audit match identity",
    )

    batch_created = parse_utc(
        batch["created_utc"]
    )

    audit_created = parse_utc(
        audit["created_utc"]
    )

    now = datetime.now(timezone.utc)

    candidates = []
    seen = set()
    provider_ids = set()
    existing_early = 0

    for forecast in forecasts:

        match_id = str(forecast["match_id"])

        require(
            match_id not in seen,
            "Duplicate forecast",
        )

        seen.add(match_id)

        require(
            forecast["competition"] == "EPL",
            "Unexpected competition",
        )

        require(
            forecast["production_eligible"] is False,
            "Unexpected forecast authorization",
        )

        # V8_EPL_EXISTING_EARLY_CHECK
        # Preserve the original immutable observation.
        # Do not compare it against a newer calendar.
        identity = hashlib.sha256(
            f"EPL:{match_id}".encode()
        ).hexdigest()[:24]

        existing_path = (
            OBSERVATIONS / f"epl_{identity}.json"
        )

        if existing_path.exists():
            saved = json.loads(
                existing_path.read_bytes()
            )

            require(
                saved.get("schema")
                == "bubus_v8_epl_observation_v1",
                f"Existing schema conflict: {match_id}",
            )

            require(
                saved.get("competition") == "EPL"
                and str(saved.get("match_id")) == match_id,
                f"Existing identity conflict: {match_id}",
            )

            require(
                saved.get("production_eligible") is False,
                f"Existing production flag: {match_id}",
            )

            for field in ("home", "away"):
                require(
                    saved.get(field) == forecast[field],
                    f"Existing team conflict: {match_id}",
                )

            require(
                saved.get("batch_file")
                and saved.get("batch_sha256")
                and saved.get("registered_utc"),
                f"Incomplete observation: {match_id}",
            )

            original_batch = DATA / saved["batch_file"]

            require(
                original_batch.is_file(),
                f"Original batch missing: {match_id}",
            )

            require(
                hashlib.sha256(
                    original_batch.read_bytes()
                ).hexdigest() == saved["batch_sha256"],
                f"Original batch changed: {match_id}",
            )

            print(
                "EXISTS (IMMUTABLE):",
                forecast["home"],
                "—",
                forecast["away"],
            )

            existing_early += 1
            continue

        audit_row = audit_index.get(match_id)

        require(
            audit_row is not None,
            f"Missing audit: {match_id}",
        )

        require(
            audit_row["status"] == "CALENDAR_MATCHED",
            "Unmatched calendar",
        )

        for field in ("date", "home", "away"):

            require(
                forecast[field] == audit_row[field],
                f"Fixture mismatch: {match_id}: {field}",
            )

        provider_id = audit_row["provider_match_id"]

        require(
            provider_id not in provider_ids,
            "Duplicate provider match identity",
        )

        provider_ids.add(provider_id)

        kickoff = parse_utc(
            audit_row["kickoff_utc"]
        )

        require(
            batch_created < kickoff,
            "Forecast generated after kickoff",
        )

        require(
            audit_created < kickoff,
            "Calendar checked after kickoff",
        )

        probabilities = forecast["probabilities"]

        values = [
            probabilities["home"],
            probabilities["draw"],
            probabilities["away"],
        ]

        require(
            all(
                type(p) in (int, float)
                and math.isfinite(p)
                and 0 <= p <= 1
                for p in values
            ),
            "Invalid probabilities",
        )

        require(
            abs(sum(values) - 1) < 0.001,
            "Probability sum mismatch",
        )

        identity = hashlib.sha256(
            f"EPL:{match_id}".encode()
        ).hexdigest()[:24]

        target = (
            OBSERVATIONS /
            f"epl_{identity}.json"
        )

        record = {
            "schema": "bubus_v8_epl_observation_v1",

            "competition": "EPL",

            "match_id": match_id,

            "provider": "OpenLigaDB",

            "provider_match_id": provider_id,

            "home": forecast["home"],

            "away": forecast["away"],

            "date": forecast["date"],

            "kickoff_utc": kickoff.isoformat(),

            "forecast_created_utc":
                batch_created.isoformat(),

            "calendar_audited_utc":
                audit_created.isoformat(),

            "batch_file": batch_path.name,

            "batch_sha256": sha(batch_raw),

            "audit_file": audit_path.name,

            "audit_sha256": sha(audit_raw),

            "snapshot_id": batch["snapshot_id"],

            "candidate_sha256":
                batch["candidate_sha256"],

            "probabilities": probabilities,

            "expected_goals":
                forecast["expected_goals"],

            "most_likely_score":
                forecast["most_likely_score"],

            "over": forecast["over"],

            "btts": forecast["btts"],

            "candidate_results_authoritative": False,

            "historical_results_verified": False,

            "kickoff_finality_guaranteed": False,

            "production_eligible": False,

            "status": "RESEARCH_OBSERVATION",
        }

        candidates.append((target, record, kickoff))

    require(
        len(candidates) + existing_early == len(forecasts),
        "Incomplete candidate set",
    )

    print()
    print("Batch:", batch_path.name)
    print("Audit:", audit_path.name)
    print("Snapshot:", batch["snapshot_id"])

    print()
    print("=== REGISTRATION PLAN ===")

    new_count = 0
    existing_count = existing_early
    skipped_count = 0

    for target, record, kickoff in candidates:

        if target.exists():

            existing = json.loads(
                target.read_text(encoding="utf-8")
            )

            require(
                existing.get("schema")
                == "bubus_v8_epl_observation_v1"
                and existing.get("competition") == "EPL"
                and existing.get("match_id")
                == record["match_id"],
                f"Existing observation conflict: {target}",
            )

            print(
                "EXISTS:",
                record["home"],
                "—",
                record["away"],
            )

            existing_count += 1
            continue

        if now + MIN_LEAD >= kickoff:

            print(
                "SKIPPED: kickoff too close:",
                record["home"],
                "—",
                record["away"],
            )

            skipped_count += 1
            continue

        print(
            "NEW:",
            record["home"],
            "—",
            record["away"],
            kickoff.isoformat(),
        )

        new_count += 1

    print()
    print("New:", new_count)
    print("Existing:", existing_count)
    print("Skipped:", skipped_count)

    if not args.write:

        print()
        print("DRY RUN COMPLETE")
        print("No observations written")
        return

    OBSERVATIONS.mkdir(
        parents=True,
        exist_ok=True,
    )

    written = 0

    for target, record, kickoff in candidates:

        if target.exists():
            continue

        if datetime.now(timezone.utc) + MIN_LEAD >= kickoff:
            continue

        record["registered_utc"] = (
            datetime.now(timezone.utc).isoformat()
        )

        raw = json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")

        fd, temporary = tempfile.mkstemp(
            prefix=".observation-",
            dir=OBSERVATIONS,
        )

        try:

            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())

            os.chmod(temporary, 0o600)

            # Atomic publish without replacing an existing record.
            os.link(temporary, target)

            written += 1

        finally:

            if os.path.exists(temporary):
                os.unlink(temporary)

    print()
    print("=== REGISTRATION COMPLETE ===")
    print("New observations:", written)
    print("Production publication: DISABLED")
    print("Bundesliga cache: UNCHANGED")


if __name__ == "__main__":
    register()
