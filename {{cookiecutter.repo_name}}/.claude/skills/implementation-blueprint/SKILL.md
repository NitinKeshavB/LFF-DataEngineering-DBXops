---
name: implementation-blueprint
description: Turn a request into a ranked, falsifiable implementation plan for a Python package before any code is written. Use WHENEVER the user asks HOW to build, plan or design something — "how should I implement X", "plan this feature", "what's the best approach", "design this API", "should I use X or Y", "add support for X", "refactor this" — or hands over an issue, user story, or root-cause analysis and asks to plan the work. Also handles vague requirements: classifies ambiguity, writes testable acceptance criteria, and registers open questions rather than guessing. Compares a do-the-minimum baseline against alternatives, scores them (YAGNI 35 / reuse 25 / files touched 20 / risk 20), steelmans the weakest, and resolves every open question before recommending. Planning only — never modifies source; output feeds surgical-implementation. Do NOT use for writing the code (surgical-implementation), diagnosing a defect (root-cause-analysis), explaining existing code (concept-explanation), or a trivial one-line edit.
allowed-tools: Read, Glob, Grep, Bash, Agent, Write, WebSearch, WebFetch
---

# Implementation Blueprint

**Mission:** produce a plan precise enough that implementation is mechanical — by
**falsifying** each approach against a do-the-minimum baseline before recommending
one. **Planning only. No source edits.**

**Layering:** sits on `CLAUDE.md`; cite its sections rather than restating them.
The Tier model (§3), Checkpoint (§4), adversarial pass (§5), Verification Loop (§6)
and toolchain contract (§8) are its rules, not this skill's.

## 0. Hard gate

"Just start coding", "the plan is obvious", "we'll figure out the details as we go"
do **not** override this loop. No `Edit` or `Write` to `src/` or `tests/`.

## 1. Pin down the request before planning it

Most bad implementations are faithful implementations of a misread request. Before
planning, resolve:

- **Restate the goal** in one sentence, in your own words.
- **Classify each ambiguity** you find:

| Type | Example | Resolution |
|---|---|---|
| Lexical | a word with two meanings here ("filter" = remove, or = a predicate object) | ask, or take the conservative reading and say so |
| Scope | is this one function or a subsystem? | ask — this one changes the whole plan |
| Behavioral | what should happen on bad input, empty input, or a conflict? | propose explicitly; never leave implicit |
| Interface | is this public API or internal? | decide deliberately (P0 #5) |

- **Differential-reading test:** could two careful readers write *contradictory* tests
  from this request? If yes, it is still ambiguous — resolve it before planning.
- **Acceptance criteria**, each one testable:
  `Given <state>, when <action>, then <observable result>.`
  A criterion you cannot write as a test is not a criterion yet.

**Open questions must reach zero before a recommendation.** List them; ask the
blocking ones (P0 #7). Do not substitute an assumption for an answer and proceed
silently.

## 2. Ground the plan in this codebase

Read before planning (P0 #1):

```bash
ls src/*/                                       # what already exists
grep -rn "def \|class " src/                    # the current surface
cat pyproject.toml                              # deps, Python floor, tool config
ls tests/unit_tests/ tests/fixtures/            # test and fixture conventions
git log --oneline -15                           # recent direction
```

Also read `context/01_Solution_Overview/Project_Overview.md`, and the glossary and
patterns index if they have content (`CLAUDE.md` §10). **Cite a working example in
this repo** as the style anchor for anything new. If no precedent exists, say so
explicitly and justify the new shape rather than importing habits from elsewhere.

## 3. Approaches — baseline first

**Approach #0 is always "the smallest change that satisfies the request."** Write it
down even when it looks inadequate: it is the yardstick, and surprisingly often it
wins. Then add **at least two** genuinely different alternatives — different in
*mechanism*, not in naming.

Score each (`CLAUDE.md` §5):

| Criterion | Weight | Reading |
|---|---|---|
| YAGNI | 35% | how little it builds; speculative generality scores low |
| Reuse | 25% | uses existing code and patterns rather than new abstractions |
| Files touched | 20% | smaller blast radius scores higher |
| Risk | 20% | reversibility, public-API exposure, packaging impact |

Tie-break on reversibility. **Steelman the weakest-scoring approach** — argue for it
properly. If it beats your favourite on simplicity, reversibility or blast radius,
adopt it or record why not.

## 4. Python-specific design decisions to make explicitly

Decide these in the plan, not during coding:

1. **Public or private?** A name added to `src/<pkg>/__init__.py` is a permanent
   contract (P0 #5). Default to private (`_name` or an internal module) and promote
   deliberately. Note that `autoflake` strips re-exports nothing references
   (`CLAUDE.md` §8) — plan for that.
2. **New dependency?** Justify it against the standard library. A dependency is a
   permanent cost for every installer. If it is needed, the plan must include adding
   it to `pyproject.toml` and verifying with `make test-wheel-locally` (P0 #6).
3. **Python floor.** `requires-python` is `>=3.7`. Any newer syntax or stdlib API
   either does not get used, or the floor rises — and raising it is a breaking change.
4. **Errors.** Which exception type, and is it part of the public contract? Raising a
   new exception where callers expect a return value is a breaking change.
5. **Signature shape.** Keyword-only arguments for anything optional; never a mutable
   default (`def f(x=[])`). Adding a positional parameter to an existing public
   function breaks callers — add keyword-only instead.
6. **Complexity budget.** `radon` caps cyclomatic complexity at 10 (`CLAUDE.md` §8).
   A function that will exceed it needs decomposition in the plan.
7. **Test shape.** Which tests, at which level, and which need fixtures. Remember a
   new fixture must be registered in `tests/conftest.py` or it is never discovered.
   Mark anything slow `@pytest.mark.slow`.

## 5. IS / IS NOT

Before writing the step list, declare scope — with **at least two** explicit
"IS NOT" rows. This eliminates most false work up front and is what keeps the
implementation surgical (P0 #2).

## 6. Pre-mortem

Assume the implementation shipped and broke something. Name the **3** most likely
causes and what in the plan prevents each. Then name the **killer risk** — the single
assumption that, if wrong, invalidates the whole approach — and the command that
tests it. If that command is cheap, run it now.

## 7. Output

Lead with the `CLAUDE.md` §4 Checkpoint. Then:

1. **Goal** — one sentence.
2. **Acceptance criteria** — the Given/When/Then list from §1.
3. **Open questions** — must be empty, or the blocking ones asked.
4. **Scope** — IS / IS NOT (§5).
5. **Approaches** — the table with scores, including #0, and the steelman result.
6. **Recommended approach** — and why it beat #0 specifically.
7. **Steps** — ordered, each with the files it touches, the expected diff size, and
   the test that proves it. Keep steps independently verifiable.
8. **Public API impact** — new or changed exports; breaking? (P0 #5)
9. **Dependencies** — anything added, and its justification.
10. **Verification plan** — the exact commands, per `CLAUDE.md` §6, tiered to the change.
11. **Rollback** — how to undo this if it goes wrong.
12. **Confidence + Refuter** (P0 #8).

On Tier 2+, write the blueprint to `LOCAL-MEMORY/CURRENT_TASK/` so implementation can
consume it.

## 8. Pre-send checks

1. Goal restated; ambiguities classified; differential-reading test applied (§1).
2. Acceptance criteria are all testable (§1).
3. Open questions resolved, or the blocking ones asked (P0 #7).
4. Approach #0 baseline written down (§3).
5. ≥3 approaches, scored, weakest steelmanned (§3).
6. Style anchor cited from this repo, or `NOVEL` justified (§2).
7. The seven §4 decisions made explicitly, not deferred.
8. IS / IS NOT with ≥2 IS NOT rows (§5).
9. Pre-mortem with 3 causes and the killer risk named (§6).
10. Per-step verification commands given, tiered per `CLAUDE.md` §6.
11. No source file was modified.
12. End with `In one sentence: recommend <approach> in <N> steps; verified by <command>.`
