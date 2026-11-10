"""E2E Root Conftest."""

import sys
from pathlib import Path

# Ensure repo root is on sys.path
root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from tests.e2e.harness.fixtures import *  # noqa: F401, F403
