#!/usr/bin/env python3
"""Bubus Forecast V8 dynamic context prototype.

Uses validated European snapshots.

Strictly offline.
No database writes.
No Telegram publication.

Research prototype only.
Not authorized for production integration.
"""

from datetime import date, datetime, timezone
from types import FunctionType, SimpleNamespace
from zoneinfo import ZoneInfo

import football_forecast_research_v8_1 as research
import football_forecast_data_live_v1 as loader


MOSCOW = ZoneInfo("Europe/Moscow")


def build_context(as_of=None):
    """Build V8 context using a validated CSV snapshot.

    The original research module is not modified.

    as_of is a date-based cutoff, NOT a precise
    historical information-availability timestamp.
    """

    if as_of is None:
        as_of = datetime.now(MOSCOW).date()

    if not isinstance(as_of, date) or isinstance(
        as_of, datetime
    ):
        raise TypeError("as_of must be datetime.date")

    print("=== BUBUS V8 DYNAMIC CONTEXT V1 ===")
    print("MODE: OFFLINE / RESEARCH ONLY")
    print("Requested as_of:", as_of)

    # Load validated European season data.
    season = loader.load_current_matches()

    fixtures = season["matches"]
    counts = season["counts"]

    # Create an isolated global namespace for the
    # existing research build_context function.
    #
    # No assignments are made to research.AS_OF,
    # research.adapter or other module globals.

    environment = dict(research.build_context.__globals__)

    environment["AS_OF"] = as_of

    environment["adapter"] = SimpleNamespace(
        load_all=lambda: (fixtures, counts)
    )

    isolated_build = FunctionType(
        research.build_context.__code__,
        environment,
        name="isolated_build_context",
        argdefs=research.build_context.__defaults__,
        closure=research.build_context.__closure__,
    )

    context = isolated_build()

    # Validate the resulting date-based history.

    combined = context["combined"]

    if not combined:
        raise RuntimeError("Combined history is empty")

    if any(match.day >= as_of for match in combined):
        raise RuntimeError(
            "Result at or after cutoff detected"
        )

    identities = set()

    for match in combined:

        key = (
            match.league,
            match.day,
            match.home,
            match.away,
        )

        if key in identities:
            raise RuntimeError(
                f"Duplicate historical match: {key}"
            )

        identities.add(key)

    print()
    print("=== DYNAMIC CONTEXT VALIDATION ===")
    print("Combined matches:", len(combined))
    print("Upcoming candidates:", len(context["upcoming"]))
    print("Duplicates: 0")
    print("Results on/after cutoff: 0")

    return {
        "context": context,
        "snapshot_id": season["snapshot_id"],
        "snapshot_created_utc": (
            season["snapshot_created_utc"]
        ),
        "as_of": as_of,
        "history_count": len(combined),
    }


def main():

    print("=== BASELINE REPRODUCIBILITY TEST ===")

    # First reproduce the original V8 research cutoff.
    baseline = build_context(
        date(2026, 11, 23)
    )

    print()
    print("=== BASELINE ===")
    print("Snapshot:", baseline["snapshot_id"])
    print("History:", baseline["history_count"])

    if baseline["history_count"] != 5766:
        raise RuntimeError(
            "Baseline history differs from original V8: "
            f"{baseline['history_count']} != 5766"
        )

    print("BASELINE REPRODUCIBILITY: PASSED")

    print()
    print("=== DYNAMIC CUTOFF TEST ===")

    current_day = datetime.now(MOSCOW).date()

    current = build_context(current_day)

    print()
    print("=== DYNAMIC RESULT ===")
    print("Moscow date:", current_day)
    print("History:", current["history_count"])
    print(
        "Upcoming:",
        len(current["context"]["upcoming"])
    )

    print()
    print("=== FINAL STATUS ===")
    print("DYNAMIC CONTEXT: PASSED")
    print("BASELINE HISTORY: REPRODUCED")
    print("SNAPSHOT SOURCE: VALIDATED")

    print("POINT-IN-TIME HISTORY: NOT VERIFIED")
    print("RESULT AVAILABILITY TIMES: NOT VERIFIED")
    print("ADDITIONAL PROVIDER FRESHNESS: NOT VERIFIED")
    print("PRODUCTION READINESS: NOT VERIFIED")

    print("ORIGINAL V8 MODULE: UNCHANGED")
    print("NO NETWORK REQUESTS")
    print("NO DATABASE WRITES")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
