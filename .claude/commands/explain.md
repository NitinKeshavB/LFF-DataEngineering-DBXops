---
description: "Build a cited mental model of how part of cookie-cutter-dbx works. Triggers: /explain, \"explain X\", \"what does X do\", \"how does this work\", \"where is X handled\", \"walk me through this module\", \"what calls this\", \"I need to understand X before changing it\", \"orient me in this codebase\"."
argument-hint: "<module | class | function | concept>"
allowed-tools: Read, Glob, Grep, Bash, Agent
---

# /explain — architectural mental model

Invoke the **`concept-explanation`** skill on `$ARGUMENTS`.

**Read-only.** No edits. Good pre-read before `/bug-why` or `/howto-implement` on
unfamiliar code.

`$ARGUMENTS` empty → reply `/explain requires a target. Usage: /explain <module | class | function | concept>` and stop.

If a single `Read` or `Grep` answers it, just answer — do not run a full
investigation for a one-symbol lookup (`CLAUDE.md` §3, Tier 0).

Otherwise, per the skill:

- **Predict the structure before reading**, including what would surprise you, so the
  evidence can refute the guess.
- **Read the tests** — `tests/unit_tests/` mirrors `src/`, so the test is the
  executable spec of the intended contract, often more honest than the docstring. If
  they disagree, report the gap rather than quietly picking one.
- **Follow `__init__.py` re-exports** to the defining module and name both: the import
  path a user writes is usually not where the code lives.
- **Read decorators** — a decorated function's real behavior is the decorator's.
- **Check for import-time side effects** — module-level code runs once and is invisible
  in the call path.
- **Flag dynamic dispatch** (`getattr`, registries, `**kwargs` passthrough) rather than
  guessing its target.
- Every claim gets `file:line` or an `ASSUMPTION:` tag. Show blast radius with real
  `grep` output.
- Finish with a **3-sentence teach-back**: what it is, how it works, why it exists. If
  any sentence is circular or needs a caveat, the model is incomplete.

Use an `Agent` (`Explore`) for sweeps likely to read more than ~5 files.

End with `In one sentence: <target> is <what>, reached via <path>.`
