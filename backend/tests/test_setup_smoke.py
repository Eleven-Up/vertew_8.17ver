"""Sample smoke test to confirm the pytest + Hypothesis toolchain runs green.

This trivial test exists only to validate the test framework setup (Task 1).
Real property-based tests for the server core are added in later tasks.
"""

from hypothesis import given
from hypothesis import strategies as st


def test_pytest_runs():
    """A trivial example test so `pytest` runs green on a fresh checkout."""
    assert 1 + 1 == 2


@given(st.integers())
def test_hypothesis_runs(n):
    """A trivial Hypothesis-driven property to confirm Hypothesis is wired up."""
    assert n + 0 == n
