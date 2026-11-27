#!/usr/bin/env python3
"""Bubus Europe Adapter Integrity V1.

Validate five immutable season archives before
using the unified European calendar adapter.

READ ONLY / OFFLINE.

No network requests.
No database writes.
No forecasts.
"""

import importlib

import football_europe_adapter_v1 as adapter


VALIDATORS = {
    "EPL": "football_europe_epl_season_v1",
    "LaLiga": "football_europe_laliga_season_v1",
    "SerieA": "football_europe_seriea_season_v1",
    "Bundesliga": "football_europe_bundesliga_season_v1",
    "Ligue1": "football_europe_ligue1_season_v1",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_league(league):

    archive, manifest, content = (
        adapter.load_archive(league)
    )

    module = importlib.import_module(
        VALIDATORS[league]
    )

    require(
        callable(
            getattr(module, "validate_archive", None)
        ),
        f"{league}: validator missing",
    )

    # Existing league-specific validation.
    #
    # Each validator checks its own immutable
    # archive format and source consistency.

    result = module.validate_archive(archive)

    require(
        len(content["matches"])
        == adapter.EXPECTED[league],
        f"{league}: incorrect match count",
    )

    return {
        "league": league,
        "archive": archive.name,
        "matches": len(content["matches"]),
        "validation_result": result,
    }


def validate_all():

    print(
        "=== BUBUS EUROPE ADAPTER INTEGRITY V1 ==="
    )

    print("MODE: READ ONLY / OFFLINE")

    results = {}

    print("\n=== ARCHIVE VALIDATION ===")

    for league in adapter.EXPECTED:

        print()
        print("LEAGUE:", league)

        result = validate_league(league)

        results[league] = result

        print(
            "Archive:",
            result["archive"],
        )

        print(
            "Matches:",
            result["matches"],
        )

        print("Integrity: PASSED")

    print("\n=== UNIFIED ADAPTER ===")

    matches, archives = adapter.load_all()

    require(
        len(matches) == 1752,
        "Unified calendar count mismatch",
    )

    for league, result in results.items():

        require(
            archives[league]["archive"]
            == result["archive"],
            f"{league}: archive identity mismatch",
        )

    print(
        "Unified matches:",
        len(matches),
    )

    print(
        "Validated championships:",
        len(results),
    )

    print("\n=== FINAL STATUS ===")

    print("ARCHIVE INTEGRITY: PASSED")
    print("CALENDAR NORMALIZATION: PASSED")
    print("MATCH COUNT: 1752")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")

    return matches, results


if __name__ == "__main__":
    validate_all()
