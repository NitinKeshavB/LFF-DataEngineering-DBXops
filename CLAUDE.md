# CLAUDE.md — cookiecutter-dbxops (the template repository)

This repository is a **cookiecutter**. The root holds only the template machinery: `cookiecutter.json`, `hooks/`, `tests/`, `Makefile`.
Everything a squad receives is under `{{cookiecutter.repo_name}}/`, and that directory has its own, much longer `CLAUDE.md` (the doctrine)
that applies to the code in it. **Read `{{cookiecutter.repo_name}}/CLAUDE.md` before changing anything under it.**

## Rules here

1. **Nothing is rendered.** `_copy_without_render` is `["*"]`: the template tree is a working repository. Never add a Jinja variable to it.
   Customisation is the post-generation hook, on a short list of known tokens.
2. **Hooks are Jinja-rendered once before they run.** Do not write `{{`, `{%` or `{#` in them except to read a prompt.
3. **Hooks run in cookiecutter's environment**: standard library + PyYAML only. No `dbxops` imports; load a module by path if you must.
4. **The framework submodule is not in this repo.** `scripts/add_framework.sh` (in the template) adds it from `framework/UPSTREAM.lock`.
5. **Tests in the template must not hard-code the example squad** unless the hook rewrites it.
6. **Verify by generating**: `make test` (fast) and, for anything that touches the hook, the template tree or packaging, `make test-e2e`.
7. Shared state needs consent: tags, pushes, `.github/workflows/`, `version.txt` (see the template's `CLAUDE.md` P0 #12).

## Commands

| | |
|---|---|
| `make test` | render projects and inspect them (seconds) |
| `make test-e2e` | render, install and run a project's own checks (minutes, network) |
| `make try` | render the defaults into `./out/` |
