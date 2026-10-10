"""Tests for the shared content hash."""

from dbxops.hashing import (
    canonical_json,
    content_hash,
)


def test__hash_ignores_key_order_and_whitespace():
    """The same content hashes identically however its keys were ordered."""
    assert content_hash({"b": [1, 2], "a": {"y": 1, "x": 2}}) == content_hash({"a": {"x": 2, "y": 1}, "b": [1, 2]})


def test__hash_changes_with_content_and_list_order():
    """A changed value or a reordered list is a different document."""
    assert content_hash({"a": [1, 2]}) != content_hash({"a": [2, 1]})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test__canonical_json_is_compact_sorted_and_keeps_unicode():
    """Canonical text has sorted keys, no spaces, and does not escape non-ASCII."""
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
