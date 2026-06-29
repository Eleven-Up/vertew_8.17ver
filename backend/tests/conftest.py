"""Shared pytest fixtures and Hypothesis profiles for the Vertew backend.

Property-based tests use Hypothesis. Per the design, each property test runs a
minimum of 100 iterations; the "ci" profile below enforces that explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, settings

# Make the backend package modules importable from tests (e.g. `import models`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Register a profile that satisfies the design's >=100 iterations requirement.
settings.register_profile(
    "vertew",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("vertew")
