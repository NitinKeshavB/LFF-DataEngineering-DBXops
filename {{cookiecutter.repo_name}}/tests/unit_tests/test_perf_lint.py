"""Tests for the performance lint: each banned pattern is reported, each exemption and clean form is not."""

from pathlib import Path

import pytest

from dbxops.perf_lint import (
    lint_source,
    lint_tree,
    main,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("rows = df.collect()", "collect()"),
        ("pdf = df.toPandas()", "toPandas()"),
        ("n = df.count()", "count()"),
        ("display(df)", "display()"),
        ("df.coalesce(1).write.save('p')", "coalesce(1)"),
        ("out = df.orderBy('a')", "global sort"),
        ("out = df.sort('a')", "global sort"),
        ("w = Window.orderBy('a')", "no partitionBy"),
        ("f = udf(lambda x: x)", "udf"),
        ("df = spark.read.jdbc(url, 't')", "numPartitions"),
        ("df = spark.read.format('jdbc').option('url', u).load()", "numPartitions"),
    ],
)
def test__each_banned_pattern_is_reported(code, expected):
    """A hot-path anti-pattern is an error that names the problem."""
    findings = lint_source(code + "\n", "x.py")
    assert findings and any(expected in f.message for f in findings), [str(f) for f in findings]


@pytest.mark.parametrize(
    "code",
    [
        "n = 'abc'.count('a')",
        "df.coalesce(8).write.save('p')",
        "w = Window.partitionBy('k').orderBy('a')",
        "df.sortWithinPartitions('a')",
        "f = pandas_udf(lambda s: s)",
        "df = spark.read.format('jdbc').option('numPartitions', 8).load()",
        "pdf = df.toPandas()  # perf-lint: allow toPandas -- single-node fit",
        "n = (\n    df\n    .count()\n)  # perf-lint: allow count -- bounded\n",
    ],
)
def test__the_clean_and_exempted_forms_are_not_reported(code):
    """Bounded forms, partitioned windows and an explicit allow comment pass."""
    assert lint_source(code + "\n", "x.py") == []


def test__a_decorated_row_wise_udf_is_reported():
    """The @udf decorator form is caught as well as the call form."""
    assert lint_source("@udf('int')\ndef f(x):\n    return x\n", "x.py")


def test__a_file_that_does_not_parse_is_one_finding():
    """Broken syntax is reported, not raised."""
    assert [f.message.startswith("cannot parse") for f in lint_source("def (:\n", "x.py")] == [True]


def test__the_tree_scan_covers_the_overlay_and_bundles_and_main_sets_the_exit_code(tmp_path: Path, capsys):
    """Only framework/local and bundles/*/src are scanned; exit code 1 when a finding remains."""
    (tmp_path / "bundles/b/src/jobs").mkdir(parents=True)
    (tmp_path / "framework/local/python").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "bundles/b/src/jobs/t.py").write_text("rows = df.collect()\n")
    (tmp_path / "framework/local/python/o.py").write_text("n = df.count()\n")
    (tmp_path / "other/skipped.py").write_text("rows = df.collect()\n")
    assert {f.path.split(":")[0] for f in lint_tree(tmp_path)} == {
        "bundles/b/src/jobs/t.py",
        "framework/local/python/o.py",
    }
    assert main(["--root", str(tmp_path)]) == 1
    assert "2 finding(s)" in capsys.readouterr().out


def test__this_repository_is_clean():
    """The shipped pipeline and task code passes its own lint."""
    assert main([]) in (0, 1) and lint_tree(Path(__file__).parents[2]) == []
