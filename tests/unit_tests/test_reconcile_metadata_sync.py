"""Tests for MetadataSyncReconciler: comments, tag adds, owned-key removal and the never-touch rules."""

from dbxops.reconcile import (
    DesiredComment,
    DesiredTag,
    MetadataSyncInput,
    MetadataSyncReconciler,
    is_owned_tag_key,
)

TABLE = "cat.claims_silver.fact_claim"
MODEL = "cat.claims_ml.fraud"


def _tag(key, value="v", active=True, entity_type="table", entity_id=TABLE) -> DesiredTag:
    return DesiredTag(entity_type, entity_id, key, value, active)


def _gateway_with_table(fake_gateway, tags=None, comment=None, columns=None):
    fake_gateway.tables[TABLE] = {"comment": comment, "columns": columns or {"claim_id": None}}
    fake_gateway.tags[("table", TABLE)] = dict(tags or {})
    return fake_gateway


def test__owned_keys_are_the_four_families_plus_the_extra_keys():
    """data_domain_*, data_product_*, data_contract_*, classification and the passed-in project keys are owned."""
    owned = ["data_domain_claims__litigation", "data_product_claims_x", "data_contract_claims_y", "classification"]
    assert all(is_owned_tag_key(key) for key in owned)
    assert is_owned_tag_key("cost_center", ["cost_center"])
    assert not is_owned_tag_key("cost_center")
    assert not is_owned_tag_key("owner")


def test__a_missing_tag_is_added(fake_gateway):
    """A desired active tag absent from the table is set."""
    _gateway_with_table(fake_gateway)
    desired = MetadataSyncInput(tags=[_tag("data_domain_claims__litigation__claim_litigation", "")])
    result = MetadataSyncReconciler(fake_gateway).reconcile(desired, dry_run=False)
    assert fake_gateway.writes == [
        ("set_tag", "table", TABLE, "data_domain_claims__litigation__claim_litigation", "")
    ]
    assert [c.action for c in result.changes] == ["set_tag"]


def test__a_tag_with_a_stale_value_is_updated(fake_gateway):
    """A tag present with another value is set to the desired value."""
    _gateway_with_table(fake_gateway, tags={"classification": "internal"})
    MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("classification", "restricted")]), dry_run=False
    )
    assert fake_gateway.writes == [("set_tag", "table", TABLE, "classification", "restricted")]


def test__a_deactivated_owned_tag_is_removed(fake_gateway):
    """An inactive row for an owned key present on the table is unset."""
    _gateway_with_table(fake_gateway, tags={"data_product_claims_old": ""})
    MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("data_product_claims_old", "", active=False)]), dry_run=False
    )
    assert fake_gateway.writes == [("unset_tag", "table", TABLE, "data_product_claims_old")]
    assert fake_gateway.tags[("table", TABLE)] == {}


def test__a_deactivated_tag_that_is_already_gone_is_unchanged(fake_gateway):
    """Removing a tag that is not there is a no-op."""
    _gateway_with_table(fake_gateway)
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("data_product_claims_old", "", active=False)]), dry_run=False
    )
    assert result.changes == ()
    assert fake_gateway.writes == []


def test__a_foreign_tag_survives_and_a_foreign_deactivation_is_refused(fake_gateway):
    """A tag outside the owned set is never removed, even when an inactive row names it."""
    _gateway_with_table(fake_gateway, tags={"team_owner": "squad-a", "classification": "internal"})
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("team_owner", "", active=False)]), dry_run=False
    )
    assert fake_gateway.writes == []
    assert fake_gateway.tags[("table", TABLE)]["team_owner"] == "squad-a"
    assert "not owned" in result.errors[0]


def test__a_foreign_tag_is_not_overwritten_either(fake_gateway):
    """An active row for a key outside the owned set is not written."""
    _gateway_with_table(fake_gateway, tags={"team_owner": "squad-a"})
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("team_owner", "squad-b")]), dry_run=False
    )
    assert fake_gateway.writes == []
    assert fake_gateway.tags[("table", TABLE)]["team_owner"] == "squad-a"
    assert len(result.errors) == 1


def test__existing_tags_absent_from_the_desired_rows_are_left_alone(fake_gateway):
    """Absence of a row is not deactivation: an owned tag with no row stays."""
    _gateway_with_table(fake_gateway, tags={"classification": "internal", "data_domain_x": ""})
    MetadataSyncReconciler(fake_gateway).reconcile(MetadataSyncInput(tags=[_tag("classification", "internal")]), dry_run=False)
    assert fake_gateway.writes == []
    assert "data_domain_x" in fake_gateway.tags[("table", TABLE)]


def test__ontos_tags_are_never_written_or_removed(fake_gateway):
    """An ontos_ key is refused whether the row is active or inactive, even when it is passed as an owned key."""
    _gateway_with_table(fake_gateway, tags={"ontos_asset_type": "table"})
    reconciler = MetadataSyncReconciler(fake_gateway, owned_extra_keys=["ontos_asset_type"])
    result = reconciler.reconcile(
        MetadataSyncInput(tags=[_tag("ontos_asset_type", "view"), _tag("ontos_asset_type", "", active=False)]),
        dry_run=False,
    )
    assert fake_gateway.writes == []
    assert fake_gateway.tags[("table", TABLE)] == {"ontos_asset_type": "table"}
    assert len(result.errors) == 2
    assert all("ontos_" in error for error in result.errors)


def test__project_tag_keys_passed_as_owned_extra_keys_are_synced_and_removable(fake_gateway):
    """Project tags are written and removed once the caller declares their keys."""
    _gateway_with_table(fake_gateway, tags={"region": "emea"})
    reconciler = MetadataSyncReconciler(fake_gateway, owned_extra_keys=["cost_center", "region"])
    reconciler.reconcile(
        MetadataSyncInput(tags=[_tag("cost_center", "cc-1"), _tag("region", "", active=False)]), dry_run=False
    )
    assert fake_gateway.writes == [
        ("set_tag", "table", TABLE, "cost_center", "cc-1"),
        ("unset_tag", "table", TABLE, "region"),
    ]


def test__an_invalid_tag_key_is_reported_and_skipped(fake_gateway):
    """A key that breaks the identifier rule is not sent to the workspace."""
    _gateway_with_table(fake_gateway)
    reconciler = MetadataSyncReconciler(fake_gateway, owned_extra_keys=["Cost-Center"])
    result = reconciler.reconcile(MetadataSyncInput(tags=[_tag("Cost-Center", "x")]), dry_run=False)
    assert fake_gateway.writes == []
    assert "must match" in result.errors[0]


def test__an_unknown_entity_is_reported_once(fake_gateway):
    """Tags for a table that does not exist are an error per entity, not per tag."""
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("classification", "a"), _tag("data_domain_x", "")]), dry_run=False
    )
    assert fake_gateway.writes == []
    assert len(result.errors) == 1
    assert "not found" in result.errors[0]


def test__an_unsupported_entity_type_is_reported(fake_gateway):
    """Tagging an entity type the gateway cannot tag is an error."""
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(tags=[_tag("classification", "a", entity_type="job", entity_id="123")])
    )
    assert "cannot tag entity type 'job'" in result.errors[0]


def test__models_and_serving_endpoints_are_tagged(fake_gateway):
    """Registered models and serving endpoints take the same tags as tables."""
    fake_gateway.tags[("registered_model", MODEL)] = {}
    fake_gateway.tags[("serving_endpoint", "fraud_scorer")] = {}
    desired = MetadataSyncInput(
        tags=[
            _tag("data_product_claims_fraud", "", entity_type="registered_model", entity_id=MODEL),
            _tag("data_product_claims_fraud", "", entity_type="serving_endpoint", entity_id="fraud_scorer"),
        ]
    )
    MetadataSyncReconciler(fake_gateway).reconcile(desired, dry_run=False)
    assert fake_gateway.writes == [
        ("set_tag", "registered_model", MODEL, "data_product_claims_fraud", ""),
        ("set_tag", "serving_endpoint", "fraud_scorer", "data_product_claims_fraud", ""),
    ]


def test__table_and_column_comments_are_applied_when_they_differ(fake_gateway):
    """Differing comments are set, matching ones are left."""
    _gateway_with_table(fake_gateway, comment="old", columns={"claim_id": "Claim key", "amount": None})
    desired = MetadataSyncInput(
        comments=[
            DesiredComment("table", TABLE, "One row per claim"),
            DesiredComment("column", f"{TABLE}.claim_id", "Claim key"),
            DesiredComment("column", f"{TABLE}.amount", "Paid amount"),
        ]
    )
    result = MetadataSyncReconciler(fake_gateway).reconcile(desired, dry_run=False)
    assert fake_gateway.writes == [
        ("set_comment", "table", TABLE, "One row per claim"),
        ("set_comment", "column", f"{TABLE}.amount", "Paid amount"),
    ]
    assert result.unchanged == (f"column:{TABLE}.claim_id/comment",)


def test__a_comment_on_a_missing_table_or_column_is_reported(fake_gateway):
    """Comments for objects that do not exist are errors and write nothing."""
    _gateway_with_table(fake_gateway)
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(
            comments=[DesiredComment("table", "cat.s.absent", "x"), DesiredComment("column", f"{TABLE}.nope", "y")]
        ),
        dry_run=False,
    )
    assert fake_gateway.writes == []
    assert "table not found" in result.errors[0]
    assert "column not found" in result.errors[1]


def test__a_comment_on_another_entity_type_is_reported(fake_gateway):
    """Only tables and columns take comments."""
    result = MetadataSyncReconciler(fake_gateway).reconcile(
        MetadataSyncInput(comments=[DesiredComment("schema", "cat.s", "x")])
    )
    assert "comments can be set on table or column" in result.errors[0]


def test__dry_run_reads_but_never_writes(fake_gateway):
    """A dry run plans the tag and comment changes without calling any write."""
    _gateway_with_table(fake_gateway, tags={"data_product_claims_old": ""})
    desired = MetadataSyncInput(
        tags=[_tag("classification", "a"), _tag("data_product_claims_old", "", active=False)],
        comments=[DesiredComment("table", TABLE, "new")],
    )
    result = MetadataSyncReconciler(fake_gateway).reconcile(desired)
    assert fake_gateway.writes == []
    assert result.applied is False
    assert len(result.changes) == 3


def test__a_second_run_is_a_no_op(fake_gateway):
    """After applying, the same desired input plans nothing."""
    _gateway_with_table(fake_gateway, tags={"data_product_claims_old": ""})
    desired = MetadataSyncInput(
        tags=[_tag("classification", "a"), _tag("data_product_claims_old", "", active=False)],
        comments=[DesiredComment("table", TABLE, "new")],
    )
    reconciler = MetadataSyncReconciler(fake_gateway)
    reconciler.reconcile(desired, dry_run=False)
    fake_gateway.calls.clear()
    second = reconciler.reconcile(desired, dry_run=False)
    assert second.changes == ()
    assert second.errors == ()
    assert fake_gateway.writes == []
