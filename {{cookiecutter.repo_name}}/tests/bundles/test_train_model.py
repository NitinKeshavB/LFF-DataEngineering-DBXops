"""Tests for the MLOps bundle's training job (bundles/mlops/src/jobs/train_model.py); scikit-learn parts are skipped without it."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.bundles.job_script import loaded_script
from tests.consts import PROJECT_DIR

pd = pytest.importorskip("pandas")

SCRIPT = PROJECT_DIR / "bundles/mlops/src/jobs/train_model.py"
MODEL = "cat.example_claims_ml.claims_fraud_model"
TABLE = "cat.example_claims_ml.claims_fraud_features"


@pytest.fixture(scope="module")
def job():
    """Import the training script as a module (it is a job script, not part of the package)."""
    with loaded_script(SCRIPT) as module:
        yield module


def make_frame(job, rows=400):
    """Return a joined training frame whose label follows the feature rules, with Decimal money like Spark gives."""
    records = []
    for i in range(rows):
        reserve = Decimal(1000 + (i % 40) * 500)
        insured = Decimal(20000 + (i % 7) * 10000)
        records.append(
            {
                "claim_reserve": reserve,
                "days_loss_to_report": (i * 7) % 60,
                "policy_age_days": (i * 13) % 700,
                "sum_insured": insured,
                "annual_premium": Decimal(800 + i % 5 * 100),
                "reserve_to_sum_insured": float(reserve / insured),
                "prior_claim_count": i % 4,
                "payment_count": i % 3,
                "total_paid": Decimal(i % 3 * 250),
                job.LABEL: int(i % 4 >= 3 or float(reserve / insured) > 0.5),
            }
        )
    return pd.DataFrame(records)


class FakeRun:
    """A context manager standing in for mlflow.start_run."""

    def __init__(self, log):
        self.log = log

    def __enter__(self):
        """Record the start of the run."""
        self.log.append(("start_run",))
        return self

    def __exit__(self, *exc):
        """Record the end of the run."""
        self.log.append(("end_run",))


def payload(log, name):
    """Return the single argument recorded for the first call called `name`."""
    return next(entry[1] for entry in log if entry[0] == name)


def fake_mlflow(log, versions):
    """Return an mlflow stand-in that records calls and serves the given registered versions."""

    class Client:
        """A registry client that records its calls."""

        def __init__(self, registry_uri=None):
            log.append(("client", registry_uri))

        def search_model_versions(self, query):
            """Return the registered versions."""
            log.append(("search_model_versions", query))
            return [SimpleNamespace(version=str(v)) for v in versions]

        def set_registered_model_alias(self, name, alias, version):
            """Record the alias."""
            log.append(("set_registered_model_alias", name, alias, version))

    return SimpleNamespace(
        sklearn="flavor-sklearn",
        tracking=SimpleNamespace(MlflowClient=Client),
        start_run=lambda run_name: FakeRun(log),
        log_params=lambda params: log.append(("log_params", params)),
        log_metrics=lambda metrics: log.append(("log_metrics", metrics)),
    )


def test__the_training_frame_is_numeric_and_drops_rows_without_a_label(job):
    """Decimal columns become floats, a missing feature becomes 0 and an unlabelled row is dropped."""
    frame = make_frame(job, rows=8)
    frame.loc[0, job.LABEL] = None
    frame.loc[1, "sum_insured"] = None
    features, label = job.build_training_frame(frame)
    assert list(features.columns) == list(job.FEATURES)
    assert len(features) == len(label) == 7
    assert set(features.dtypes.astype(str)) == {"float64"}
    assert features.loc[1, "sum_insured"] == 0.0
    assert label.dtype.kind == "i"


def test__a_frame_without_a_feature_column_is_rejected_by_name(job):
    """A missing column is named, so a changed feature table fails loudly instead of training on less."""
    frame = make_frame(job, rows=8).drop(columns=["total_paid"])
    with pytest.raises(ValueError, match="total_paid"):
        job.build_training_frame(frame)


def test__a_single_class_label_is_rejected(job):
    """A classifier cannot be trained or scored by AUC on one class."""
    frame = make_frame(job, rows=8)
    frame[job.LABEL] = 0
    with pytest.raises(ValueError, match="both classes"):
        job.build_training_frame(frame)


def test__training_separates_the_classes_on_the_held_out_split(job):
    """The synthetic label follows rules over the features, so a boosted tree classifier scores a high held-out AUC."""
    pytest.importorskip("sklearn")
    features, label = job.build_training_frame(make_frame(job))
    x_train, x_test, y_train, y_test = job.split(features, label, seed=7)
    assert len(x_test) == pytest.approx(len(features) * job.TEST_FRACTION, abs=1)
    assert abs(y_train.mean() - y_test.mean()) < 0.02
    metrics = job.evaluate(job.train(x_train, y_train, seed=7, n_estimators=50), x_test, y_test)
    assert set(metrics) == {"auc", "accuracy"}
    assert metrics["auc"] > 0.9


def test__logging_registers_with_lineage_and_gives_the_newest_version_the_challenger_alias(job):
    """The run holds the params and metrics, fe.log_model gets the training set, and the highest version is aliased."""
    log = []
    fe = SimpleNamespace(log_model=lambda **kwargs: log.append(("log_model", kwargs)))
    trained = job.TrainedModel("model", {"auc": 0.9}, {"seed": 7})
    version = job.log_and_register(fake_mlflow(log, [1, 2, 10]), fe, trained, "training-set", MODEL)
    assert version == 10
    assert [entry[0] for entry in log] == [
        "start_run",
        "log_params",
        "log_metrics",
        "log_model",
        "end_run",
        "client",
        "search_model_versions",
        "set_registered_model_alias",
    ]
    logged = payload(log, "log_model")
    assert logged == {
        "model": "model",
        "artifact_path": "model",
        "flavor": "flavor-sklearn",
        "training_set": "training-set",
        "registered_model_name": MODEL,
    }
    assert ("client", "databricks-uc") in log
    assert ("set_registered_model_alias", MODEL, "challenger", 10) in log


def test__train_challenger_builds_the_training_set_from_the_feature_table_and_registers(job):
    """Labels are read from the feature table, the lookups join the features, and the held-out AUC is what is logged."""
    pytest.importorskip("sklearn")
    frame = make_frame(job)
    log = []
    labels = SimpleNamespace()
    spark = SimpleNamespace(
        table=lambda name: SimpleNamespace(select=lambda *columns: log.append(("select", name, columns)) or labels)
    )
    training_set = SimpleNamespace(load_df=lambda: SimpleNamespace(toPandas=lambda: frame))

    def create_training_set(**kwargs):
        log.append(("create_training_set", kwargs))
        return training_set

    fe = SimpleNamespace(
        create_training_set=create_training_set, log_model=lambda **kwargs: log.append(("log_model", kwargs))
    )
    result = job.train_challenger(spark, fe, fake_mlflow(log, [3]), ["lookup"], job.TrainingConfig(TABLE, MODEL, 7))
    assert ("select", TABLE, (job.KEY, job.LABEL)) in log
    created = payload(log, "create_training_set")
    assert created == {"df": labels, "feature_lookups": ["lookup"], "label": job.LABEL, "exclude_columns": [job.KEY]}
    assert payload(log, "log_metrics") == result.metrics and result.metrics["auc"] > 0.9
    assert (result.model, result.version) == (MODEL, 3)


def test__the_entry_point_wires_the_job_arguments_to_the_training(job, monkeypatch, capsys):
    """The job arguments reach the training, MLflow is set to Unity Catalog and the result is printed as JSON."""
    log = []
    engineering = SimpleNamespace(
        FeatureLookup=lambda **kwargs: ("lookup", kwargs),
        FeatureEngineeringClient=lambda model_registry_uri: ("fe", model_registry_uri),
    )
    mlflow = SimpleNamespace(
        set_registry_uri=lambda uri: log.append(("registry", uri)),
        set_experiment=lambda path: log.append(("experiment", path)),
    )
    spark = SimpleNamespace(builder=SimpleNamespace(getOrCreate=lambda: "spark"))
    modules = {
        "mlflow": mlflow,
        "mlflow.sklearn": None,
        "mlflow.tracking": None,
        "databricks.feature_engineering": engineering,
        "pyspark.sql": SimpleNamespace(SparkSession=spark),
    }
    monkeypatch.setattr(job.importlib, "import_module", modules.__getitem__)

    def fake_train_challenger(*args):
        log.append(("train", args))
        return job.TrainingResult(MODEL, 4, {"auc": 0.91})

    monkeypatch.setattr(job, "train_challenger", fake_train_challenger)
    code = job.main(["--features-table", TABLE, "--model", MODEL, "--experiment", "/Shared/x", "--seed", "3"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"model": MODEL, "version": 4, "auc": 0.91}
    assert ("registry", "databricks-uc") in log and ("experiment", "/Shared/x") in log
    spark_arg, fe_arg, _, lookups, config = payload(log, "train")
    assert (spark_arg, fe_arg, config) == ("spark", ("fe", "databricks-uc"), job.TrainingConfig(TABLE, MODEL, 3))
    assert lookups == [
        ("lookup", {"table_name": TABLE, "lookup_key": "claim_id", "feature_names": list(job.FEATURES)})
    ]
