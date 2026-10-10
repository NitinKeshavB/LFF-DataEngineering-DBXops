---
description: "Plan a change to cookie-cutter-dbx before writing any code — ranked approaches, testable acceptance criteria, open questions resolved. Triggers: /howto-implement, \"how should I implement X\", \"plan this feature\", \"what's the best approach\", \"design this API\", \"should I use X or Y\", \"add support for X\", \"refactor this\", or a pasted issue or user story to plan."
argument-hint: "<feature description | issue number | path to an analysis>"
allowed-tools: Read, Glob, Grep, Bash, Agent, Write
---

# /howto-implement — implementation blueprint

Invoke the **`implementation-blueprint`** skill on `$ARGUMENTS`.

**Planning only.** No edits to `src/` or `tests/`. Execution is `/implement`.

`$ARGUMENTS` empty → reply `/howto-implement requires input. Usage: /howto-implement <feature | issue | analysis path>` and stop.

Required before any recommendation:

- The request restated, ambiguities classified, and the **differential-reading test**
  applied: could two careful readers write contradictory tests from this?
- Acceptance criteria as Given/When/Then. A criterion you cannot write as a test is
  not a criterion.
- **Open questions at zero** — ask the blocking ones (P0 #7). Never substitute an
  assumption and proceed silently.
- **Approach #0 = the smallest change that satisfies the request**, written down even
  if it looks inadequate; plus ≥2 approaches differing in *mechanism*. Scored
  YAGNI 35 / reuse 25 / files 20 / risk 20, with the weakest **steelmanned**.
- A style anchor cited from this repo, or `NOVEL` justified.
- The explicit decisions: public vs private surface (P0 #5), any new dependency
  justified against the standard library (P0 #6), the `>=3.7` floor, error types,
  signature shape, the complexity budget, and the test shape.
- IS / IS NOT with **≥2 IS NOT rows**.
- A pre-mortem: the 3 likeliest failures, plus the **killer risk** and the command
  that tests it — run it now if it is cheap.
- Per-step verification commands, tiered per `CLAUDE.md` §6, and a rollback path.

End with `In one sentence: recommend <approach> in <N> steps; verified by <command>.`
