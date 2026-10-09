"""
An example pytest fixture. Replace it with fixtures for your own code.

Fixture modules are registered in `tests/conftest.py` via `pytest_plugins`.
A module that is not listed there is never imported, so its fixtures are never
discovered -- which looks exactly like a typo in the fixture name.
"""

import pytest


@pytest.fixture
def example_value() -> str:
    """Return a value a test can assert on."""
    return "example"
