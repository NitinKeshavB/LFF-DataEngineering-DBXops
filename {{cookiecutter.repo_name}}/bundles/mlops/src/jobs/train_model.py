"""
Train the claims fraud model on the feature table and register it in Unity Catalog as the challenger.

The feature table (a materialized view keyed by claim_id) is joined with FeatureLookup, a gradient boosting
classifier is trained and scored on a held-out split, and the model is logged with MLflow through the Feature
Engineering client so it keeps its feature lineage. The held-out AUC is logged as the run metric ``auc``; the
promotion gate (src/gates/claims_fraud_model.gate.yml) reads it from the run. The new version gets the alias
``challenger``.

``build_training_frame``, ``split``, ``train`` and ``evaluate`` are pure functions of pandas objects;
``log_and_register`` and ``train_challenger`` take the Spark, Feature Engineering and MLflow objects as arguments;
only ``main`` imports the Databricks libraries.

Run as a job task:  train_model.py --features-table <catalog.schema.table> --model <catalog.schema.model>
                                   --experiment /Shared/<name> [--seed 7]
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Sequence,
)

import pandas as pd

KEY = "claim_id"
LABEL = "is_fraud_suspected"
FEATURES = (
    "claim_reserve",
    "days_loss_to_report",
    "policy_age_days",
    "sum_insured",
    "annual_premium",
    "reserve_to_sum_insured",
    "prior_claim_count",
    "payment_count",
    "total_paid",
)
CHALLENGER = "challenger"
TEST_FRACTION = 0.25


@dataclass(frozen=True)
class TrainingConfig:
    """What to train from and where to register it."""

    features_table: str
    model_name: str
    seed: int = 7


@dataclass(frozen=True)
class TrainedModel:
    """A fitted classifier with its held-out metrics and the parameters to log."""

    model: Any
    metrics: dict[str, float]
    params: dict[str, Any]


@dataclass(frozen=True)
class TrainingResult:
    """The registered model version, and the metrics the version was logged with."""

    model: str
    version: int
    metrics: dict[str, float] = field(default_factory=dict)


def build_training_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Return the numeric feature matrix and the integer label of a joined training frame.

    Rows without a label are dropped and missing feature values become 0. Raises ``ValueError`` when a column is
    missing or the label does not hold both classes.
    """
    missing = [name for name in (*FEATURES, LABEL) if name not in frame.columns]
    if missing:
        raise ValueError(f"the training frame lacks column(s): {', '.join(missing)}")
    labelled = frame.dropna(subset=[LABEL])
    features = labelled[list(FEATURES)].apply(pd.to_numeric, errors="coerce").astype(float).fillna(0.0)
    label = labelled[LABEL].astype(int)
    if label.nunique() < 2:
        raise ValueError(f"{LABEL} must hold both classes to train a classifier, found {sorted(label.unique())}")
    return features, label


def _sklearn(module: str) -> Any:
    return importlib.import_module(f"sklearn.{module}")


def split(
    features: pd.DataFrame, label: pd.Series, seed: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """Return a stratified ``(x_train, x_test, y_train, y_test)`` split."""
    return _sklearn("model_selection").train_test_split(
        features, label, test_size=TEST_FRACTION, random_state=seed, stratify=label
    )


def train(features: pd.DataFrame, label: pd.Series, seed: int = 7, n_estimators: int = 100) -> Any:
    """Fit and return a gradient boosting classifier."""
    classifier = _sklearn("ensemble").GradientBoostingClassifier(n_estimators=n_estimators, random_state=seed)
    return classifier.fit(features, label)


def evaluate(model: Any, features: pd.DataFrame, label: pd.Series) -> dict[str, float]:
    """Return the held-out ``auc`` and ``accuracy`` of a fitted classifier."""
    metrics = _sklearn("metrics")
    return {
        "auc": float(metrics.roc_auc_score(label, model.predict_proba(features)[:, 1])),
        "accuracy": float(metrics.accuracy_score(label, model.predict(features))),
    }


def log_and_register(mlflow: Any, fe: Any, trained: TrainedModel, training_set: Any, model_name: str) -> int:
    """
    Log the model with its feature lineage, register it in Unity Catalog and give the new version the challenger alias.

    ``mlflow`` is the mlflow module (with ``mlflow.sklearn`` imported) and ``fe`` a FeatureEngineeringClient.
    Returns the registered version.
    """
    with mlflow.start_run(run_name="train_claims_fraud_model"):
        mlflow.log_params(trained.params)
        mlflow.log_metrics(trained.metrics)
        fe.log_model(
            model=trained.model,
            artifact_path="model",
            flavor=mlflow.sklearn,
            training_set=training_set,
            registered_model_name=model_name,
        )
    client = mlflow.tracking.MlflowClient(registry_uri="databricks-uc")
    version = max(int(v.version) for v in client.search_model_versions(f"name='{model_name}'"))
    client.set_registered_model_alias(model_name, CHALLENGER, version)
    return version


def fit(frame: pd.DataFrame, seed: int = 7) -> TrainedModel:
    """Train on the stratified train split of a joined frame and evaluate on the held-out split."""
    features, label = build_training_frame(frame)
    x_train, x_test, y_train, y_test = split(features, label, seed)
    model = train(x_train, y_train, seed)
    params = {"seed": seed, "test_fraction": TEST_FRACTION, "features": ",".join(FEATURES)}
    return TrainedModel(model, evaluate(model, x_test, y_test), params)


def train_challenger(
    spark: Any, fe: Any, mlflow: Any, lookups: Sequence[Any], config: TrainingConfig
) -> TrainingResult:
    """Build the training set, train, evaluate on the held-out split and register the challenger."""
    labels = spark.table(config.features_table).select(KEY, LABEL)
    training_set = fe.create_training_set(df=labels, feature_lookups=list(lookups), label=LABEL, exclude_columns=[KEY])
    trained = fit(training_set.load_df().toPandas(), config.seed)  # perf-lint: allow toPandas -- single-node fit
    version = log_and_register(mlflow, fe, trained, training_set, config.model_name)
    return TrainingResult(config.model_name, version, trained.metrics)


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--features-table", required=True, help="catalog.schema.table of the feature table")
    parser.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    parser.add_argument("--experiment", required=True, help="MLflow experiment path; its parent folder must exist")
    parser.add_argument("--seed", type=int, default=7, help="seed of the split and the classifier")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Train the challenger on the job's Spark session and print the result as JSON."""
    args = build_parser().parse_args(argv)
    mlflow = importlib.import_module("mlflow")
    importlib.import_module("mlflow.sklearn")
    importlib.import_module("mlflow.tracking")
    engineering = importlib.import_module("databricks.feature_engineering")
    spark = importlib.import_module("pyspark.sql").SparkSession.builder.getOrCreate()
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experiment)
    lookups = [engineering.FeatureLookup(table_name=args.features_table, lookup_key=KEY, feature_names=list(FEATURES))]
    fe = engineering.FeatureEngineeringClient(model_registry_uri="databricks-uc")
    result = train_challenger(spark, fe, mlflow, lookups, TrainingConfig(args.features_table, args.model, args.seed))
    print(json.dumps({"model": result.model, "version": result.version, **result.metrics}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
