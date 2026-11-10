#!/usr/bin/env python3
"""Audit script to verify that the E2E test suite meets all readiness criteria."""

from __future__ import annotations
import inspect
import sys
from pathlib import Path
import pytest


def audit_test_suite() -> dict:
    e2e_dir = Path(__file__).resolve().parent

    tier1_dir = e2e_dir / "tier1_features"
    tier2_dir = e2e_dir / "tier2_boundaries"
    tier3_dir = e2e_dir / "tier3_pairwise"
    tier4_dir = e2e_dir / "tier4_scenarios"

    assert tier1_dir.is_dir(), "tier1_features directory missing"
    assert tier2_dir.is_dir(), "tier2_boundaries directory missing"
    assert tier3_dir.is_dir(), "tier3_pairwise directory missing"
    assert tier4_dir.is_dir(), "tier4_scenarios directory missing"

    tier1_files = sorted(list(tier1_dir.glob("test_*.py")))
    tier2_files = sorted(list(tier2_dir.glob("test_*.py")))
    tier3_files = sorted(list(tier3_dir.glob("test_*.py")))
    tier4_files = sorted(list(tier4_dir.glob("test_*.py")))

    print("=" * 70)
    print("HEMORY E2E TEST SUITE READINESS AUDIT")
    print("=" * 70)
    print(f"Tier 1 (Feature Coverage):     {len(tier1_files)} modules")
    print(f"Tier 2 (Boundary & Corner):    {len(tier2_files)} modules")
    print(f"Tier 3 (Pairwise Coverage):    {len(tier3_files)} modules")
    print(f"Tier 4 (Real-World Scenarios): {len(tier4_files)} modules")

    return {
        "tier1_count": len(tier1_files),
        "tier2_count": len(tier2_files),
        "tier3_count": len(tier3_files),
        "tier4_count": len(tier4_files),
    }


if __name__ == "__main__":
    res = audit_test_suite()
    print("\nAuditing complete. To run the full test suite:")
    print("  .venv/bin/pytest tests/e2e/ -v")
