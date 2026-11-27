#!/usr/bin/env python3
"""Read-only evaluation of immutable Bubus V8 European research forecasts."""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"

LEAGUES = {
    "EPL": ("football_v8_epl_shadow", 30),
    "LaLiga": ("football_v8_laliga_shadow", 22),
    "SerieA": ("football_v8_seriea_shadow", 20),
    "Ligue1": ("football_v8_ligue1_shadow", 18),
}

EPS = 1e-15


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    return json.loads(path.read_bytes())


def parse_utc(value):
    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    require(dt.tzinfo is not None, "Timezone missing")
    return dt.astimezone(timezone.utc)


def normalize_forecast(obs):
    if obs["competition"] == "Ligue1":
        forecast = obs["forecast"]
        batch_name = obs["forecast_batch_file"]
        batch_sha = obs["forecast_batch_sha256"]
    else:
        forecast = obs
        batch_name = obs["batch_file"]
        batch_sha = obs["batch_sha256"]

    require(
        obs["production_eligible"] is False,
        "Unexpected production observation",
    )

    require(
        forecast["match_id"] == obs["match_id"],
        "Forecast identity mismatch",
    )

    require(
        forecast["competition"] == obs["competition"],
        "Forecast competition mismatch",
    )

    for field in ("home", "away"):
        if forecast is not obs:
            require(
                forecast[field] == obs["forecast"][field],
                "Forecast team mismatch",
            )

    probabilities = forecast["probabilities"]

    for key in ("home", "draw", "away"):
        value = probabilities[key]
        require(
            isinstance(value, (int, float))
            and math.isfinite(value)
            and 0 <= value <= 1,
            "Invalid 1X2 probability",
        )

    require(
        abs(sum(probabilities.values()) - 1) < 1e-6,
        "1X2 probabilities do not sum to one",
    )

    for field in ("btts",):
        value = forecast[field]
        require(
            isinstance(value, (int, float))
            and math.isfinite(value)
            and 0 <= value <= 1,
            f"Invalid {field}",
        )

    over = forecast["over"]["2.5"]

    require(
        isinstance(over, (int, float))
        and math.isfinite(over)
        and 0 <= over <= 1,
        "Invalid Over 2.5 probability",
    )

    for key in ("home", "away"):
        value = forecast["expected_goals"][key]
        require(
            isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0,
            "Invalid expected goals",
        )

    score = forecast["most_likely_score"]

    require(
        isinstance(score, list)
        and len(score) == 2
        and all(type(v) is int and v >= 0 for v in score),
        "Invalid most likely score",
    )

    return forecast, batch_name, batch_sha


def load_observations(league, folder, expected):
    paths = sorted(
        (folder / "observations").glob("*.json")
    )

    require(
        len(paths) >= expected,
        f"{league}: expected at least {expected} observations, "
        f"found {len(paths)}",
    )

    observations = {}
    batch_hashes = {}

    for path in paths:
        obs = load_json(path)

        require(
            obs["competition"] == league,
            f"{league}: competition mismatch",
        )

        forecast, batch_name, batch_sha = (
            normalize_forecast(obs)
        )

        key = str(obs["match_id"])

        require(
            key not in observations,
            f"{league}: duplicate observation {key}",
        )

        batch_path = folder / batch_name

        require(
            batch_path.is_file(),
            f"{league}: missing original batch",
        )

        if batch_name not in batch_hashes:
            batch_hashes[batch_name] = sha256(
                batch_path.read_bytes()
            )

        require(
            batch_hashes[batch_name] == batch_sha,
            f"{league}: original forecast batch changed",
        )

        created = parse_utc(
            obs["forecast_created_utc"]
        )

        registered = parse_utc(
            obs["registered_utc"]
        )

        require(
            created <= registered,
            f"{league}: invalid forecast timeline",
        )

        observations[key] = {
            "observation": obs,
            "forecast": forecast,
            "file": path.name,
            "batch_sha256": batch_sha,
        }

    return observations


def extract_result(league, event):
    if league == "EPL":
        require(
            event["schema"]
            == "bubus_v8_epl_result_candidate_v1",
            "Unexpected EPL result schema",
        )

        require(
            event["status"] == "PROVIDER_RESULT_CANDIDATE",
            "Unexpected EPL result status",
        )

        require(
            event["independently_corroborated"] is False,
            "Unexpected EPL corroboration status",
        )

        return {
            "match_id": str(event["match_id"]),
            "home_score": event["home_score"],
            "away_score": event["away_score"],
            "grade": "PROVIDER_ONLY",
            "home": event["home"],
            "away": event["away"],
            "observation_file": None,
            "batch_sha256": None,
        }

    require(
        event["schema"]
        == "bubus_v8_europe_result_event_v1",
        "Unexpected Europe result schema",
    )

    identity = event["result_identity"]

    require(
        identity["competition"] == league,
        "Result identity competition mismatch",
    )

    require(
        identity["state_status"] == "FINISHED",
        "Result is not final",
    )

    return {
        "match_id": str(identity["match_id"]),
        "home_score": identity["home_score"],
        "away_score": identity["away_score"],
        "grade": event["result_grade"],
        "home": event["home"],
        "away": event["away"],
        "observation_file": event.get(
            "forecast_observation_file"
        ),
        "batch_sha256": event.get(
            "forecast_batch_sha256",
            event.get("batch_sha256"),
        ),
    }


def load_results(league, folder, observations):
    directory = (
        folder / "results"
        if league == "EPL"
        else folder / "result_events"
    )

    grouped = defaultdict(list)

    if not directory.exists():
        return {}, Counter()

    for path in sorted(directory.glob("*.json")):
        event = load_json(path)

        result = extract_result(league, event)
        match_id = result["match_id"]

        require(
            match_id in observations,
            f"{league}: unregistered result {match_id}",
        )

        original = observations[match_id]
        obs = original["observation"]
        forecast = original["forecast"]

        require(
            result["home"] == forecast["home"]
            and result["away"] == forecast["away"],
            f"{league}: result team mismatch {match_id}",
        )

        if result["observation_file"] is not None:
            require(
                result["observation_file"]
                == original["file"],
                f"{league}: observation reference mismatch",
            )

        if result["batch_sha256"] is not None:
            require(
                result["batch_sha256"]
                == original["batch_sha256"],
                f"{league}: result batch mismatch",
            )

        for field in ("home_score", "away_score"):
            value = result[field]

            require(
                type(value) is int
                and 0 <= value <= 30,
                f"{league}: invalid score",
            )

        if league == "EPL":
            require(
                str(event["provider_match_id"])
                == str(obs["provider_match_id"]),
                "EPL provider identity mismatch",
            )

            require(
                parse_utc(event["kickoff_utc"])
                == parse_utc(obs["kickoff_utc"]),
                "EPL kickoff identity mismatch",
            )

        grouped[match_id].append(result)

    accepted = {}
    diagnostics = Counter()

    for match_id, records in grouped.items():
        scores = {
            (r["home_score"], r["away_score"])
            for r in records
        }

        if len(scores) != 1:
            diagnostics["CONFLICTS"] += 1
            print(
                f"WARNING: {league} conflicting scores:",
                match_id,
                sorted(scores),
            )
            continue

        grades = {r["grade"] for r in records}

        require(
            grades <= {
                "PROVIDER_ONLY",
                "CORROBORATED_BY_ADAPTER",
            },
            f"{league}: unknown result grade",
        )

        chosen = dict(records[0])

        chosen["grade"] = (
            "CORROBORATED_BY_ADAPTER"
            if "CORROBORATED_BY_ADAPTER" in grades
            else "PROVIDER_ONLY"
        )

        accepted[match_id] = chosen

        diagnostics[chosen["grade"]] += 1

    return accepted, diagnostics


def outcome(home, away):
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def binary_brier(probability, actual):
    return (probability - actual) ** 2


def binary_logloss(probability, actual):
    p = min(max(probability, EPS), 1 - EPS)
    return -(
        actual * math.log(p)
        + (1 - actual) * math.log(1 - p)
    )


def evaluate_match(forecast, result):
    home = result["home_score"]
    away = result["away_score"]

    probabilities = forecast["probabilities"]

    actual = outcome(home, away)

    predicted = max(
        ("home", "draw", "away"),
        key=lambda key: probabilities[key],
    )

    # Multiclass Brier: sum of three squared errors.
    brier = sum(
        (
            probabilities[key]
            - (1 if actual == key else 0)
        ) ** 2
        for key in ("home", "draw", "away")
    )

    true_probability = min(
        max(probabilities[actual], EPS),
        1 - EPS,
    )

    over_actual = int(home + away > 2.5)
    over_probability = forecast["over"]["2.5"]

    btts_actual = int(home > 0 and away > 0)
    btts_probability = forecast["btts"]

    expected = forecast["expected_goals"]

    predicted_score = forecast["most_likely_score"]

    return {
        "accuracy_1x2": int(predicted == actual),
        "brier_1x2": brier,
        "logloss_1x2": -math.log(true_probability),

        "over_accuracy": int(
            (over_probability >= 0.5) == bool(over_actual)
        ),
        "over_brier": binary_brier(
            over_probability, over_actual
        ),
        "over_logloss": binary_logloss(
            over_probability, over_actual
        ),

        "btts_accuracy": int(
            (btts_probability >= 0.5) == bool(btts_actual)
        ),
        "btts_brier": binary_brier(
            btts_probability, btts_actual
        ),
        "btts_logloss": binary_logloss(
            btts_probability, btts_actual
        ),

        "goals_mae": (
            abs(expected["home"] - home)
            + abs(expected["away"] - away)
        ) / 2,

        "exact_score": int(
            predicted_score == [home, away]
        ),
    }


def summarize(name, records):
    if not records:
        print(f"{name}: no completed forecasts")
        return

    print(f"\n=== {name} ===")
    print("Evaluated matches:", len(records))

    labels = {
        "accuracy_1x2": "1X2 accuracy",
        "brier_1x2": "1X2 Brier",
        "logloss_1x2": "1X2 Log Loss",
        "over_accuracy": "Over 2.5 accuracy",
        "over_brier": "Over 2.5 Brier",
        "over_logloss": "Over 2.5 Log Loss",
        "btts_accuracy": "BTTS accuracy",
        "btts_brier": "BTTS Brier",
        "btts_logloss": "BTTS Log Loss",
        "goals_mae": "Goals MAE",
        "exact_score": "Exact score accuracy",
    }

    for key, label in labels.items():
        mean = sum(
            record[key] for record in records
        ) / len(records)

        if "accuracy" in key or key == "exact_score":
            print(f"{label}: {mean * 100:.2f}%")
        else:
            print(f"{label}: {mean:.5f}")


def main():
    print("=== BUBUS V8 EUROPE RESEARCH EVALUATION ===")
    print("MODE: STRICTLY READ ONLY")
    print("Timestamp:", datetime.now(timezone.utc).isoformat())

    total_expected = 0
    total_evaluated = 0

    all_metrics = []
    corroborated_metrics = []
    provider_metrics = []

    for league, (directory, expected) in LEAGUES.items():
        folder = STATE / directory

        observations = load_observations(
            league, folder, expected
        )

        results, diagnostics = load_results(
            league, folder, observations
        )

        league_metrics = []

        for match_id, result in results.items():
            forecast = observations[match_id]["forecast"]

            metrics = evaluate_match(forecast, result)

            league_metrics.append(metrics)
            all_metrics.append(metrics)

            if result["grade"] == "CORROBORATED_BY_ADAPTER":
                corroborated_metrics.append(metrics)
            else:
                provider_metrics.append(metrics)

        total_expected += len(observations)
        total_evaluated += len(league_metrics)

        print(f"\n=== {league} STATUS ===")
        print("Registered:", len(observations))
        print("Evaluated:", len(league_metrics))
        print(
            "Pending:",
            len(observations) - len(league_metrics),
        )
        print(
            "Corroborated:",
            diagnostics["CORROBORATED_BY_ADAPTER"],
        )
        print(
            "Provider only:",
            diagnostics["PROVIDER_ONLY"],
        )
        print("Conflicts:", diagnostics["CONFLICTS"])

        summarize(league, league_metrics)

    print("\n" + "=" * 60)
    print("=== EUROPE V8 TOTAL ===")
    print("=" * 60)

    print("Registered forecasts:", total_expected)
    print("Evaluated matches:", total_evaluated)
    print("Pending:", total_expected - total_evaluated)

    summarize("ALL RESULTS", all_metrics)

    summarize(
        "CORROBORATED RESULTS",
        corroborated_metrics,
    )

    summarize(
        "PROVIDER ONLY RESULTS",
        provider_metrics,
    )

    print("\nForecasts modified: NO")
    print("Results modified: NO")
    print("Telegram modified: NO")


if __name__ == "__main__":
    main()
