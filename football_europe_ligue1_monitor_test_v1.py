#!/usr/bin/env python3
"""Bubus Ligue 1 Monitor Mutation Tests V1.

Tests real calendar normalization and Monitor V1
comparison using archived source files.

STRICTLY OFFLINE / READ ONLY.

No network requests.
No archive writes.
No observation writes.
No forecast recording.
"""

import copy
import csv
import io

from pathlib import Path

import football_europe_ligue1_monitor_v1 as monitor
import football_europe_ligue1_season_v1 as season
import football_europe_ligue1_calendar_v1 as calendar
import football_europe_ligue1_season_audit_v1 as audit


def require(condition, message):

    if not condition:
        raise RuntimeError(message)


def csv_rows(raw):

    return list(
        csv.DictReader(
            io.StringIO(
                raw.decode("utf-8-sig")
            )
        )
    )


def encode_csv(rows):

    require(
        bool(rows),
        "Cannot encode empty CSV",
    )

    output = io.StringIO()

    writer = csv.DictWriter(
        output,
        fieldnames=list(rows[0]),
        lineterminator="\n",
    )

    writer.writeheader()
    writer.writerows(rows)

    return output.getvalue().encode("utf-8")


def load_sources():

    (
        archive,
        manifest,
        old_calendar,
        old_states,
        old_provider,
    ) = monitor.load_archive()

    raw = {
        name: (archive / name).read_bytes()
        for name in (
            "lfp_official.pdf",
            "fixtures_2627.csv",
            "results_2627.csv",
        )
    }

    return (
        raw,
        old_calendar,
        old_states,
        old_provider,
    )


def normalize(raw):

    return season.normalize_sources(raw)


def compare(old, new):

    return monitor.compare_entries(
        old,
        new,
    )


def assert_rejected(raw):

    rejected = False

    try:
        normalize(raw)
    except (
        RuntimeError,
        ValueError,
        KeyError,
    ):
        rejected = True

    require(
        rejected,
        "Invalid source was accepted",
    )


def main():

    print(
        "=== BUBUS LIGUE 1 MONITOR TEST V1 ==="
    )

    print("MODE: OFFLINE / READ ONLY")

    (
        raw,
        old_calendar,
        old_states,
        old_provider,
    ) = load_sources()

    print("\n=== TEST 1: BASELINE ===")

    content, states, provider = normalize(raw)

    require(
        calendar.dataset_id(content)
        == calendar.dataset_id(old_calendar),
        "Baseline Calendar mismatch",
    )

    require(
        calendar.dataset_id(states)
        == calendar.dataset_id(old_states),
        "Baseline States mismatch",
    )

    require(
        calendar.dataset_id(provider)
        == calendar.dataset_id(old_provider),
        "Baseline Provider mismatch",
    )

    print("PASS: baseline unchanged")

    print("\n=== TEST 2: NEW RESULT ===")

    changed = dict(raw)

    fixtures = csv_rows(
        changed["fixtures_2627.csv"]
    )

    results = csv_rows(
        changed["results_2627.csv"]
    )

    next_match = fixtures.pop(0)

    completed = dict.fromkeys(
        results[0].keys(),
        "",
    )

    completed.update(next_match)

    completed["ft_home"] = "2"
    completed["ft_away"] = "1"
    completed["ft_result"] = "H"

    results.append(completed)

    changed["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    changed["results_2627.csv"] = (
        encode_csv(results)
    )

    c2, s2, p2 = normalize(changed)

    require(
        calendar.dataset_id(c2)
        == calendar.dataset_id(content),
        "Calendar changed after result",
    )

    require(
        calendar.dataset_id(s2)
        != calendar.dataset_id(states),
        "State change not detected",
    )

    require(
        calendar.dataset_id(p2)
        == calendar.dataset_id(provider),
        "Provider changed after result",
    )

    events = compare(states, s2)

    require(
        len(events) == 1,
        "Expected one State event",
    )

    print("PASS: new result detected")

    print("\n=== TEST 3: PROVIDER TIME ===")

    changed = dict(raw)

    fixtures = csv_rows(
        changed["fixtures_2627.csv"]
    )

    target = fixtures[0]

    old_time = target["time"]

    target["time"] = (
        "22:45"
        if old_time != "22:45"
        else "21:45"
    )

    changed["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    c3, s3, p3 = normalize(changed)

    require(
        calendar.dataset_id(c3)
        == calendar.dataset_id(content),
        "Calendar changed after time update",
    )

    require(
        calendar.dataset_id(s3)
        == calendar.dataset_id(states),
        "States changed after time update",
    )

    require(
        calendar.dataset_id(p3)
        != calendar.dataset_id(provider),
        "Provider change not detected",
    )

    events = compare(provider, p3)

    require(
        len(events) == 1,
        "Expected one Provider event",
    )

    print("PASS: provider time detected")

    print("\n=== TEST 4: CALENDAR MUTATION ===")

    changed = dict(raw)

    fixtures = csv_rows(
        changed["fixtures_2627.csv"]
    )

    first = fixtures[0]

    first["home"], first["away"] = (
        first["away"],
        first["home"],
    )

    changed["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    assert_rejected(changed)

    print("PASS: calendar mutation blocked")

    print("\n=== TEST 5: INVALID SCORE ===")

    changed = dict(raw)

    results = csv_rows(
        changed["results_2627.csv"]
    )

    results[0]["ft_home"] = "-1"

    changed["results_2627.csv"] = (
        encode_csv(results)
    )

    assert_rejected(changed)

    print("PASS: invalid score blocked")

    print("\n=== TEST 6: DUPLICATE FIXTURE ===")

    changed = dict(raw)

    fixtures = csv_rows(
        changed["fixtures_2627.csv"]
    )

    fixtures.append(
        copy.deepcopy(fixtures[0])
    )

    changed["fixtures_2627.csv"] = (
        encode_csv(fixtures)
    )

    assert_rejected(changed)

    print("PASS: duplicate fixture blocked")

    print("\n=== FINAL STATUS ===")

    print("ALL 6 TESTS PASSED")
    print("CALENDAR SAFETY: VERIFIED")
    print("STATE DETECTION: VERIFIED")
    print("PROVIDER DETECTION: VERIFIED")
    print("INVALID DATA: REJECTED")
    print("NO NETWORK REQUESTS")
    print("NO FILES MODIFIED")
    print("FORECAST RECORDING: BLOCKED")


if __name__ == "__main__":
    main()
