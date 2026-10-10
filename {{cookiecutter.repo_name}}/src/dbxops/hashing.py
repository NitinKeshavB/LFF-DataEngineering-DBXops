"""
Content hashing shared by the metadata loaders and the metadata database.

A definition document (contract, product, registration, ...) is identified by the sha256 of its
canonical JSON form, so the same content always hashes the same regardless of key order or YAML
formatting.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(document: Any) -> str:
    """Return the canonical JSON text of a document: sorted keys, no whitespace, UTF-8 safe."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(document: Any) -> str:
    """Return the hex sha256 of a document's canonical JSON."""
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()
