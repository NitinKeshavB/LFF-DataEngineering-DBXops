"""
Create the Unity Catalog functions that the policy assistant uses as tools.

A SQL file holds each function, with `{ai_schema}` and `{silver_schema}` tokens for the fully qualified schema of
the target (catalog, project id and, in dev, the developer suffix). The statement is run with Spark SQL, so no SQL
warehouse is needed. `CREATE OR REPLACE` makes a rerun harmless.

Run as a job task:
    create_uc_functions.py --catalog C --project-id P [--logical-env _dev] --sql <get_claim_status.sql> [--sql ...]
"""

from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path
from typing import (
    Callable,
    Mapping,
    Sequence,
)

TOKEN = re.compile(r"\{([a-z_]+)\}")
RunSql = Callable[[str], None]


def schema_tokens(catalog: str, project_id: str, logical_env: str) -> dict[str, str]:
    """Return the fully qualified schema names the SQL files refer to."""
    return {
        "ai_schema": f"{catalog}.{project_id}_ai{logical_env}",
        "silver_schema": f"{catalog}.{project_id}_silver{logical_env}",
    }


def render_sql(template: str, tokens: Mapping[str, str]) -> str:
    """Replace each `{name}` token; raise `ValueError` when the template uses a token that has no value."""
    unknown = sorted({name for name in TOKEN.findall(template) if name not in tokens})
    if unknown:
        raise ValueError(f"the SQL uses unknown token(s): {', '.join(unknown)}")
    return TOKEN.sub(lambda match: tokens[match.group(1)], template)


def spark_sql() -> RunSql:
    """Return a function that runs a statement on the job's Spark session (pyspark is provided by the job)."""
    session = importlib.import_module("pyspark.sql").SparkSession.builder.getOrCreate()
    return session.sql


def create_functions(paths: Sequence[Path], tokens: Mapping[str, str], run_sql: RunSql) -> list[str]:
    """Run each SQL file with its tokens filled; return the file names that ran."""
    ran = []
    for path in paths:
        run_sql(render_sql(path.read_text(encoding="utf-8"), tokens))
        ran.append(path.name)
    return ran


def main(argv: Sequence[str] | None = None, run_sql: RunSql | None = None) -> int:
    """Create the functions named by `--sql`; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--logical-env", default="")
    parser.add_argument("--sql", required=True, action="append", type=Path)
    args = parser.parse_args(argv)
    tokens = schema_tokens(args.catalog, args.project_id, args.logical_env)
    ran = create_functions(args.sql, tokens, run_sql or spark_sql())
    print(f"created functions from: {', '.join(ran)} in {tokens['ai_schema']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
