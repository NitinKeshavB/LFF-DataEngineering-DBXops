"""
Example unit tests. Replace these with tests for your own code.

Keep this directory mirroring `src/dbxops/`, so
the tests for a module are findable from its path.
"""

import dbxops


def test__package_is_importable():
    """
    The package imports and carries a module docstring.

    Deliberately trivial, but not pointless: it fails if the package is not
    installed (`make install`), and it is what makes `make test-wheel-locally`
    meaningful -- that task tests the *installed* wheel, and a suite that
    collects no tests exits 5 and reports coverage of nothing.
    """
    assert dbxops.__doc__


def test__fixtures_are_injected_by_name(example_value: str):
    """A fixture registered in `tests/conftest.py` is injected by parameter name."""
    assert example_value == "example"
