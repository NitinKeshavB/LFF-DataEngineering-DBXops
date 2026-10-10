"""
Score every claim with the champion model and write the scores table.

The model was logged with its feature lineage (train_model.py), so ``fe.score_batch`` takes only the claim ids and
joins the features itself. The table is rewritten on every run: one row per claim with the fraud probability, the
model version that produced it and the scoring time.

Run as a job task:  score_batch.py --features-table <catalog.schema.table> --model <catalog.schema.model>
                                   --scores-table <catalog.schema.table>
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from typing import (
    Any,
    Sequence,
)

KEY = "claim_id"
CHAMPION = "champion"


@dataclass(frozen=True)
class ScoringConfig:
    """The tables and the model of a scoring run."""

    features_table: str
    model_name: str
    scores_table: str


def score_champion(spark: Any, fe: Any, client: Any, config: ScoringConfig) -> int:
    """
    Score all claims with the model's champion version and overwrite the scores table.

    ``client`` is an MlflowClient on the Unity Catalog registry. Returns the champion version that was used.
    """
    version = int(client.get_model_version_by_alias(config.model_name, CHAMPION).version)
    scored = fe.score_batch(
        model_uri=f"models:/{config.model_name}@{CHAMPION}",
        df=spark.table(config.features_table).select(KEY),
        result_type="double",
    )
    result = scored.selectExpr(
        KEY, "prediction AS fraud_score", f"{version} AS model_version", "current_timestamp() AS scored_at"
    )
    result.write.mode("overwrite").saveAsTable(config.scores_table)
    return version


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--features-table", required=True, help="catalog.schema.table of the feature table")
    parser.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    parser.add_argument("--scores-table", required=True, help="catalog.schema.table the scores are written to")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Score on the job's Spark session."""
    args = build_parser().parse_args(argv)
    mlflow_tracking = importlib.import_module("mlflow.tracking")
    engineering = importlib.import_module("databricks.feature_engineering")
    spark = importlib.import_module("pyspark.sql").SparkSession.builder.getOrCreate()
    fe = engineering.FeatureEngineeringClient(model_registry_uri="databricks-uc")
    client = mlflow_tracking.MlflowClient(registry_uri="databricks-uc")
    version = score_champion(spark, fe, client, ScoringConfig(args.features_table, args.model, args.scores_table))
    print(f"{args.scores_table}: scored with {args.model} version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
