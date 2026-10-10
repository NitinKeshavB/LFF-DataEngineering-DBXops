"""Fixtures for the domain taxonomy: the real snapshot file and a minimal valid document."""

import pytest

from dbxops.registry.domains import DomainTaxonomy
from tests.consts import PROJECT_DIR

DOMAINS_FILE = PROJECT_DIR / "metadata/_platform/domains.yaml"


@pytest.fixture(scope="session")
def client_taxonomy() -> DomainTaxonomy:
    """Return the taxonomy loaded from metadata/_platform/domains.yaml."""
    return DomainTaxonomy.from_yaml(DOMAINS_FILE)


@pytest.fixture
def minimal_taxonomy_doc() -> dict:
    """Return a one-branch taxonomy document that tests can corrupt."""
    return {
        "business_domains": [
            {
                "name": "Claims",
                "slug": "claims",
                "sub_domains": [
                    {
                        "name": "Litigation",
                        "slug": "litigation",
                        "data_domains": [{"name": "Claim Litigation", "slug": "claim_litigation", "status": "new"}],
                    }
                ],
            }
        ]
    }
