# cookiecutter-dbxops

A [cookiecutter](https://cookiecutter.readthedocs.io/) template for a **metadata-driven Databricks platform**: DataOps, MLOps and AIOps built
from declarative [Lakeflow Framework](https://github.com/databricks-solutions/lakeflow_framework) dataflow specs, with every table, product,
model and endpoint described in [Ontos](https://github.com/databrickslabs/ontos)-ready metadata and published to one shared database.

Run it once and you get a repository of your own, with the whole platform already in it: the `dbxops` library, example DataOps, MLOps and
AIOps bundles, the metadata tooling, CI/CD workflows, the squad-registry scaffold and a [Claude Code](https://claude.com/claude-code)
harness. Then you start building.

> **Status.** The generated project is verified offline: its tests, the Databricks CLI's own bundle schema, the Lakeflow Framework's own
> validator, `actionlint` and `shellcheck` all pass. **Nothing has run against a real workspace, database, GitHub organisation or Ontos
> instance.** The honest list of gaps ships with every project, in `context/06_Operations/Known_Gaps.md`.

## Quick start

You need Python 3.11+ and `git`. `make`, and the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/) for the bundle-schema tests, are used
by the generated project.

```bash
# install cookiecutter into an isolated virtual environment
python -m venv ./venv/
source ./venv/bin/activate
pip install --upgrade pip
pip install cookiecutter

# generate your repository, answering the prompts
cookiecutter https://github.com/NitinKeshavB/cookiecutter-dbxops.git
```

Then, in the new directory:

```bash
git add -A && git commit -m "Initial commit from cookiecutter-dbxops"
make install              # editable install with every extra (use a virtual environment)
make validate-metadata    # contracts, products, domains, specs, snapshots: 0 errors
make test                 # about 600 tests
make check-placeholders   # everything you still have to fill in
```

The generated `README.md` continues from there ("Start building"): fill in the placeholders, add your first bundle, register the squad.

### Prompts

| Prompt | Meaning | Default |
|---|---|---|
| `project_id` | the squad's slug: prefixes every schema, contract and product id; `^[a-z][a-z0-9_]{2,39}$` | `example_claims` |
| `repo_name` | the directory and the Python distribution name; `^dbx-[a-z0-9-]{3,60}$` | `dbx-<project_id>` with `-` for `_` |
| `squad_title` | the squad's display name | `Example Claims Squad` |
| `kind` | `project` (consumes domain data products) or `domain` (builds them; may only hold data in its own domain) | `project` |
| `domain` | the squad's place in the domain taxonomy, any level: `claims`, `claims__litigation`, `claims__litigation__claim_litigation` | `claims` |
| `include_examples` | `yes` keeps the three example bundles; `no` removes them and the tests written for them | `yes` |

Answers are checked before anything is written, against the same rules the registry applies, so a project made here can later be registered
without renaming anything. `domain` must exist in the taxonomy shipped with the template (16 business domains, 62 sub-domains, 82 data
domains); a typo gets a "did you mean". A `domain` repository needs `include_examples=no`, because the examples span several business domains.

Non-interactive:

```bash
cookiecutter --no-input https://github.com/NitinKeshavB/cookiecutter-dbxops.git \
    project_id=claims_fraud squad_title="Claims Fraud Squad" kind=domain domain=claims__litigation include_examples=no
```

## What you get

```
dbx-claims-fraud/
├── src/dbxops/              # the library: metadata, registry, metastore (platform_meta), config, lifecycle
├── bundles/                 # dataops, mlops, aiops example bundles (or empty); add yours with `make new-bundle`
├── metadata/_platform/      # your squad's registration snapshot: repo, team, domain taxonomy, glossary (read-only)
├── conf/                    # environments (dev..prod) and the metadata database sinks
├── framework/               # the Lakeflow Framework (pinned submodule, read-only) + our overlay and wrapper bundle
├── templates/registry-repo/ # scaffold of the platform team's registry repository
├── .github/workflows/       # ci, cd, deploy-bundle, deploy-framework, lakeflow-upstream, build-test-publish
├── .claude/                 # settings, commands, skills (project + vendored Databricks, MLflow, framework skills)
├── context/                 # the spec layer: architecture, conventions, operations runbooks, glossary
├── tests/  scripts/  CLAUDE.md  Makefile  run.sh  pyproject.toml  version.txt
└── .git/  .gitmodules       # initialised for you; the framework submodule is added at the pinned commit
```

## How generation works

1. **Prompts** (`cookiecutter.json`), then `hooks/pre_gen_project.py` rejects malformed answers.
2. **Copy.** The template under `{{cookiecutter.repo_name}}/` is copied *verbatim* (`_copy_without_render: ["*"]`). It is a complete, working
   repository, not a Jinja template: workflows, skills and job files are full of `${{ ... }}`, and keeping the tree free of placeholders means
   its own tests run in place. The example squad `example_claims` is the stand-in identity.
3. **`hooks/post_gen_project.py`** then makes it yours:
   - checks `domain` against the taxonomy (with the project's own `dbxops.registry.domains`);
   - removes the example bundles if asked;
   - replaces the example identity (id, repo name, GitHub team, AD group token, title) in every text file, and the distribution name
     `cookie-cutter-dbx` with `repo_name` (except in the registry scaffold, which installs the *published* library);
   - rewrites the four `metadata/_platform` snapshots for your answers and recomputes their hash headers (a test proves the result is
     byte-identical to what the registry itself would render);
   - runs `git init` and `scripts/add_framework.sh`, which adds the Lakeflow Framework as a submodule at the commit in `framework/UPSTREAM.lock`.
     If that fails (no network), the hook says so and how to retry; the project is still created.

The import name stays `dbxops` in every generated project, on purpose: it is the platform's library, shared tooling and docs refer to it, and
renaming it would touch about 600 files for no benefit to a squad.

## Developing the template

The repository root is the cookiecutter; everything a squad receives is under `{{cookiecutter.repo_name}}/`. Work there as in any repository
(its `Makefile`, `CLAUDE.md` and tests apply when you `cd` into it, with the framework checked out).

```bash
make install     # cookiecutter, pytest, PyYAML
make test        # fast: renders projects and inspects them (a few seconds, no network)
make test-e2e    # slow: renders, installs and runs the generated project's own validation and tests (network, about 4 minutes per variant)
make try         # render the defaults into ./out/
```

`DBXOPS_TEST_FRAMEWORK_URL=<local clone of the framework>` makes `make test-e2e` independent of GitHub.

Rules of thumb:

- **Keep the template tree free of `{{`-style placeholders of your own.** All customisation is hook code on a short list of known tokens
  (`identity_replacements` in `hooks/post_gen_project.py`). Adding a prompt means: `cookiecutter.json`, the hook, a test in `tests/`.
- **A test in the template must not hard-code the example squad's values** (`claims`, `example_claims`) unless the hook rewrites them, and must not
  use an id a real squad might choose (the registry tests use `intake_probe`).
- Hooks are rendered by Jinja before they run, so avoid `{{`, `{%` and `{#` in them other than the prompts.
- The framework submodule is **not** in this repository. To move the pin for new projects, generate a project, run `make upgrade-lakeflow TAG=<tag>`
  there, and copy back the files it changed (`git status` lists them).

## Known limitations

- Needs network on first generation (the framework submodule), unless you point `DBXOPS_FRAMEWORK_URL` at a mirror.
- Each generated repository carries its own copy of `dbxops`. Fixes to the library reach squads by regenerating or merging from the template;
  there is no automatic update channel yet.
- With `include_examples=no`, the `cd.yml` workflow has no bundle to deploy until you add one (an empty matrix fails); `make new-bundle` fixes that.
- The generated `metadata/_platform/` is a stand-in with placeholder owner emails. The registry owns the real one.
