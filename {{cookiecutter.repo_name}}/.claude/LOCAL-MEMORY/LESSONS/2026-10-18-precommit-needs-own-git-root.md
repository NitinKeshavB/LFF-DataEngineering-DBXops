---
artifact_type: LESSON
producer: /implement
state: verified
parent_artifact: null
created: 2026-10-18T00:00:00Z
tier: 2
verification:
  command: "pre-commit run pwd-check --verbose (local-hook reproduction in an isolated throwaway repo)"
  expected: "hook subprocess cwd equals the git toplevel, not the directory holding .pre-commit-config.yaml"
  executed: true
---

# make lint silently falls back to tool defaults when run from a nested dev copy

**What happened:** `make lint` from inside `{{cookiecutter.repo_name}}/` ran every hook and reported
success ("files were modified by this hook"), but reformatted 177 unrelated tracked files repo-wide —
collapsing `force_grid_wrap=2` imports to one line and rewrapping code at black's 88-char default
instead of this project's pinned 119. All 177 files were reverted (`git checkout --` from the outer
repo root) after confirming none of the changes were real unused-import fixes.

**Root cause:** `pre-commit` always executes each hook subprocess with cwd set to
`git rev-parse --show-toplevel` — confirmed by reproducing in an isolated throwaway repo with a
`local` debug hook that printed `pwd`. In this working copy, `{{cookiecutter.repo_name}}/` is a plain
subdirectory of the outer `lff_de_ops` git repo (not its own repo: `git rev-parse --show-toplevel`
from inside it returns the outer root). Every hook arg that points at a config file —
`--config=./pyproject.toml` (black), `--settings-path=./pyproject.toml` (isort),
`--rcfile=./pyproject.toml` (pylint), `--toml-config=./pyproject.toml` (flake8),
`--config-file=./pyproject.toml` (mypy) — is a path relative to that cwd, so it silently resolved to
the *outer* template repo's own tiny `pyproject.toml` (no `[tool.black]`/`[tool.isort]`/etc sections),
and every tool fell back to its own hardcoded defaults instead of erroring.

**Signal I missed:** `make lint`'s "Fixing ..." output looked like normal autoflake/black activity; I
didn't check *which* `pyproject.toml` each tool actually read until the diff showed 119-char lines
being rewrapped at ~88 chars.

**Why this isn't a bug in the shipped `.pre-commit-config.yaml`:** every real generated project is its
own git repo (`hooks/post_gen_project.py` step 5 runs `git init`), so the same relative paths resolve
correctly there — confirmed against the already-rendered `out/dbx-example-claims/` copy, whose
`git rev-parse --show-toplevel` is itself. `.github/workflows/ci.yml` runs `make lint-ci` in exactly
that correct layout. The break is specific to developing `{{cookiecutter.repo_name}}/` in place,
nested under the template repo, which is how this particular working copy is set up.

**Decision (user, 2026-10-18):** leave it as a known limitation rather than `git init` a nested repo
inside `{{cookiecutter.repo_name}}/` or touch the shared `.pre-commit-config.yaml`/`pyproject.toml`
that ships to every squad.

**Rule:** before trusting `make lint`'s "Passed"/"Fixing" output inside any directory that is not
itself a git toplevel, verify which `pyproject.toml` the hooks actually resolved — mass reformatting
with no real unused-import removals is the tell. To verify or lint `{{cookiecutter.repo_name}}/`
content safely, either run the specific tool directly with an **absolute** `--config`/`--rcfile`/etc.
path from within that directory (bypassing `pre-commit`), or render it with `make try` into `./out/`
(a real standalone repo) and run `make lint` there.
