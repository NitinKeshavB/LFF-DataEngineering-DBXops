"""Tests for registration validation, the platform snapshots and the registry command line."""

import shutil
from pathlib import Path

import pytest
import yaml

from dbxops.registry.cli import main
from dbxops.registry.registration import (
    group_name,
    validate_registry,
)
from dbxops.registry.snapshots import (
    SNAPSHOT_FILES,
    check_text,
    render_registry_project,
    strip_header,
    verify_directory,
    with_header,
)
from tests.consts import PROJECT_DIR

SCAFFOLD = PROJECT_DIR / "templates/registry-repo"


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """Return a writable copy of the registry scaffold."""
    root = tmp_path / "registry"
    shutil.copytree(SCAFFOLD, root)
    return root


def project(root: Path, name: str = "example_claims") -> Path:
    """Return the path of a registration file."""
    return root / "projects" / f"{name}.yaml"


def edit(root: Path, change, name: str = "example_claims") -> None:
    """Apply `change(doc)` to a registration file in place."""
    doc = yaml.safe_load(project(root, name).read_text(encoding="utf-8"))
    change(doc)
    project(root, name).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def messages(root: Path, strict: bool = False) -> list[str]:
    """Return every finding message for a registry."""
    return [f.message for f in validate_registry(root, strict)]


def test__the_scaffold_registry_is_valid_but_not_strictly(registry: Path):
    """Placeholders are accepted on a normal run and rejected with --strict."""
    assert messages(registry) == []
    assert any(
        "placeholder value 'PLACEHOLDER-project-sp-application-id'" in m for m in messages(registry, strict=True)
    )


def test__ad_groups_follow_the_naming_convention():
    """Groups are AAD-DBX-<PROJECT>-<DEV|READ|ADMIN> with underscores removed and upper case."""
    assert group_name("claims_fraud", "developers") == "AAD-DBX-CLAIMSFRAUD-DEV"
    assert group_name("claims_fraud", "readers") == "AAD-DBX-CLAIMSFRAUD-READ"
    assert group_name("claims_fraud", "admins") == "AAD-DBX-CLAIMSFRAUD-ADMIN"


def test__a_group_with_the_wrong_name_is_rejected(registry: Path):
    """A group that is well-formed but belongs to another project is caught with the expected name."""
    edit(registry, lambda d: d["groups"].update(readers="AAD-DBX-OTHER-READ"))
    assert "groups/readers: expected 'AAD-DBX-EXAMPLECLAIMS-READ', got 'AAD-DBX-OTHER-READ'" in messages(registry)


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: d["contacts"].pop("data_owner"), "'data_owner' is a required property"),
        (lambda d: d["contacts"].update(data_owner="not-an-email"), "contacts/data_owner"),
        (lambda d: d.update(project_id="Bad Id"), "project_id"),
        (lambda d: d["repo"].update(name="my-repo"), "repo/name"),
        (lambda d: d.update(kind="team"), "kind"),
        (lambda d: d["groups"].update(admins="some-group"), "groups/admins"),
    ],
)
def test__schema_violations_are_reported_with_their_path(registry: Path, change, expected: str):
    """Missing owners, malformed emails/ids/repo names/kinds and group patterns fail the JSON schema."""
    edit(registry, change)
    assert any(expected in m for m in messages(registry))


def test__an_unknown_domain_gets_suggestions_and_any_level_is_accepted(registry: Path):
    """A registration may name a business, sub or data domain, but a typo is rejected with suggestions."""
    for level_path in ("claims", "claims__litigation", "claims__litigation__claim_litigation"):
        edit(registry, lambda d, p=level_path: d.update(domain=p))
        assert messages(registry) == []
    edit(registry, lambda d: d.update(domain="claims__litigaton"))
    assert any("did you mean: claims__litigation" in m for m in messages(registry))


def test__missing_required_tag_keys_are_listed(registry: Path):
    """Every key in required_tags.yaml must be present; the message names the missing ones."""
    edit(registry, lambda d: d["tags"].pop("region"))
    assert "missing required tag(s): region" in messages(registry)


def test__identifiers_must_be_unique_across_registrations(registry: Path):
    """A second registration reusing the project id (ignoring '_'), repo name, GitHub team or squad name is rejected."""
    other = yaml.safe_load(project(registry).read_text(encoding="utf-8"))
    other.update(project_id="exampleclaims")
    other["groups"] = {r: group_name("exampleclaims", r) for r in ("developers", "readers", "admins")}
    project(registry, "exampleclaims").write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")
    found = messages(registry)
    assert any("project_id (ignoring '_') 'exampleclaims' is already used" in m for m in found)
    assert any("repo name 'dbx-example-claims' is already used" in m for m in found)
    assert any("github_team 'example-claims' is already used" in m for m in found)
    assert any("squad name 'example_claims_squad' is already used" in m for m in found)


def test__the_file_name_must_be_the_project_id(registry: Path):
    """projects/<project_id>.yaml: a mismatched file name is an error."""
    project(registry).rename(project(registry, "wrong_name"))
    assert any("file name must be <project_id>.yaml" in m for m in messages(registry))


def test__snapshots_carry_a_hash_header_that_detects_edits(registry: Path, tmp_path: Path):
    """Rendered snapshots verify; changing the body, or removing the header, is detected."""
    out = tmp_path / "platform"
    for name, text in render_registry_project(registry, "example_claims").items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(text, encoding="utf-8")
    assert not verify_directory(out)
    (out / "repo.yaml").write_text((out / "repo.yaml").read_text(encoding="utf-8") + "extra: 1\n", encoding="utf-8")
    (out / "teams.yaml").write_text(strip_header((out / "teams.yaml").read_text(encoding="utf-8")), encoding="utf-8")
    (out / "glossary.ttl").unlink()
    problems = dict(verify_directory(out))
    assert "edited by hand" in problems["repo.yaml"]
    assert "missing the machine-owned hash header" in problems["teams.yaml"]
    assert problems["glossary.ttl"] == "missing"


def test__rendering_is_deterministic_and_matches_the_committed_platform_snapshots():
    """Rendering the scaffold twice gives the same bytes, and equals what is committed in metadata/_platform."""
    first = render_registry_project(SCAFFOLD, "example_claims")
    assert first == render_registry_project(SCAFFOLD, "example_claims")
    assert set(first) == set(SNAPSHOT_FILES)
    for name, text in first.items():
        assert (PROJECT_DIR / "metadata/_platform" / name).read_text(encoding="utf-8") == text, name


def test__header_helpers_round_trip():
    """with_header/strip_header/check_text agree, and text without a header is returned unchanged by strip_header."""
    assert check_text(with_header("a: 1\n")) is None
    assert strip_header(with_header("a: 1\n")) == "a: 1\n"
    assert strip_header("a: 1\n") == "a: 1\n"


def test__cli_validate_and_render(registry: Path, tmp_path: Path, capsys):
    """`validate` exits 0/1 by --strict; `render` writes the four snapshot files."""
    assert main(["validate", str(registry)]) == 0
    assert "registry is valid" in capsys.readouterr().out
    assert main(["validate", str(registry), "--strict"]) == 1
    assert "placeholder value" in capsys.readouterr().out
    assert main(["render", str(registry), "example_claims", str(tmp_path / "out")]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(SNAPSHOT_FILES)
