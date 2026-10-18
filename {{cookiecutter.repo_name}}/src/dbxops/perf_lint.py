"""
A static check for worker-efficiency anti-patterns in pipeline and task code.

    python -m dbxops.perf_lint [--root .]

It parses every Python file under ``framework/local`` and ``bundles/*/src`` and reports, as errors:

* ``collect()``, ``toPandas()``, a zero-argument ``count()`` and ``display()``, which pull data to the driver
* ``coalesce(1)``, which funnels a write through one task
* a global ``orderBy``/``sort``, and a ``Window.orderBy`` with no ``partitionBy``
* a row-wise Python ``udf`` (use a built-in function, SQL, or ``pandas_udf``)
* a JDBC read with no ``numPartitions``

A call is exempt when any line of its statement carries ``# perf-lint: allow <rule> -- <why>``.
Exit code 0 when clean, 1 when a finding remains.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dbxops.findings import Finding

RULE = "perf-lint"
DRIVER_CALLS = {"collect": "collect()", "toPandas": "toPandas()", "display": "display()"}
SORT_CALLS = frozenset({"orderBy", "sort", "sortWithinPartitions"})
SCAN_DIRS = ("framework/local", "bundles/*/src")
ALLOW = "# perf-lint: allow"


def _chain_names(node: ast.AST) -> list[str]:
    """Return the attribute and call names of a method chain, from the receiver outwards."""
    names: list[str] = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            names.append(node.id)
            break
        else:
            break
    return names[::-1]


def _is_window_chain(names: Sequence[str]) -> bool:
    return "Window" in names or "W" in names or "partitionBy" in names


@dataclass(frozen=True)
class _Call:
    """One call with its method name and the names along its receiver chain."""

    node: ast.Call
    name: str
    chain: Sequence[str]


def _driver_check(call: _Call) -> str | None:
    if call.name in DRIVER_CALLS:
        return f"{DRIVER_CALLS[call.name]} moves data to the driver"
    node = call.node
    if call.name == "count" and not node.args and not node.keywords and isinstance(node.func, ast.Attribute):
        return "count() runs a full job; use a metric or a bounded check"
    return None


def _coalesce_check(call: _Call) -> str | None:
    args = call.node.args
    if call.name == "coalesce" and args and isinstance(args[0], ast.Constant) and args[0].value == 1:
        return "coalesce(1) writes through a single task"
    return None


def _sort_check(call: _Call) -> str | None:
    if call.name not in SORT_CALLS or not isinstance(call.node.func, ast.Attribute):
        return None
    if "Window" in call.chain and "partitionBy" not in call.chain:
        return "a window with no partitionBy moves every row to one task"
    if not _is_window_chain(call.chain) and call.name != "sortWithinPartitions":
        return f"{call.name}() is a global sort; sort within partitions or drop it"
    return None


def _udf_check(call: _Call) -> str | None:
    if call.name == "udf":
        return "a row-wise Python udf is slow; use a built-in function, SQL, or pandas_udf"
    return None


CALL_CHECKS = (_driver_check, _coalesce_check, _sort_check, _udf_check)


def _call_problem(node: ast.Call) -> str | None:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    call = _Call(node, name, _chain_names(func))
    return next((problem for check in CALL_CHECKS if (problem := check(call))), None)


def _allowed(lines: Sequence[str], first: int, last: int) -> bool:
    return any(ALLOW in lines[number - 1] for number in range(first, last + 1) if 0 < number <= len(lines))


def _jdbc_problem(statement: ast.stmt, source: str) -> str | None:
    text = ast.get_source_segment(source, statement) or ""
    jdbc = ".jdbc(" in text or '"jdbc"' in text or "'jdbc'" in text
    if jdbc and "numPartitions" not in text and not isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
        return "a JDBC read needs numPartitions, partitionColumn and bounds to read in parallel"
    return None


def _statement_spans(tree: ast.AST) -> dict[int, tuple[int, int]]:
    """Map each call to the first and last line of the simple statement that holds it."""
    spans: dict[int, tuple[int, int]] = {}
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.stmt) or isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        for child in ast.walk(statement):
            if isinstance(child, ast.Call):
                spans.setdefault(id(child), (statement.lineno, statement.end_lineno or statement.lineno))
    return spans


def _call_findings(tree: ast.AST, lines: Sequence[str], path: str) -> list[Finding]:
    spans = _statement_spans(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        problem = _call_problem(node)
        if problem and not _allowed(lines, *spans.get(id(node), (node.lineno, node.lineno))):
            findings.append(Finding(RULE, f"{path}:{node.lineno}", problem))
    return findings


def _decorator_findings(tree: ast.AST, lines: Sequence[str], path: str) -> list[Finding]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            names = _chain_names(decorator)
            if "udf" in names and "pandas_udf" not in names and not _allowed(lines, decorator.lineno, node.lineno):
                findings.append(Finding(RULE, f"{path}:{decorator.lineno}", "a row-wise Python udf is slow"))
    return findings


def _jdbc_findings(tree: ast.AST, source: str, lines: Sequence[str], path: str) -> list[Finding]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt) or isinstance(node, ast.ClassDef):
            continue
        problem = _jdbc_problem(node, source)
        if problem and not _allowed(lines, node.lineno, node.end_lineno or node.lineno):
            findings.append(Finding(RULE, f"{path}:{node.lineno}", problem))
    return findings


def lint_source(source: str, path: str) -> list[Finding]:
    """Return the findings of one Python source text; a file that does not parse yields one finding."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [Finding(RULE, path, f"cannot parse: {error.msg} (line {error.lineno})")]
    lines = source.splitlines()
    findings = [
        *_call_findings(tree, lines, path),
        *_decorator_findings(tree, lines, path),
        *_jdbc_findings(tree, source, lines, path),
    ]
    return sorted(set(findings), key=lambda f: (f.path, f.message))


def lint_tree(root: Path) -> list[Finding]:
    """Lint every Python file under the scanned folders of ``root``."""
    findings: list[Finding] = []
    for pattern in SCAN_DIRS:
        for folder in sorted(root.glob(pattern)):
            for file in sorted(folder.rglob("*.py")):
                if "__pycache__" in file.parts:
                    continue
                findings += lint_source(file.read_text(encoding="utf-8"), file.relative_to(root).as_posix())
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check and return the exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m dbxops.perf_lint", description=__doc__.split("\n\n", maxsplit=1)[0]
    )
    parser.add_argument("--root", default=".", help="repository root")
    findings = lint_tree(Path(parser.parse_args(argv).root))
    for finding in findings:
        print(finding)
    print(f"perf-lint: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
