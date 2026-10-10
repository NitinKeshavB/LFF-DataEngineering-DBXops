"""Tests for the policy wording generator (bundles/aiops/src/jobs/generate_policy_docs.py)."""

from types import SimpleNamespace

import pytest

from tests.bundles.aiops_helpers import (
    SRC,
    load_script,
)


@pytest.fixture(scope="module")
def gen():
    """Import the generator script as a module."""
    return load_script("generate_policy_docs", SRC / "jobs/generate_policy_docs.py")


def fake_converter(page_html, path):
    """Write the HTML where the PDF would go, so tests can see what was converted."""
    path.write_text(page_html, encoding="utf-8")


def test__there_is_a_handful_of_wordings_with_unique_codes_and_file_names(gen):
    """The generator produces five documents, each with its own code and PDF name."""
    wordings = gen.policy_wordings()
    assert len(wordings) == 5
    assert len({w.code for w in wordings}) == 5
    assert len({w.file_name for w in wordings}) == 5
    assert all(w.file_name.endswith("_policy_wording.pdf") for w in wordings)


def test__generation_is_deterministic(gen):
    """Two calls give the same text and the same HTML, byte for byte."""
    first, second = gen.policy_wordings(), gen.policy_wordings()
    assert [gen.render_text(w) for w in first] == [gen.render_text(w) for w in second]
    assert [gen.render_html(w) for w in first] == [gen.render_html(w) for w in second]


def test__every_paragraph_is_long_enough_to_survive_the_silver_chunk_filter(gen):
    """Silver drops passages of 40 characters or fewer, so a wording paragraph must be longer than that."""
    for wording in gen.policy_wordings():
        for _, paragraphs in wording.sections:
            assert all(len(paragraph) > 40 for paragraph in paragraphs), wording.code


def test__each_wording_names_its_product_in_every_paragraph_so_a_chunk_stands_alone(gen):
    """A retrieved passage has no heading, so each paragraph says which policy it belongs to."""
    for wording in gen.policy_wordings():
        for _, paragraphs in wording.sections:
            assert all(wording.name in paragraph for paragraph in paragraphs), wording.code


def test__html_escapes_text_and_is_a_complete_page(gen):
    """Special characters in a paragraph are escaped, and the page has a doctype and a body."""
    wording = gen.PolicyWording("X", "A & B", (("Cover <1>", ("A & B pays $1 < $2 in every case that applies.",)),))
    page = gen.render_html(wording)
    assert page.startswith("<!DOCTYPE html>")
    assert "A &amp; B pays $1 &lt; $2" in page and "Cover &lt;1&gt;" in page


def test__write_documents_converts_each_wording_once_and_leaves_existing_files_alone(gen, tmp_path):
    """A rerun writes nothing, so Auto Loader never sees a rewritten file."""
    wordings = gen.policy_wordings()
    first = gen.write_documents(tmp_path / "docs", wordings, fake_converter)
    assert sorted(p.name for p in first) == sorted(w.file_name for w in wordings)
    marker = tmp_path / "docs" / wordings[0].file_name
    marker.write_text("kept", encoding="utf-8")
    assert gen.write_documents(tmp_path / "docs", wordings, fake_converter) == []
    assert marker.read_text(encoding="utf-8") == "kept"


def test__main_writes_the_documents_and_reports_the_count(gen, tmp_path, capsys):
    """The task entry point creates the output folder and prints how many PDFs it wrote."""
    assert gen.main(["--output", str(tmp_path / "landing/policy_docs")], convert=fake_converter) == 0
    assert "wrote 5 of 5" in capsys.readouterr().out
    assert len(list((tmp_path / "landing/policy_docs").glob("*.pdf"))) == 5


def test__the_plutoprint_converter_loads_the_html_and_writes_the_pdf(gen, tmp_path, monkeypatch):
    """The converter follows the skill's plutoprint calls: Book(A4), load_html, write_to_pdf."""
    calls = []

    class Book:
        """Records the calls made on it."""

        def __init__(self, size):
            calls.append(("Book", size))

        def load_html(self, page):
            calls.append(("load_html", page))

        def write_to_pdf(self, path):
            calls.append(("write_to_pdf", path))

    fake = SimpleNamespace(Book=Book, PAGE_SIZE_A4="A4")
    monkeypatch.setattr(gen.importlib, "import_module", lambda name: fake if name == "plutoprint" else None)
    gen.plutoprint_converter("<p>x</p>", tmp_path / "a.pdf")
    assert calls == [("Book", "A4"), ("load_html", "<p>x</p>"), ("write_to_pdf", str(tmp_path / "a.pdf"))]


def test__a_real_pdf_is_written_when_plutoprint_is_installed(gen, tmp_path):
    """With the job library available, a wording becomes a file that starts like a PDF."""
    pytest.importorskip("plutoprint")
    written = gen.write_documents(tmp_path, gen.policy_wordings()[:1], gen.plutoprint_converter)
    assert written[0].read_bytes().startswith(b"%PDF")
