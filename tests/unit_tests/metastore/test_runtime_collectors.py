"""Tests of the run, deployment and lineage collectors with fake clients; no workspace is contacted."""

from datetime import (
    datetime,
    timezone,
)

from dbxops.metastore.records import (
    ORIGIN_SPEC,
    ORIGIN_UC_SYSTEM,
    CiContext,
    QualityRuleRef,
)
from dbxops.metastore.runtime_collectors import (
    DeploymentCollector,
    InMemoryRuleCatalog,
    LineageCollector,
    RunCollector,
    RunSources,
)
from dbxops.metastore.sources import ci_context_from_env
from dbxops.metastore.tasks import default_asset_resolver
from tests.fixtures.metastore import (
    SQUAD,
    FakeEventLog,
    FakeJobs,
    dataflow_record_for_tests,
)

ASSET = "claims_fraud_ml.fraud_scores"


def collect_run():
    """Collect the fake run against a catalog holding one rule at version 4."""
    rules = InMemoryRuleCatalog([QualityRuleRef("dc_x", 4, "claim_id_not_null", ASSET)])
    collector = RunCollector(RunSources(FakeJobs(), FakeEventLog(), rules), default_asset_resolver, SQUAD)
    return collector.collect("55", "dev")


def test__a_run_record_comes_from_the_jobs_api():
    """Status, trigger, commit, job asset id and millisecond timestamps are mapped from the run."""
    run = collect_run().run
    assert (run.run_id, run.status, run.origin.trigger, run.origin.git_sha, run.job_asset_id) == (
        "55",
        "SUCCESS",
        "PERIODIC",
        "abc123",
        "uc_job/claims_fraud/7",
    )
    assert run.timing.started_at == datetime.fromtimestamp(1_760_000_000, tz=timezone.utc)


def test__pipeline_updates_carry_dataset_metrics_with_resolved_asset_ids():
    """Dataset names from the event log become logical asset ids."""
    update = collect_run().run.pipeline_updates[0]
    assert update.full_refresh is True and update.metrics[0].asset_id == ASSET and update.metrics[0].rows_dropped == 3


def test__quality_results_are_pinned_to_the_current_rule_version_and_unknown_expectations_reported():
    """A known expectation is pinned to the catalog's rule version; an unknown one is listed as unmapped."""
    result = collect_run()
    assert len(result.quality) == 1
    quality = result.quality[0]
    assert (quality.contract_id, quality.rule_record_version, quality.counts.passed, quality.counts.dropped) == (
        "dc_x",
        4,
        8,
        2,
    )
    assert [item.name for item in result.unmapped] == ["unknown_expectation"]


def test__a_deployment_lists_bundle_resources_as_assets():
    """Jobs and pipelines of the bundle summary become deployed assets with workspace ids and known versions."""

    class Runner:
        """A bundle runner stand-in."""

        def summary(self, _target):
            """Return a summary with one job and one pipeline."""
            return {
                "bundle": {"name": "dataops"},
                "resources": {
                    "jobs": {"main": {"id": "11", "name": "[prod] main"}},
                    "pipelines": {"silver": {"id": "22", "name": "silver"}},
                },
            }

    ci = CiContext("99", 2, "https://ci/99", "sha1", "octocat", "v0.24.1", "0.1.0")
    record = DeploymentCollector(Runner(), SQUAD, lambda asset_id: 3).collect("prod", ci)
    assert (record.bundle, record.ci.ci_run_attempt, record.deployment_id) == ("dataops", 2, "prod/dataops/99/2")
    assert [(a.asset_id, a.workspace_object_id, a.asset_record_version) for a in record.assets] == [
        ("uc_job/claims_fraud/main", "11", 3),
        ("uc_pipeline/claims_fraud/silver", "22", 3),
    ]
    assert record.deployed_at is None and record.ci.framework_version == "v0.24.1"


def test__spec_edges_are_the_declared_design():
    """Flows with both a source and a target become spec edges that name their spec."""
    edges = LineageCollector.spec_edges([dataflow_record_for_tests()])
    assert [(e.source_asset_id, e.target_asset_id, e.origin, e.via) for e in edges] == [
        ("a.x", "a.y", ORIGIN_SPEC, "spec_scores")
    ]


def test__observed_edges_resolve_to_logical_assets_and_skip_unresolved_ends():
    """Lineage rows become uc_system edges; duplicates collapse and a missing end is dropped."""

    class Reader:
        """A lineage reader stand-in."""

        def table_lineage(self):
            """Return two equal rows and one row without a source."""
            return [("main.a.x", "main.a.y"), ("main.a.x", "main.a.y"), ("", "main.a.z")]

    observed = LineageCollector(Reader()).observed_edges()
    assert [(e.source_asset_id, e.target_asset_id, e.origin) for e in observed] == [("a.x", "a.y", ORIGIN_UC_SYSTEM)]
    assert not LineageCollector().observed_edges()


def test__the_ci_context_reads_github_variables():
    """In CI the run id, attempt, url and actor come from GITHUB_* variables."""
    env = {
        "GITHUB_RUN_ID": "5",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SHA": "s",
        "GITHUB_ACTOR": "me",
    }
    ci = ci_context_from_env(env, "u")
    assert (ci.ci_run_id, ci.ci_run_attempt, ci.ci_run_url, ci.deployed_by) == (
        "5",
        2,
        "https://github.com/o/r/actions/runs/5",
        "me",
    )


def test__outside_ci_the_run_id_is_local_and_unique():
    """Without GITHUB_RUN_ID the id is local-<uuid>."""
    assert ci_context_from_env({}, "abc").ci_run_id == "local-abc"
