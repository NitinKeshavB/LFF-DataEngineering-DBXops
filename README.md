# cookie-cutter-dbx

## Quick start

```bash
pip install cookie-cutter-dbx
```

```python
from dbxops import ...
```

## Developing/Contributing

### System requirements

You will need the following installed on your machine to develop on this codebase

- `make` AKA `cmake`, e.g. `sudo apt-get update -y; sudo apt-get install cmake -y`
- Python 3.7+, ideally using `pyenv` to easily change between Python versions
- `git`

###

```bash
# clone the repo
git clone https://github.com/<your github username>/cookie-cutter-dbx.git

# install the dev dependencies
make install

# run the tests
make test
```

## Continuous integration

`.github/workflows/build-test-publish.yml` runs on every pull request and push to
`main`: lint, then tests across Python 3.9-3.13, then a build that installs the
resulting wheel in a clean venv and tests *that* — which is the only step that
catches a module missing from the wheel.

Releases are tag-driven. Push a tag matching `version.txt` (e.g. `v0.1.0`) and the
publish jobs run: TestPyPI first, then PyPI. A tag that disagrees with `version.txt`
fails the pipeline before anything is uploaded, and publishing never happens on an
ordinary push. Add `TEST_PYPI_TOKEN` and `PROD_PYPI_TOKEN` under
*Settings > Secrets and variables > Actions*; to require human approval before a
production release, create a GitHub Environment named `pypi` with a required reviewer.

## AI agent harness

This project ships a [Claude Code](https://claude.com/claude-code) harness: a set of
rules, slash commands and skills that make an AI coding agent investigate before it
acts, cite `file:line` for every claim, keep changes surgical, and prove a change by
running something rather than by asserting it works.

| Path | Purpose |
|---|---|
| `CLAUDE.md` | the operational doctrine — read first |
| `.claude/commands/` | slash commands |
| `.claude/skills/` | the operational loops the commands invoke |
| `context/` | this project's spec layer — **fill in the glossary** |
| `LOCAL-MEMORY/` | where analyses and plans are written |

### Commands

| Command | Use it for |
|---|---|
| `/qa` | run lint, tests and coverage, and triage what fails |
| `/explain <target>` | a cited mental model of how some code works |
| `/bug-why <error>` | root cause analysis — read-only, requires a repro that fails |
| `/bug-fix <analysis>` | the surgical fix, failing-test-first |
| `/howto-implement <feature>` | ranked approaches and testable acceptance criteria, before any code |
| `/implement <plan>` | execute the plan as a surgical change |
| `/verify [target]` | adversarially check a change, plan or diagnosis |

Most work needs two: one to analyse, one to execute. `/qa` and `/verify` are the
guard rails around them.

### Getting the most out of it

**Fill in `context/02_Domain_Knowledge/Domain_Glossary.md` early.** It is the
highest-leverage file in the repo and it starts empty. One agreed name per concept,
with the synonyms to avoid, is what stops the same idea becoming four different names
across four modules — cheap now, expensive to retrofit later.

Then keep `context/01_Solution_Overview/Project_Overview.md` honest as the package
grows. Its layout and toolchain sections were pre-filled at generation time and are
accurate today.

The doctrine was generated at the `full` weight. The
`lean` doctrine covers the core: evidence, refuters, surgical changes and the
verification loop. The `full` doctrine adds vocabulary locking, pattern reuse, repro
kernels and reflexion lessons — worth adopting once the project has real domain terms
and established patterns. Regenerating with `harness_doctrine=full` shows the
additional sections.

Adapted from the Claude Code Agent Harness by Viacheslav Tronko (MIT) — see
`HARNESS-ATTRIBUTION.md`.
