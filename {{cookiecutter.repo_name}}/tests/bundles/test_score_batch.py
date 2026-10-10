"""Tests for the MLOps bundle's batch scoring job (bundles/mlops/src/jobs/score_batch.py)."""

from types import SimpleNamespace

import pytest

from tests.bundles.job_script import loaded_script
from tests.consts import PROJECT_DIR

SCRIPT = PROJECT_DIR / "bundles/mlops/src/jobs/score_batch.py"
MODEL = "cat.example_claims_ml.claims_fraud_model"
FEATURES = "cat.example_claims_ml.claims_fraud_features"
SCORES = "cat.example_claims_ml.claims_fraud_scores"


@pytest.fixture(scope="module")
def job():
    """Import the scoring script as a module (it is a job script, not part of the package)."""
    with loaded_script(SCRIPT) as module:
        yield module


def fakes(log):
    """Return Spark, Feature Engineering and MLflow stand-ins that record what the job asks of them."""

    class Result:
        """A scored frame that records how it is saved."""

        write = SimpleNamespace(
            mode=lambda mode: SimpleNamespace(saveAsTable=lambda name: log.append(("save", mode, name)))
        )

    scored = SimpleNamespace(selectExpr=lambda *expressions: log.append(("selectExpr", expressions)) or Result)
    spark = SimpleNamespace(
        table=lambda name: SimpleNamespace(select=lambda *columns: log.append(("select", name, columns)) or "keys")
    )
    fe = SimpleNamespace(score_batch=lambda **kwargs: log.append(("score_batch", kwargs)) or scored)
    client = SimpleNamespace(
        get_model_version_by_alias=lambda name, alias: log.append(("alias", name, alias))
        or SimpleNamespace(version="9")
    )
    return spark, fe, client


def payload(log, name):
    """Return the single argument recorded for the first call called `name`."""
    return next(entry[1] for entry in log if entry[0] == name)


def test__the_champion_scores_every_claim_and_the_table_is_overwritten(job):
    """Only the claim ids go in, the champion alias is the model uri, and the scores table is rewritten."""
    log = []
    spark, fe, client = fakes(log)
    assert job.score_champion(spark, fe, client, job.ScoringConfig(FEATURES, MODEL, SCORES)) == 9
    assert ("alias", MODEL, "champion") in log
    assert ("select", FEATURES, ("claim_id",)) in log
    assert payload(log, "score_batch") == {
        "model_uri": f"models:/{MODEL}@champion",
        "df": "keys",
        "result_type": "double",
    }
    assert payload(log, "selectExpr") == (
        "claim_id",
        "prediction AS fraud_score",
        "9 AS model_version",
        "current_timestamp() AS scored_at",
    )
    assert log[-1] == ("save", "overwrite", SCORES)


def test__the_entry_point_wires_the_arguments_to_the_scoring(job, monkeypatch, capsys):
    """The entry point builds the clients on Unity Catalog and reports the table and version."""
    calls = []
    engineering = SimpleNamespace(FeatureEngineeringClient=lambda model_registry_uri: ("fe", model_registry_uri))
    tracking = SimpleNamespace(MlflowClient=lambda registry_uri: ("client", registry_uri))
    spark = SimpleNamespace(builder=SimpleNamespace(getOrCreate=lambda: "spark"))
    modules = {
        "mlflow.tracking": tracking,
        "databricks.feature_engineering": engineering,
        "pyspark.sql": SimpleNamespace(SparkSession=spark),
    }
    monkeypatch.setattr(job.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(job, "score_champion", lambda *args: calls.append(args) or 2)
    argv = ["--features-table", FEATURES, "--model", MODEL, "--scores-table", SCORES]
    assert job.main(argv) == 0
    config = job.ScoringConfig(FEATURES, MODEL, SCORES)
    assert calls == [("spark", ("fe", "databricks-uc"), ("client", "databricks-uc"), config)]
    assert f"{SCORES}: scored with {MODEL} version 2" in capsys.readouterr().out
