---
description: "Diagnose why something fails in cookie-cutter-dbx — read-only root cause analysis. Triggers: /bug-why, \"why is this failing\", \"find the root cause\", \"investigate this bug\", \"debug this\", a pasted Python traceback, a pytest failure, ImportError, AttributeError, a flaky test, a CI log."
argument-hint: "<issue number | error message | traceback | file:line>"
allowed-tools: Read, Glob, Grep, Bash, Agent, Write
---

# /bug-why — root cause analysis

Invoke the **`root-cause-analysis`** skill on `$ARGUMENTS`.

**Read-only.** No edits to `src/` or `tests/`. The fix is `/bug-fix`.

`$ARGUMENTS` empty → reply `/bug-why requires input. Usage: /bug-why <issue | error | traceback | file:line>` and stop.

Non-negotiable, per the skill and `CLAUDE.md`:

- Initial intuition recorded **before** reading any code (anti-anchoring).
- A repro that **fails on current code**, or an explicit `non-reproducible` verdict
  and a stop. Start narrow: `python -m pytest <path>::<test> -v`.
- 3–6 competing hypotheses covering different classes, each with the command that
  discriminates it — not three phrasings of one guess.
- Refutation attempted on the leading hypothesis before confirmation (P0 #9).
- Every `file:line` backed by a `Read` this turn (P0 #1).
- A **Primary Fix** table (`What | Where | How`) — never prose. That table is the
  contract `/bug-fix` consumes.
- `INCONCLUSIVE` over a fabricated cause (`CLAUDE.md` §5).

A `try/except` or a `None` guard at the crash site is a symptom patch. Keep going
until a fix would **prevent** the bug.

End with `In one sentence: root cause = <one line>; confidence = <level>.`
