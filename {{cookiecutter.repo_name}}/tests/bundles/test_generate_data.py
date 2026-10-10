"""Tests for the DataOps bundle's synthetic data generator (bundles/dataops/src/jobs/generate_data.py)."""

import importlib.util
import json
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import pytest

from tests.consts import PROJECT_DIR

SCRIPT = PROJECT_DIR / "bundles/dataops/src/jobs/generate_data.py"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def gen():
    """Import the generator script as a module (it is a job script, not part of the package)."""
    spec = importlib.util.spec_from_file_location("generate_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test__the_first_batch_is_all_inserts_with_unique_ids(gen):
    """Batch 1 creates `rows` new records per entity, every one an insert, with unique primary keys."""
    batch = gen.generate_batch(seed=7, batch=1, rows=30, now=NOW)
    assert set(batch) == set(gen.ENTITIES)
    for entity, records in batch.items():
        assert len(records) == 30 and {r["_op"] for r in records} == {"I"}, entity
    assert len({r["party_id"] for r in batch["party"]}) == 30


def test__later_batches_update_and_delete_cdc_entities_but_only_append_facts(gen):
    """Batch 2 has inserts, updates and deletes for party/policy/coverage/claim; premium and payments stay inserts."""
    batch = gen.generate_batch(seed=7, batch=2, rows=100, now=NOW)
    for entity in ("party", "policy", "coverage", "claim"):
        assert {r["_op"] for r in batch[entity]} == {"I", "U", "D"}, entity
    for entity in gen.APPEND_ONLY:
        assert {r["_op"] for r in batch[entity]} == {"I"}, entity


def test__updates_and_deletes_only_touch_earlier_batches(gen):
    """An update or delete refers to a party inserted in an earlier batch, never to one that does not exist yet."""
    batch = gen.generate_batch(seed=7, batch=3, rows=50, now=NOW)
    inserted = {int(r["party_id"][1:]) for r in batch["party"] if r["_op"] == "I"}
    changed = {int(r["party_id"][1:]) for r in batch["party"] if r["_op"] != "I"}
    assert min(inserted) == 100 and changed and max(changed) < min(inserted)


def test__sequence_numbers_increase_within_and_across_batches(gen):
    """`_seq` strictly increases inside a batch, and a later batch (later clock) always sorts after an earlier one."""
    first = gen.generate_batch(7, 1, 20, NOW)["claim"]
    second = gen.generate_batch(7, 2, 20, NOW.replace(minute=5))["claim"]
    seqs = [r["_seq"] for r in first]
    assert seqs == sorted(set(seqs)) and min(r["_seq"] for r in second) > max(seqs)


def test__generation_is_deterministic_for_a_seed_and_batch(gen):
    """The same seed, batch and clock give identical data; a different seed differs."""
    assert gen.generate_batch(7, 2, 40, NOW) == gen.generate_batch(7, 2, 40, NOW)
    assert gen.generate_batch(8, 2, 40, NOW) != gen.generate_batch(7, 2, 40, NOW)


def test__records_match_the_bronze_schemas_column_for_column(gen):
    """Every generated record has exactly the columns of its bronze schema file, plus `_op` and `_seq`."""
    batch = gen.generate_batch(7, 2, 20, NOW)
    for entity, records in batch.items():
        schema = json.loads(
            (PROJECT_DIR / f"bundles/dataops/src/dataflows/bronze/schemas/{entity}_schema.json").read_text()
        )
        expected = {f["name"] for f in schema["fields"]}
        assert all(set(r) == expected for r in records), entity


def test__monetary_values_are_two_decimal_strings(gen):
    """Money is written as a string with two decimals so Auto Loader casts it to decimal(18,2) exactly."""
    record = gen.generate_batch(7, 1, 5, NOW)["premium"][0]
    for field in ("written_premium", "earned_premium"):
        assert len(record[field].split(".")[1]) == 2
    assert float(record["earned_premium"]) <= float(record["written_premium"])


def test__batches_are_written_as_json_lines_and_the_counter_advances(gen, tmp_path: Path):
    """Each run writes one file per entity and moves the stored batch number on, so reruns continue."""
    assert gen.main(["--output", str(tmp_path), "--rows", "10"]) == 0
    assert gen.main(["--output", str(tmp_path), "--rows", "10"]) == 0
    assert (tmp_path / "_state/batch").read_text() == "2"
    files = sorted((tmp_path / "party").glob("batch_*.json"))
    assert [f.name for f in files] == ["batch_00001.json", "batch_00002.json"]
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert len(rows) == 10 and rows[0]["_op"] == "I"
