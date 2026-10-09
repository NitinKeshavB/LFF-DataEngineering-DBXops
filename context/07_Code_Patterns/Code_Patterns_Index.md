# Code Patterns Index — cookie-cutter-dbx

Repeating patterns in this codebase, to be **reused rather than reinvented**. The
`B-XXX` rows below are already true of this project at generation time — they describe
real conventions in the files you have. Add to them as the package grows.

`B-XXX` = package / library pattern · `D-XXX` = data or packaging pattern.
`state`: `stable` · `experimental` · `deprecated`. **Never base new code on a
`deprecated` pattern.**

Under the `full` doctrine, cite the pattern ID you followed in your Checkpoint and
register genuinely new repeating patterns here in the same change (`CLAUDE.md` §12).

## Package patterns (B-XXX)

| ID | Name | Description | State | Anchor |
|---|---|---|---|---|
| B-001 | Task function + Makefile wrapper | Real logic goes in a `run.sh` bash function; the `Makefile` target is a one-line `bash run.sh <fn>` wrapper. Gives tab-completion and chaining without a task-runner dependency. | stable | `run.sh`, `Makefile` |
| B-002 | Fixture module + conftest registration | Each pytest fixture lives in its own `tests/fixtures/<name>.py` and is registered in the `pytest_plugins` list in `tests/conftest.py`. **An unregistered fixture module is never discovered.** | stable | `tests/conftest.py`, `tests/fixtures/` |
| B-003 | Test layout mirrors source layout | `tests/unit_tests/` mirrors the module structure of `src/dbxops/`, so the test for a module is findable by path. | stable | `tests/unit_tests/` |
| B-004 | Public surface via `__init__.py` re-export | The importable API is what `src/dbxops/__init__.py` re-exports; everything else is internal. Note `autoflake` strips re-exports nothing references. | stable | `src/dbxops/__init__.py` |
| B-005 | Slow tests behind a marker | Anything slow is marked `@pytest.mark.slow` so `make test-quick` can skip it while `make test` still runs it. | stable | `[tool.pytest.ini_options]` in `pyproject.toml` |

## Data / packaging patterns (D-XXX)

| ID | Name | Description | State | Anchor |
|---|---|---|---|---|
| D-001 | Single source of version truth | The version lives only in `version.txt`; `pyproject.toml` reads it via `[tool.setuptools.dynamic]`. **Never hardcode a version anywhere else.** | stable | `version.txt`, `pyproject.toml` |
| D-002 | Optional dependency groups | Dev tooling is grouped into `test`, `release` and `static-code-qa` extras, aggregated by a `dev` extra, so CI can install only what a job needs. | stable | `[project.optional-dependencies]` |
| D-003 | `src/` layout, install to import | Source lives under `src/`, so the package is importable only after `make install` does an editable install. This is deliberate: it stops tests passing against the working tree while the built package is broken. | stable | `pyproject.toml`, `run.sh` |

## Adding a pattern

Register it here once the same shape appears a **third** time — not on the first.
Give it the next free ID (never renumber), one line of description, a `state`, and an
anchor file so a reader can see a working instance. If a pattern is superseded, mark
it `deprecated` and name its replacement rather than deleting the row.
