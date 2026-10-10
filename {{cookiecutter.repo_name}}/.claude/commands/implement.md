---
description: "Execute a plan against cookie-cutter-dbx as a surgical code change, proven by a real test run. Triggers: /implement, \"implement this\", \"write the code\", \"build this\", \"execute the plan\", \"add this function\", or handing over a blueprint and asking to carry it out."
argument-hint: "<path to a blueprint | step number | description of the work>"
allowed-tools: Read, Edit, Write, Glob, Grep, Bash, Agent
---

# /implement — surgical execution

Invoke the **`surgical-implementation`** skill in **IMPLEMENT mode** on `$ARGUMENTS`.

**This modifies source.** `CLAUDE.md` P0 #1, #2, #3, #5, #6 and #10 are in force.

`$ARGUMENTS` empty → reply `/implement requires a contract. Usage: /implement <blueprint path | step | description>` and stop.

- **No plan and the work is Tier 2+?** Stop and run `/howto-implement` first. Do not
  improvise a change spanning multiple modules.
- **Consuming a blueprint?** Re-read every `file:line` it cites. Report drift and stop
  rather than building on a stale plan.
- **Read before every edit**, quoting 3 anchor lines (P0 #1). Prefer `Edit` over
  `Write` on an existing file — `Write` silently discards what you did not know was there.
- **Docstrings are mandatory** on every new public function, method and class.
  `flake8-docstrings` enforces `D101`/`D102`/`D103` even though `pylint` disables its
  own check — the most common avoidable `make lint` failure.
- **Declare every new import** in `pyproject.toml` (P0 #6), and verify it resolves.
- **Public surface is a contract.** Anything added to
  `src/dbxops/__init__.py` is permanent for installed
  users — say so explicitly (P0 #5).
- **Nothing from the forbidden list** in the diff: reformatting, import reordering,
  renames, added logging, adjacent refactors, dependency bumps (P0 #2).
- **Diff budget:** justify over 20 lines, ask over 50, refuse over 100 and re-plan.
- **Prove it:** the target test, `make test`, `make lint` — **transcripts pasted** (P0 #3).
  `make lint` rewrites files, so re-run it to confirm it settles and re-read anything
  it touched.

End with `In one sentence: <what changed>; verified by <command> = PASS.`
