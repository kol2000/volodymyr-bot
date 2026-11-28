#!/usr/bin/env python3
"""Bubus Europe Integration Architecture Audit V1.

Inspect existing prediction and snapshot code.

STRICTLY READ ONLY.
No imports of inspected project modules.
No database writes.
No forecasts.
"""

import ast
from pathlib import Path


FILES = {
    "football_forecast_live_v7.py": [
        "source_snapshot",
        "load_matches",
        "forecast_at",
        "rating_before",
        "fixtures_for",
        "record",
        "main",
    ],

    "football_europe_snapshot_v1.py": [
        "validate_fixture_rows",
        "validate_all",
        "build_manifest",
        "save_snapshot",
        "main",
    ],

    "football_europe_update_v1.py": [
        "fetch",
        "parse",
        "result_index",
        "compare_league",
        "compare_fixtures",
        "main",
    ],

    "football_forward_journal_v14.py": [
        "open_db",
        "validate",
        "register",
        "do_import",
        "add_result",
        "main",
    ],

    "football_compare_v2.py": [
        "candidate_prediction",
    ],

    "football_compare_v3.py": [
        "candidate_prediction",
    ],
}


def inspect(path, selected):

    print()
    print("=" * 75)
    print("FILE:", path)
    print("=" * 75)

    if not path.is_file():
        print("NOT FOUND")
        return

    source = path.read_text(
        encoding="utf-8"
    )

    lines = source.splitlines()

    tree = ast.parse(source)

    print("Total lines:", len(lines))

    print("\n=== TOP-LEVEL CONSTANTS ===")

    for node in tree.body:

        if not isinstance(
            node,
            (ast.Assign, ast.AnnAssign),
        ):
            continue

        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )

        for target in targets:

            if not isinstance(
                target,
                ast.Name,
            ):
                continue

            if target.id not in {
                "BASE",
                "ROOT",
                "DB",
                "SOURCES",
                "LEAGUES",
                "COMPETITIONS",
                "SCHEMA",
            }:
                continue

            try:
                value = ast.unparse(
                    node.value
                )
            except Exception:
                value = "<unavailable>"

            print(
                target.id,
                "=",
                value[:300],
            )

    print("\n=== SELECTED FUNCTIONS ===")

    found = set()

    for node in tree.body:

        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue

        if node.name not in selected:
            continue

        found.add(node.name)

        print()
        print("-" * 65)
        print(
            "FUNCTION:",
            node.name,
        )
        print("-" * 65)

        start = node.lineno
        end = node.end_lineno

        print(
            f"Lines: {start}-{end}"
        )

        for number in range(
            start,
            end + 1,
        ):

            print(
                f"{number:4d}: "
                f"{lines[number - 1]}"
            )

    missing = [
        name
        for name in selected
        if name not in found
    ]

    if missing:

        print("\nNOT FOUND:", missing)


def main():

    print(
        "=== BUBUS EUROPE INTEGRATION AUDIT V1 ==="
    )

    print("MODE: READ ONLY")

    for filename, functions in FILES.items():

        inspect(
            Path(filename),
            functions,
        )

    print()
    print("=" * 75)

    print("=== FINAL STATUS ===")
    print("SOURCE INSPECTION COMPLETED")
    print("NO MODULE FUNCTIONS EXECUTED")
    print("NO DATABASE WRITES")
    print("NO FORECASTS CREATED")


if __name__ == "__main__":
    main()
