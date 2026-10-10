# Tasks for developing the cookiecutter itself. Tasks for a generated project are in its own Makefile.
.PHONY: help install test test-e2e try

help:
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*## /\t/'

install: ## install what the template tests need (cookiecutter, pytest, PyYAML)
	python -m pip install "cookiecutter>=2.6" pytest pyyaml

test: ## fast tests: render projects and inspect them (no network, a few seconds)
	python -m pytest tests

test-e2e: ## slow tests: render a project, install it and run its own validation and tests (network, about 8 minutes)
	python -m pytest tests -m slow -o addopts=""

try: ## render a project with the default answers into ./out/ to look at it
	cookiecutter --no-input -o out .
