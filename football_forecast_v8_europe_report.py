#!/usr/bin/env python3
"""Daily JSON report for Bubus V8 European research."""

import argparse
import json
import os
import tempfile

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import football_forecast_v8_europe_evaluate as ev


ROOT = Path(__file__).resolve().parent

REPORT_DIR = (
    ROOT / "state/football_v8_europe_evaluation"
)

METRICS = (
    "accuracy_1x2",
    "brier_1x2",
    "logloss_1x2",
    "over_accuracy",
    "over_brier",
    "over_logloss",
    "btts_accuracy",
    "btts_brier",
    "btts_logloss",
    "goals_mae",
    "exact_score",
)


def aggregate(records):
    if not records:
        return {
            "sample_size": 0,
            "metrics": {
                key: None for key in METRICS
            },
        }

    return {
        "sample_size": len(records),
        "metrics": {
            key: sum(
                item[key] for item in records
            ) / len(records)
            for key in METRICS
        },
    }


def create_report():
    now = datetime.now(timezone.utc)

    report = {
        "schema": "bubus_v8_europe_evaluation_report_v1",
        "generated_utc": now.isoformat(),
        "mode": "RESEARCH",
        "production_eligible": False,
        "leagues": {},
        "total": {},
    }

    all_metrics = []
    all_corroborated = []
    all_provider = []

    total_registered = 0
    total_conflicts = 0

    for league, (directory, expected) in ev.LEAGUES.items():

        folder = ev.STATE / directory

        observations = ev.load_observations(
            league,
            folder,
            expected,
        )

        results, diagnostics = ev.load_results(
            league,
            folder,
            observations,
        )

        metrics = []
        corroborated = []
        provider = []

        for match_id, result in results.items():

            forecast = observations[
                match_id
            ]["forecast"]

            evaluated = ev.evaluate_match(
                forecast,
                result,
            )

            metrics.append(evaluated)
            all_metrics.append(evaluated)

            if (
                result["grade"]
                == "CORROBORATED_BY_ADAPTER"
            ):
                corroborated.append(evaluated)
                all_corroborated.append(evaluated)

            else:
                provider.append(evaluated)
                all_provider.append(evaluated)

        registered = len(observations)
        evaluated_count = len(metrics)
        conflicts = diagnostics["CONFLICTS"]

        total_registered += registered
        total_conflicts += conflicts

        report["leagues"][league] = {
            "registered": registered,
            "evaluated": evaluated_count,
            "pending": (
                registered
                - evaluated_count
                - conflicts
            ),
            "conflicts": conflicts,
            "all_results": aggregate(metrics),
            "corroborated": aggregate(
                corroborated
            ),
            "provider_only": aggregate(
                provider
            ),
        }

    report["total"] = {
        "registered": total_registered,
        "evaluated": len(all_metrics),
        "pending": (
            total_registered
            - len(all_metrics)
            - total_conflicts
        ),
        "conflicts": total_conflicts,
        "all_results": aggregate(
            all_metrics
        ),
        "corroborated": aggregate(
            all_corroborated
        ),
        "provider_only": aggregate(
            all_provider
        ),
    }

    ev.require(
        total_registered >= 90,
        "Unexpected research observation count",
    )

    ev.require(
        (
            report["total"]["evaluated"]
            + report["total"]["pending"]
            + report["total"]["conflicts"]
        ) == total_registered,
        "Report accounting mismatch",
    )

    return report, now


def atomic_write(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")

    fd, temporary = tempfile.mkstemp(
        prefix=".report_",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(temporary, 0o600)

        os.replace(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        "=== BUBUS V8 EUROPE DAILY REPORT ==="
    )

    print(
        "MODE:",
        "WRITE" if args.write else "DRY RUN",
    )

    report, now = create_report()

    print()
    print("=== CHAMPIONSHIPS ===")

    for league, data in report["leagues"].items():
        print(
            league,
            "| registered:", data["registered"],
            "| evaluated:", data["evaluated"],
            "| pending:", data["pending"],
            "| conflicts:", data["conflicts"],
        )

    print()
    print("=== TOTAL ===")

    for key in (
        "registered",
        "evaluated",
        "pending",
        "conflicts",
    ):
        print(
            key + ":",
            report["total"][key],
        )

    print(
        "Corroborated:",
        report["total"]["corroborated"][
            "sample_size"
        ],
    )

    print(
        "Provider only:",
        report["total"]["provider_only"][
            "sample_size"
        ],
    )

    if not args.write:
        print()
        print("DRY RUN COMPLETE")
        print("Files modified: NO")
        return

    timestamp = now.strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    history = (
        REPORT_DIR
        / "history"
        / f"evaluation_{timestamp}.json"
    )

    latest = REPORT_DIR / "latest.json"

    ev.require(
        not history.exists(),
        "History file already exists",
    )

    atomic_write(history, report)
    atomic_write(latest, report)

    print()
    print("=== SAVED ===")
    print("History:", history)
    print("Latest:", latest)

    print("Forecasts modified: NO")
    print("Results modified: NO")
    print("Telegram modified: NO")


if __name__ == "__main__":
    main()
