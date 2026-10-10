"""Tests for the domain taxonomy loader, validator and resolver."""

import pytest
import yaml

from dbxops.registry.domains import (
    MAX_PATH_LENGTH,
    DomainError,
    DomainTaxonomy,
    main,
)
from tests.fixtures.domain_taxonomy import DOMAINS_FILE


def test__client_taxonomy_matches_the_source_document(client_taxonomy: DomainTaxonomy):
    """The snapshot holds every row of the client's taxonomy: 16 business, 62 sub, 82 data domains."""
    assert len(client_taxonomy.nodes("business_domain")) == 16
    assert len(client_taxonomy.nodes("sub_domain")) == 62
    assert len(client_taxonomy.nodes("data_domain")) == 82
    restricted = [n.name for n in client_taxonomy.nodes("data_domain") if n.details.sensitivity == "restricted"]
    assert len(restricted) == 10


def test__a_table_rolls_up_through_its_domain_path(client_taxonomy: DomainTaxonomy):
    """A data-domain path resolves to its node, and its rollup names every level above it."""
    node = client_taxonomy.resolve("claims__litigation__claim_litigation", level="data_domain")
    assert node.display_path == "Claims > Litigation > Claim Litigation"
    assert node.details.sensitivity == "restricted"
    assert [n.name for n in client_taxonomy.rollup(node.path)] == ["Claims", "Litigation", "Claim Litigation"]


def test__a_misspelt_path_suggests_the_closest_valid_paths(client_taxonomy: DomainTaxonomy):
    """An unknown path is rejected, and the error names the path the user most likely meant."""
    with pytest.raises(DomainError, match="did you mean: claims__litigation__claim_litigation"):
        client_taxonomy.resolve("claims__litigation__claim_litigaton")


def test__a_display_name_suggests_its_path(client_taxonomy: DomainTaxonomy):
    """A human-readable display path is rejected but mapped to the path that must be used."""
    with pytest.raises(DomainError, match="did you mean: claims__litigation__claim_litigation"):
        client_taxonomy.resolve("Claims > Litigation > Claim Litigation")


def test__a_path_at_the_wrong_level_lists_valid_children(client_taxonomy: DomainTaxonomy):
    """A sub-domain is rejected where a data domain is required, and its data domains are offered."""
    with pytest.raises(DomainError, match="choose one of: customer__vulnerability_hardship__vulnerability_indicator"):
        client_taxonomy.resolve("customer__vulnerability_hardship", level="data_domain")


def test__non_string_references_are_rejected(client_taxonomy: DomainTaxonomy):
    """A missing or non-string value from a YAML file is rejected, not looked up."""
    with pytest.raises(DomainError, match="unknown domain path None"):
        client_taxonomy.resolve(None)


def test__is_within_follows_the_hierarchy(client_taxonomy: DomainTaxonomy):
    """A data domain is within its business domain, and not within a sibling business domain."""
    assert client_taxonomy.is_within("claims__litigation__claim_litigation", "claims")
    assert client_taxonomy.is_within("claims", "claims")
    assert not client_taxonomy.is_within("claims__litigation__claim_litigation", "customer")


def test__tentative_is_inherited_by_descendants(client_taxonomy: DomainTaxonomy):
    """Technology & Security is marked "(if in scope)", and its data domains carry that flag."""
    assert client_taxonomy.resolve("technology_security__security_events__security_event").details.tentative
    assert not client_taxonomy.resolve("claims__litigation__claim_litigation").details.tentative


def test__every_problem_in_an_invalid_taxonomy_is_reported(minimal_taxonomy_doc: dict):
    """One load reports bad slugs, duplicates, bad enums, unknown keys and a rename with no previous name."""
    data_domains = minimal_taxonomy_doc["business_domains"][0]["sub_domains"][0]["data_domains"]
    data_domains.extend(
        [
            {"name": "Bad", "slug": "Bad Slug", "status": "new"},
            {"name": "Dup", "slug": "claim_litigation", "status": "new"},
            {"name": "Odd", "slug": "odd", "status": "maybe", "sensitivity": "secret", "sensitivty": "restricted"},
            {"name": "Moved", "slug": "moved", "status": "renamed"},
        ]
    )
    with pytest.raises(DomainError) as excinfo:
        DomainTaxonomy.from_dict(minimal_taxonomy_doc)
    message = str(excinfo.value)
    assert "slug 'Bad Slug'" in message
    assert "claims__litigation__claim_litigation: duplicate domain path" in message
    assert "status 'maybe'" in message
    assert "sensitivity 'secret'" in message
    assert "unknown key(s) sensitivty" in message
    assert "renamed data domain needs 'previous_name'" in message


def test__a_double_underscore_in_a_slug_is_rejected(minimal_taxonomy_doc: dict):
    """A slug containing the path separator would make paths ambiguous."""
    minimal_taxonomy_doc["business_domains"][0]["slug"] = "claims__extra"
    with pytest.raises(DomainError, match="slug 'claims__extra'"):
        DomainTaxonomy.from_dict(minimal_taxonomy_doc)


def test__a_path_longer_than_the_identifier_cap_is_rejected(minimal_taxonomy_doc: dict):
    """A path that would push the UC tag key past 128 characters is rejected."""
    minimal_taxonomy_doc["business_domains"][0]["slug"] = "a" * MAX_PATH_LENGTH
    with pytest.raises(DomainError, match=f"longer than {MAX_PATH_LENGTH} characters"):
        DomainTaxonomy.from_dict(minimal_taxonomy_doc)


def test__an_empty_branch_is_rejected(minimal_taxonomy_doc: dict):
    """A sub-domain with no data domains cannot be a valid rollup target."""
    minimal_taxonomy_doc["business_domains"][0]["sub_domains"][0]["data_domains"] = []
    with pytest.raises(DomainError, match="claims__litigation: 'data_domains' must be a non-empty list"):
        DomainTaxonomy.from_dict(minimal_taxonomy_doc)


def test__main_validates_a_file(tmp_path, capsys, minimal_taxonomy_doc: dict):
    """The CLI exits 0 with a summary for a valid file and 1 with the errors for an invalid one."""
    assert main([str(DOMAINS_FILE)]) == 0
    assert "82 data_domains; 10 restricted" in capsys.readouterr().out

    invalid = tmp_path / "domains.yaml"
    minimal_taxonomy_doc["business_domains"][0]["slug"] = "Claims"
    invalid.write_text(yaml.safe_dump(minimal_taxonomy_doc), encoding="utf-8")
    assert main([str(invalid)]) == 1
    assert "slug 'Claims'" in capsys.readouterr().err

    assert main([]) == 2
