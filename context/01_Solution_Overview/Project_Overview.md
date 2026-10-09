# Project Overview — cookie-cutter-dbx

**Status:** pre-seeded at generation time with facts the generator knew. Everything
under "Domain" is yours to fill in; everything under "Layout", "Toolchain" and
"Tasks" is already true and worth keeping accurate as the project changes.

---

## Domain

<!-- FILL THIS IN. What does this package do, and for whom? Two or three sentences
     is enough to be useful. An agent reads this before touching anything, and it is
     the difference between a change that fits the project and one that merely
     compiles. -->

`cookie-cutter-dbx` is a distributable Python package. _Describe its purpose
here._

**Users:** _who installs this and why._
**Not in scope:** _what this package deliberately does not do._ This line prevents
more scope creep than any other sentence in this file.

---

## Layout

| Path | Role |
|---|---|
| `src/dbxops/` | the package source — `src/` layout, so it is importable only via install |
| `src/dbxops/__init__.py` | the public API surface; anything re-exported here is a contract |
| `tests/unit_tests/` | unit tests, mirroring the `src/` module layout |
| `tests/fixtures/` | one module per fixture, each registered in `tests/conftest.py` |
| `tests/conftest.py` | puts `tests/..` on `sys.path` and registers fixture plugins |
| `tests/consts.py` | `THIS_DIR`, `PROJECT_DIR` |
| `pyproject.toml` | metadata, dependencies, and all tool configuration |
| `version.txt` | the single source of version truth |
| `run.sh` | task implementations in bash |
| `Makefile` | one-line wrappers over `run.sh` |
| `context/` | this spec layer |
| `LOCAL-MEMORY/` | where agent analyses and plans are written |

**Import name vs distribution name.** Installed as `cookie-cutter-dbx`,
imported as `dbxops`. They differ; keep them straight in
docs and error messages.

---

## Toolchain

| Concern | Tool | Notes |
|---|---|---|
| Packaging | `setuptools` + `pip` | `build-backend = "setuptools.build_meta"` |
| Version | `version.txt` | read dynamically via `[tool.setuptools.dynamic]` |
| Formatting | `black`, `isort` | line length **119**; isort profile `black`, `force_grid_wrap = 2` |
| Linting | `flake8` (+ `flake8-docstrings`, `radon`), `pylint` | complexity capped at **10** |
| Types | `mypy` | runs with **no `[tool.mypy]` section** — defaults only |
| Dead code | `autoflake` | removes unused imports **and variables**, in place |
| Tests | `pytest` + `pytest-cov` | `slow` marker available |
| Orchestration | `pre-commit` | also blocks direct commits to `main` |

**Python floor:** `requires-python = ">=3.7"`. Raising it is a breaking change for
installed users.

**Three things that surprise people** (see `CLAUDE.md` §8 for the full list):

1. Docstrings on public functions, methods and classes are **required** —
   `flake8-docstrings` enforces `D101`/`D102`/`D103` even though `pylint` disables its
   own `missing-function-docstring`.
2. `autoflake` runs with `--remove-all-unused-imports`, so a re-export in
   `__init__.py` that nothing references gets deleted.
3. The coverage floor is `0` (`MINIMUM_TEST_COVERAGE_PERCENT` in `run.sh`), so a green
   test run says nothing about whether your change was covered.

---

## Tasks

| Command | Effect |
|---|---|
| `make install` | editable install with all dev extras |
| `make test` | pytest + coverage into `test-reports/` |
| `make test-quick` | as above, skipping `@pytest.mark.slow` |
| `make test-wheel-locally` | build a wheel, install it in a fresh venv, test the installed package |
| `make lint` | `pre-commit run --all-files` |
| `make build` | sdist + wheel into `dist/` |
| `make publish-test` / `make publish-prod` | TestPyPI / PyPI — **irreversible** |

---

## Architecture notes

<!-- FILL THIS IN as the package grows beyond a single module. Worth recording:
     - the layers or subsystems and what each owns
     - which modules are public API and which are internal
     - any invariant a caller must maintain
     - anything that looks wrong but is deliberate, and why -->

_Single-module package at generation time. Record the structure here as it emerges._

---

## Dependencies

<!-- FILL THIS IN whenever you add a runtime dependency, with the reason. A
     dependency is a permanent cost for everyone who installs this package, and the
     reason it was added is the first thing forgotten. -->

**Runtime:** none at generation time (`[project].dependencies` is empty).
**Development:** see `[project.optional-dependencies]` — `test`, `release`, `static-code-qa`.
