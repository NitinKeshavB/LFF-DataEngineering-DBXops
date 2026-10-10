"""
Generate synthetic insurance change data (Faker) into the landing volume, one JSON-lines file per entity.

Each run is one batch. Batch 1 inserts parties, policies, coverages, premiums, claims and claim payments;
later batches insert new rows, update some existing ones and delete a few, so the silver layer's SCD1 and
SCD2 flows have real history to apply. Every record carries `_op` (I, U, D) and `_seq` (a number that only
increases), which the dataflow specs use as the CDC operation and sequence.

The batch number is kept in `<output>/_state/batch`, so repeated runs continue where the last stopped.
Generation is deterministic for a given seed and batch number.

Run as a job task:  generate_data.py --output /Volumes/<catalog>/<schema>/landing [--rows 200] [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker

ENTITIES = ("party", "policy", "coverage", "premium", "claim", "claim_payment")
APPEND_ONLY = frozenset({"premium", "claim_payment"})  # silver appends these, so they only ever get inserts
STATES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS")
PRODUCTS = ("MOTOR", "HOME", "CTP", "TRAVEL")
CAUSES = ("COLLISION", "THEFT", "STORM", "FIRE", "WATER_DAMAGE", "LIABILITY")
COVERAGE_TYPES = ("COMPREHENSIVE", "THIRD_PARTY", "BUILDING", "CONTENTS", "LIABILITY")
UPDATE_SHARE = 0.2
DELETE_SHARE = 0.02


def money(value: float) -> str:
    """Return a monetary amount with two decimals as a string, which Auto Loader casts to decimal(18,2)."""
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _fake(seed: int, batch: int) -> tuple[Faker, random.Random]:
    fake = Faker("en_AU")
    fake.seed_instance(seed * 1000 + batch)
    return fake, random.Random(seed * 1000 + batch)


def _ops(rng: random.Random, batch: int, rows: int, append_only: bool) -> list[tuple[str, int]]:
    """Return (op, index) pairs: new rows are inserts; later batches also update and delete earlier rows."""
    start = (batch - 1) * rows
    operations = [("I", start + i) for i in range(rows)]
    if batch > 1 and not append_only:
        existing = list(range(start))
        updates = rng.sample(existing, min(len(existing), max(1, int(rows * UPDATE_SHARE))))
        deletes = rng.sample(existing, min(len(existing), max(1, int(rows * DELETE_SHARE))))
        operations += [("U", i) for i in updates if i not in deletes] + [("D", i) for i in deletes]
    return operations


def _party(fake: Faker, rng: random.Random, index: int, now: datetime) -> dict[str, Any]:
    return {
        "party_id": f"P{index:07d}",
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "date_of_birth": fake.date_of_birth(minimum_age=18, maximum_age=85).isoformat(),
        "email": fake.email(),
        "postcode": fake.postcode(),
        "state": rng.choice(STATES),
        "created_at": now.isoformat(),
    }


def _policy(fake: Faker, rng: random.Random, index: int, rows_per_batch: int) -> dict[str, Any]:
    start = fake.date_between(start_date="-3y", end_date="today")
    return {
        "policy_id": f"POL{index:07d}",
        "party_id": f"P{rng.randrange(max(rows_per_batch, index + 1)):07d}",
        "product": rng.choice(PRODUCTS),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=365)).isoformat(),
        "status": rng.choice(("ACTIVE", "ACTIVE", "ACTIVE", "LAPSED", "CANCELLED")),
        "sum_insured": money(rng.uniform(10_000, 900_000)),
        "annual_premium": money(rng.uniform(300, 6_000)),
    }


def _coverage(rng: random.Random, index: int) -> dict[str, Any]:
    return {
        "coverage_id": f"COV{index:07d}",
        "policy_id": f"POL{index:07d}",
        "coverage_type": rng.choice(COVERAGE_TYPES),
        "limit_amount": money(rng.uniform(5_000, 800_000)),
        "excess_amount": money(rng.choice((250, 500, 750, 1000, 2000))),
    }


def _premium(fake: Faker, rng: random.Random, index: int, now: datetime) -> dict[str, Any]:
    period_start = fake.date_between(start_date="-18m", end_date="today").replace(day=1)
    written = rng.uniform(25, 500)
    return {
        "premium_id": f"PRM{index:07d}",
        "policy_id": f"POL{index:07d}",
        "period_start": period_start.isoformat(),
        "period_end": (period_start + timedelta(days=30)).isoformat(),
        "written_premium": money(written),
        "earned_premium": money(written * rng.uniform(0.5, 1.0)),
        "billed_at": now.isoformat(),
    }


def _claim(fake: Faker, rng: random.Random, index: int) -> dict[str, Any]:
    loss: date = fake.date_between(start_date="-18m", end_date="today")
    return {
        "claim_id": f"CLM{index:07d}",
        "policy_id": f"POL{index:07d}",
        "loss_date": loss.isoformat(),
        "reported_date": (loss + timedelta(days=rng.randrange(0, 10))).isoformat(),
        "status": rng.choice(("OPEN", "OPEN", "SETTLED", "CLOSED", "DENIED")),
        "cause": rng.choice(CAUSES),
        "reserve_amount": money(rng.uniform(0, 40_000)),
        "state": rng.choice(STATES),
    }


def _payment(rng: random.Random, index: int, now: datetime) -> dict[str, Any]:
    return {
        "payment_id": f"PAY{index:07d}",
        "claim_id": f"CLM{index:07d}",
        "paid_at": now.isoformat(),
        "amount": money(rng.uniform(50, 25_000)),
        "payment_type": rng.choice(("REPAIR", "REPLACEMENT", "MEDICAL", "LEGAL")),
    }


def generate_batch(seed: int, batch: int, rows: int, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Return one batch of change records per entity, each with `_op` and an increasing `_seq`."""
    fake, rng = _fake(seed, batch)
    builders = {
        "party": lambda i: _party(fake, rng, i, now),
        "policy": lambda i: _policy(fake, rng, i, rows),
        "coverage": lambda i: _coverage(rng, i),
        "premium": lambda i: _premium(fake, rng, i, now),
        "claim": lambda i: _claim(fake, rng, i),
        "claim_payment": lambda i: _payment(rng, i, now),
    }
    base = int(now.timestamp() * 1000) * 1000
    batch_data: dict[str, list[dict[str, Any]]] = {}
    for entity in ENTITIES:
        records: list[dict[str, Any]] = []
        for op, index in _ops(rng, batch, rows, entity in APPEND_ONLY):
            record = builders[entity](index)
            records.append({**record, "_op": op, "_seq": base + len(records)})
        batch_data[entity] = records
    return batch_data


def next_batch(output: Path) -> int:
    """Read, increment and store the batch counter under `<output>/_state/batch`; return the new batch number."""
    state = output / "_state" / "batch"
    current = int(state.read_text(encoding="utf-8")) if state.exists() else 0
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(str(current + 1), encoding="utf-8")
    return current + 1


def write_batch(output: Path, batch_data: dict[str, list[dict[str, Any]]], batch: int) -> list[Path]:
    """Write each entity's records as `<output>/<entity>/batch_<n>.json` (JSON lines); return the files."""
    written = []
    for entity, records in batch_data.items():
        path = output / entity / f"batch_{batch:05d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """Generate and write one batch; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument(
        "--output", required=True, type=Path, help="landing directory, e.g. /Volumes/<catalog>/<schema>/landing"
    )
    parser.add_argument("--rows", type=int, default=200, help="new rows per entity per batch")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    batch = next_batch(args.output)
    files = write_batch(args.output, generate_batch(args.seed, batch, args.rows, datetime.now(timezone.utc)), batch)
    print(f"batch {batch}: wrote {len(files)} files under {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
