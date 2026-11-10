#!/usr/bin/env python3
"""E2E Test Runner for Hemory / Echoes Platform.
Executes opaque-box, requirement-driven tests across Tiers 1-4.
"""

from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
import pytest


def main():
    parser = argparse.ArgumentParser(description="Run Hemory E2E Test Suite")
    parser.add_argument(
        "--tier",
        type=int,
        choices=[1, 2, 3, 4],
        help="Run specific test tier (1: Features, 2: Boundaries, 3: Pairwise, 4: Scenarios)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    e2e_root = repo_root / "tests" / "e2e"

    pytest_args = []
    if args.verbose:
        pytest_args.append("-v")

    if args.tier == 1:
        target = e2e_root / "tier1_features"
    elif args.tier == 2:
        target = e2e_root / "tier2_boundaries"
    elif args.tier == 3:
        target = e2e_root / "tier3_pairwise"
    elif args.tier == 4:
        target = e2e_root / "tier4_scenarios"
    else:
        target = e2e_root

    pytest_args.append(str(target))

    print(f"============================================================")
    print(f"  Hemory E2E Test Runner")
    print(f"  Target: {target}")
    print(f"============================================================")

    exit_code = pytest.main(pytest_args)
    if exit_code == 0:
        print("\nAll targeted E2E tests PASSED successfully.")
    else:
        print(f"\nE2E tests failed with exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
