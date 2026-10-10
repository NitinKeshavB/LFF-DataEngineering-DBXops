---
name: concept-explanation
description: Build an evidence-bound mental model of how part of a Python package actually works. Use WHENEVER the user asks WHAT, HOW or WHERE about existing code — "explain X", "what does X do", "how does this work", "where is X handled", "walk me through this module", "what calls this", "I need to understand X before changing it", "orient me in this codebase" — or names a module, class, function or concept and asks how it fits. Predicts structure BEFORE reading, then confirms or refutes against file:line evidence; cites every claim; declares INCONCLUSIVE rather than inventing behavior. Read-only — never modifies source. Output is a good pre-read before root-cause-analysis or implementation-blueprint. Do NOT use for diagnosing a defect (root-cause-analysis), planning a change (implementation-blueprint), writing code (surgical-implementation), or a trivial single-symbol lookup where Read or Grep answers it directly.
allowed-tools: Read, Glob, Grep, Bash, Agent, WebSearch, WebFetch
---

# Concept Explanation

**Mission:** produce a mental model of `<target>` that is **falsifiable and cited** —
predict first, then verify against the code. **Read-only. No source edits.**

**Layering:** sits on `CLAUDE.md`; cite its sections rather than restating them.

## 0. Scope check first

If the answer is a single symbol's definition, this skill is overkill — `Grep` and
`Read` it, answer in one or two lines, and stop (`CLAUDE.md` §3, Tier 0). Use this
skill when the target spans modules, or when the user is about to change the code and
needs to know the blast radius.

## 1. Predict before reading

State, before opening any file:

- **Architectural hypothesis** — where this likely lives and how it is likely shaped.
- **Expected entry points** — how a caller probably reaches it.
- **What would surprise you** — the observation that would mean your model is wrong.

The point is that the prediction can be *refuted*. An explanation that only ever
confirms its own first guess is not an investigation.

## 2. Locate — cheapest probe first

```bash
ls src/*/                                      # the package's real module layout
grep -rn "def <name>\|class <name>" src/ tests/ # definitions
grep -rn "<name>" src/ tests/                   # all uses, incl. the tests
git log --oneline -15 -- <path>                 # why it looks this way
```

Rules: stop as soon as you have the definition. At most ~4 grep variants, OR-combined
(`grep -rnE 'Thing|THING|thing_'`). If the sweep will read more than ~5 files, spawn
an `Agent` (`Explore`) so the digest comes back instead of the whole corpus
(`CLAUDE.md` is explicit that long reads belong in a subagent).

**Read the tests.** `tests/unit_tests/` mirrors `src/`, so the test for a module is
the executable specification of its intended contract — often more honest than its
docstring. If behavior and docstring disagree, say so; do not quietly pick one.

## 3. Trace the real path

For each step, record **where control enters, what it does, what it returns, and where
it goes next** — each with `file:line`. Specifically for Python:

- **`__init__.py` re-exports** mean the import path a user writes is usually *not*
  where the code lives. Follow the re-export to the defining module and say both.
- **Decorators change the contract.** A decorated function's real behavior is the
  decorator's. Read it.
- **Import-time side effects** — module-level code, registry population, singletons —
  run once at import and are invisible in the call path. Check for them.
- **Dynamic dispatch** (`getattr`, `**kwargs` passthrough, registries, plugins)
  defeats grep. When you hit it, say the path is dynamic rather than guessing a target.
- **Inheritance and MRO** — a method may be defined in a base class. Follow it up.

## 4. Verification before explanation

Re-read every line you are about to cite (P0 #1) and check each load-bearing claim
with an independent question: *what else would have to be true if this were right?*
Then check that.

Label evidence: **STRONG** (read at `file:line` this turn, or a command printed it),
**MODERATE** (inferred), **WEAK** (assumption). Tag unverified statements
`ASSUMPTION:`. Never present inference as a reading.

Where it is cheap, make the model *executable* instead of asserting it:

```bash
python -c "import <pkg>; print(<pkg>.__all__ if hasattr(<pkg>,'__all__') else dir(<pkg>))"
python -m pytest tests/ -k "<name>" -v
```

**Where the model and the code disagree, the code wins.** Report the gap.

## 5. Output

Lead with the `CLAUDE.md` §4 Checkpoint when the target is non-trivial. Then:

1. **One-sentence answer** — what this thing is, in plain language.
2. **How it fits** — the layer it lives in and its neighbours.
3. **The path** — numbered steps with `file:line` for each hop.
4. **Public surface** — what is exported and therefore a contract (P0 #5).
5. **Contracts and invariants** — what callers must guarantee; what it guarantees back.
6. **Where to dig** — if the user is heading for a change or a bug, the 2–3 specific
   places to look first, and why.
7. **Blast radius** — who depends on this (shown via `grep`), and what breaks if its
   behavior changes.
8. **Gaps** — what you could not determine, and the command that would settle it.

## 6. Teach-back

Before sending, explain the target in **exactly 3 sentences**: what it is, how it
works, why it exists. If any sentence is circular, jargon-heavy, or needs a caveat to
be true, your model is still incomplete — go back to §3 rather than shipping prose
that sounds right.

## 7. Pre-send checks

1. Prediction recorded **before** reading (§1).
2. Every claim carries `file:line` or an `ASSUMPTION:` tag.
3. Every cited line was read **this turn** (P0 #1).
4. `__init__.py` re-exports followed to their defining module (§3).
5. Tests consulted as the behavioral spec (§2).
6. Dynamic dispatch flagged rather than guessed (§3).
7. Blast radius shown with real `grep` output, not asserted.
8. 3-sentence teach-back passes (§6).
9. Gaps stated with the command that would close them; `INCONCLUSIVE` over invention.
10. No source file was modified.
11. End with `In one sentence: <target> is <what>, reached via <path>.`
