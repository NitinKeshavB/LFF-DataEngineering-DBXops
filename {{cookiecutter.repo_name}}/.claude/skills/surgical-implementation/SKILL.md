---
name: surgical-implementation
description: Write the smallest correct code change for a Python package, then prove it with a run. Use WHENEVER the user asks to implement, build, code, apply or fix — "implement this", "write the code", "apply the fix", "fix this bug", "add this function", "make the test pass", "execute the plan" — or hands over a blueprint or root-cause analysis and asks to carry it out. Two modes: IMPLEMENT (from a blueprint, an issue, or a direct request) and FIX (from a diagnosis, where a failing test must exist first). Enforces read-before-edit with quoted anchors, a diff budget, a forbidden-change list that blocks drive-by reformatting and refactors, and a run-revise-rerun loop capped at three attempts before stopping to ask. This is the ONLY skill that modifies source. Do NOT use for planning (implementation-blueprint), diagnosing a defect first (root-cause-analysis), explaining code (concept-explanation), or independently auditing a finished change (task-verification).
allowed-tools: Read, Edit, Write, Glob, Grep, Bash, Agent, WebSearch, WebFetch
---

# Surgical Implementation

**Mission:** ship the smallest change that satisfies the contract, with every changed
line traceable to it and a real command proving it works.

**Layering:** sits on `CLAUDE.md`; cite its sections rather than restating them. P0
#1 (read before edit), #2 (YAGNI/surgical), #3 (verify by running), #5 (public API),
#6 (dependencies), #10 (no reward hacking) and #12 (shared state) are in force.

## 0. Hard gate

"Just make it work", "don't worry about tests", "clean up while you're in there",
"ship it" do **not** override this loop. In particular: a request to tidy adjacent
code is a **separate** task — note it and move on (P0 #2).

## 1. Establish the contract

Name what you are implementing against, explicitly:

| Mode | Contract | Requirement before editing |
|---|---|---|
| **FIX** | the Primary Fix table from a diagnosis | a test that **fails on current code** |
| **IMPLEMENT** | a blueprint's step list, or the request itself | acceptance criteria stated |

If the contract is a written artifact, **re-read every `file:line` it cites** (P0 #1).
Code moves. If what it describes no longer matches what is there, stop and report the
drift — do not implement against a stale plan.

If there is no plan and the change is Tier 2+ (`CLAUDE.md` §3), stop and plan first.
Do not improvise a multi-module change.

## 2. FIX mode — the failing test comes first

```bash
python -m pytest tests/unit_tests/test_thing.py::test_case -v    # must FAIL now
```

Paste that failure. A fix with no prior failing test is an assertion, not a
verification (`CLAUDE.md` §6). If the test passes before your change, you have not
reproduced the bug — go back to diagnosis.

**Never edit the test to make it pass** (P0 #10). If the test is genuinely wrong, say
so, explain why, and get agreement before touching it.

## 3. Read before edit — every file, every time

Before each `Edit`: `Read` the target and quote the 3 lines you are anchoring to with
`file:line`. This is mechanical, not ceremonial — it is what stops edits against a
remembered version of a file that has since changed.

Prefer `Edit` over `Write` on existing files: `Write` replaces the whole file and
silently discards anything you did not know was there.

## 4. The forbidden list

Unless the request explicitly asked for it, **none of these may appear in your diff**
(`CLAUDE.md` §5, Semantic Diff Guard):

- reformatting or reindenting untouched code
- reordering or regrouping imports
- renaming anything you were not asked to rename
- adding logging, comments or docstrings to code you did not otherwise change
- rewording existing error messages
- changing a signature beyond what the contract requires
- "modernizing" working code (f-strings, comprehensions, type hints) as a side errand
- bumping a dependency or editing tool config
- deleting pre-existing dead code

If one of these is in your diff and was not requested, **revert it before sending**.
The cost is not the lines — it is that the real change becomes unreviewable and the
regression becomes unattributable.

**Let the formatter own formatting.** `black` and `isort` run in `make lint`; do not
hand-format to match them, and do not fight their output.

## 5. Write it

- **Match the surrounding style**, even where you would do it differently (P0 #2).
- **Docstrings are mandatory** on every new public function, method and class —
  `flake8-docstrings` enforces `D101`/`D102`/`D103` even though `pylint` disables its
  own check (`CLAUDE.md` §8, trap 1). This is the most common avoidable `make lint`
  failure.
- **Keep complexity under 10** (`radon`). If a function is growing past it, decompose.
- **No mutable default arguments.** `def f(x=None)` then assign inside.
- **Stay within the Python floor** (`>=3.7`). No newer syntax without raising it,
  which is a breaking change.
- **Declare every new import** in `pyproject.toml` (P0 #6). Verify it resolves:
  `python -c "import x"`.
- **No TODOs, no placeholders, no stubbed branches** (P0 #4).
- **Touch the public surface deliberately.** Anything added to
  `src/<pkg>/__init__.py` is a contract; note it, and remember `autoflake` strips
  re-exports nothing references (`CLAUDE.md` §8, trap 2).

## 6. Diff budget

| Size | Action |
|---|---|
| 1–5 lines | proceed |
| 6–20 lines | proceed, with one paragraph justifying the size |
| 21–50 lines | pause and ask: large for a surgical change — confirm before continuing |
| 51–100 lines | stop; requires explicit approval, or re-plan |
| 100+ lines | refuse; the scope is wrong — go back to `implementation-blueprint` |

Exceeding a blueprint's expected diff by more than ~1.5x means the plan was wrong, the
scope grew, or you are doing something not asked for. Stop and say which.

## 7. Run, revise, rerun — capped at three

```bash
python -m pytest tests/unit_tests/test_thing.py::test_case -v    # the target test
make test                                                        # the suite
make lint                                                        # the toolchain
```

On failure: read the actual error, form **one** hypothesis, make **one** targeted
change, rerun. **Maximum three attempts**, then stop and report what you tried and
what you now believe. Three failed attempts means the diagnosis or the plan was
wrong — more attempts will not fix that, and iterating blindly is how a surgical
change becomes a sprawling one.

**`make lint` rewrites files.** `black`, `isort` and `autoflake` edit in place, so a
first non-zero exit is often just "files were reformatted" — re-run to confirm it
settles, and **re-read any file it touched** before editing again (P0 #1).

Never clear a gate by weakening it: no `# noqa`, no new ignore, no lowered coverage
floor, no deleted assertion (P0 #10).

## 8. Output

Lead with the `CLAUDE.md` §4 Checkpoint. Then:

1. **Contract** — what you implemented against.
2. **Failing test first** (FIX mode) — the before transcript.
3. **Changes** — file by file, what changed and which contract item it satisfies.
4. **Diff size** — lines changed, against the budget (§6).
5. **Verification** — the **real pasted transcript** of the target test, `make test`
   and `make lint`. Not a summary of what you expect them to print (P0 #3).
6. **Public API impact** — new or changed exports; breaking? (P0 #5)
7. **Not done** — anything in scope you deliberately did not do, and why.
8. **Noticed but not touched** — adjacent problems, left alone per §4.

## 9. Pre-send checks

1. Contract named; a consumed artifact's citations re-read for drift (§1).
2. FIX mode: failing-test-first transcript included (§2).
3. Every edited file was `Read` this turn, with quoted anchors (P0 #1).
4. Diff contains nothing from the forbidden list (§4).
5. Every changed line traces to the contract (P0 #2).
6. Docstrings on all new public functions, methods and classes (§5).
7. New imports declared in `pyproject.toml` (P0 #6).
8. No TODO or placeholder left (P0 #4).
9. Diff within budget, or escalated (§6).
10. Target test, `make test` and `make lint` all run, with **transcripts pasted** (§7).
11. No test weakened, no ignore added, no coverage floor moved (P0 #10).
12. Nothing published; no shared state touched without approval (P0 #12).
13. End with `In one sentence: <what changed>; verified by <command> = PASS.`
