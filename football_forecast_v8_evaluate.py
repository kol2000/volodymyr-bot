#!/usr/bin/env python3
"""Bubus Forecast V8 research evaluator.

Read-only.
One earliest valid V2 forecast per match.
Scores only matches with verified final results.
"""

import json
import math

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("state/football_v8_shadow")

OBSERVATIONS = BASE / "observations.jsonl"
RESULTS = BASE / "results.jsonl"

OUTCOMES = ("HOME", "DRAW", "AWAY")


def parse_time(value):

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if dt.tzinfo is None:
        raise ValueError("Timezone required")

    return dt.astimezone(timezone.utc)


def load_jsonl(path):

    if not path.exists():
        return []

    records = []

    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):

        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON: {path}:{number}"
            ) from exc

        if not isinstance(record, dict):
            raise RuntimeError(
                f"Invalid record: {path}:{number}"
            )

        records.append(record)

    return records


def validate_probabilities(row):

    probabilities = row.get("v8_probabilities")

    if not isinstance(probabilities, dict):
        raise ValueError("Missing probabilities")

    values = []

    for name in ("home", "draw", "away"):

        value = probabilities.get(name)

        if type(value) not in (int, float):
            raise ValueError("Invalid probability type")

        if not math.isfinite(value):
            raise ValueError("Non-finite probability")

        if not 0 <= value <= 100:
            raise ValueError("Probability out of range")

        values.append(value / 100.0)

    if abs(sum(values) - 1.0) > 0.001:
        raise ValueError("Probability sum mismatch")

    return values


def select_forecasts(observations):

    groups = defaultdict(list)

    rejected = Counter()

    for row in observations:

        if row.get("schema") != "bubus_v8_observation_v2":
            continue

        try:

            match_id = row["match_id"]

            created = parse_time(row["created_utc"])
            kickoff = parse_time(row["kickoff_utc"])

            if created >= kickoff:
                raise ValueError("Forecast after kickoff")

            probabilities = validate_probabilities(row)

            if row.get("published_v8") is not False:
                raise ValueError("Unexpected publication flag")

            groups[match_id].append((
                created,
                row,
                probabilities,
            ))

        except (KeyError, TypeError, ValueError) as exc:

            rejected[str(exc)] += 1

    selected = {}

    for match_id, candidates in groups.items():

        candidates.sort(
            key=lambda item: item[0]
        )

        # Earliest registered V2 observation wins.
        selected[match_id] = candidates[0]

    return selected, rejected


def validate_results(records):

    results = {}

    for row in records:

        if row.get("schema") != "bubus_v8_result_v1":
            raise RuntimeError("Unsupported result schema")

        match_id = row["match_id"]

        home = row["home_goals"]
        away = row["away_goals"]

        if not (
            type(home) is int
            and type(away) is int
            and 0 <= home <= 30
            and 0 <= away <= 30
        ):
            raise RuntimeError(
                f"Invalid final score: {match_id}"
            )

        outcome = (
            "HOME" if home > away
            else "AWAY" if away > home
            else "DRAW"
        )

        if row.get("outcome") != outcome:
            raise RuntimeError(
                f"Outcome conflict: {match_id}"
            )

        if match_id in results:
            raise RuntimeError(
                f"Duplicate result: {match_id}"
            )

        parse_time(row["kickoff_utc"])
        parse_time(row["collected_utc"])

        results[match_id] = row

    return results


def calculate_metrics(probabilities, outcome):

    actual = [
        1.0 if name == outcome else 0.0
        for name in OUTCOMES
    ]

    brier = sum(
        (prediction - target) ** 2
        for prediction, target in zip(
            probabilities,
            actual,
        )
    )

    outcome_index = OUTCOMES.index(outcome)

    log_loss = -math.log(
        max(probabilities[outcome_index], 1e-15)
    )

    predicted_index = max(
        range(3),
        key=lambda index: probabilities[index],
    )

    correct = (
        OUTCOMES[predicted_index] == outcome
    )

    return brier, log_loss, correct


def evaluate():

    print("=== BUBUS FORECAST V8 EVALUATOR ===")
    print("MODE: READ ONLY")

    observations = load_jsonl(OBSERVATIONS)
    result_rows = load_jsonl(RESULTS)

    forecasts, rejected = select_forecasts(
        observations
    )

    results = validate_results(result_rows)

    print("\n=== INPUT ===")

    print("Observation records:", len(observations))
    print("Unique valid V2 forecasts:", len(forecasts))
    print("Verified results:", len(results))

    print("\n=== FORECAST VALIDATION ===")

    for reason, count in sorted(rejected.items()):
        print(reason, ":", count)

    evaluations = []

    for match_id, result in sorted(results.items()):

        forecast = forecasts.get(match_id)

        if forecast is None:
            print(
                "SKIPPED: result without forecast:",
                match_id,
            )
            continue

        created, row, probabilities = forecast

        forecast_kickoff = parse_time(
            row["kickoff_utc"]
        )

        result_kickoff = parse_time(
            result["kickoff_utc"]
        )

        if abs(
            (
                forecast_kickoff - result_kickoff
            ).total_seconds()
        ) > 60:

            print(
                "BLOCKED: kickoff mismatch:",
                match_id,
            )
            continue

        outcome = result["outcome"]

        brier, log_loss, correct = (
            calculate_metrics(
                probabilities,
                outcome,
            )
        )

        evaluations.append({
            "match_id": match_id,
            "home": row["home"],
            "away": row["away"],
            "score": (
                result["home_goals"],
                result["away_goals"],
            ),
            "outcome": outcome,
            "created": created.isoformat(),
            "brier": brier,
            "log_loss": log_loss,
            "correct": correct,
        })

    print("\n=== MATCH EVALUATIONS ===")

    for item in evaluations:

        print()
        print(
            item["home"],
            "—",
            item["away"],
        )

        print(
            "Score:",
            f'{item["score"][0]}:{item["score"][1]}',
        )

        print("Outcome:", item["outcome"])

        print(
            "Accuracy:",
            "HIT" if item["correct"] else "MISS",
        )

        print(
            "Brier:",
            round(item["brier"], 5),
        )

        print(
            "Log Loss:",
            round(item["log_loss"], 5),
        )

    total = len(evaluations)

    print("\n=== V8 PERFORMANCE ===")

    print("Evaluated matches:", total)

    if total:

        accuracy = (
            sum(item["correct"] for item in evaluations)
            / total
        )

        average_brier = (
            sum(item["brier"] for item in evaluations)
            / total
        )

        average_log_loss = (
            sum(item["log_loss"] for item in evaluations)
            / total
        )

        print(
            "Accuracy:",
            f"{accuracy * 100:.2f}%",
        )

        print(
            "Brier Score:",
            f"{average_brier:.6f}",
        )

        print(
            "Log Loss:",
            f"{average_log_loss:.6f}",
        )

    else:

        print("No finished matches available.")
        print("Metrics: PENDING")

    print("\n=== SAFETY ===")

    print("One forecast per match: YES")
    print("Original observations modified: NO")
    print("Results modified: NO")
    print("Telegram messages: NO")


if __name__ == "__main__":
    evaluate()
