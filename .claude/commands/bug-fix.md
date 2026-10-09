---
description: "Apply a surgical fix to cookie-cutter-dbx from a diagnosis, proving it with a failing-test-first run. Triggers: /bug-fix, \"fix this bug\", \"apply the fix\", \"make the failing test pass\", or handing over a root cause analysis and asking to implement it."
argument-hint: "<path to the analysis | issue number | description of the fix>"
allowed-tools: Read, Edit, Write, Glob, Grep, Bash, Agent
---

# /bug-fix — surgical fix

Invoke the **`surgical-implementation`** skill in **FIX mode** on `$ARGUMENTS`.

**This modifies source.** `CLAUDE.md` P0 #1, #2, #3 and #10 are in force.

`$ARGUMENTS` empty → reply `/bug-fix requires a contract. Usage: /bug-fix <analysis path | issue | description>` and stop.

Order of operations — do not reorder:

1. **Re-read the contract's citations.** If the code has moved since the analysis was
   written, report the drift and stop. Never implement against a stale plan.
2. **Show the test failing first.** Paste the transcript. No failing test, no fix —
   if it already passes, the bug was never reproduced, so go back to `/bug-why`.
3. **Read before every edit**, quoting the 3 anchor lines (P0 #1).
4. **Fix the cause, not the symptom**, and only that. Nothing from the forbidden list:
   no reformatting, no import reordering, no drive-by renames, no added logging, no
   adjacent refactor. Note what you noticed; do not touch it (P0 #2).
5. **Prove it:** the target test, then `make test`, then `make lint` — **real
   transcripts pasted**, not predicted (P0 #3).

Hard limits: never edit a test to make it pass, add a `# noqa`, or move the coverage
floor (P0 #10). Three failed revision attempts → stop and report; the diagnosis was
probably wrong. Over ~50 changed lines → stop and ask; that is not a surgical fix.

End with `In one sentence: <what changed>; verified by <command> = PASS.`
