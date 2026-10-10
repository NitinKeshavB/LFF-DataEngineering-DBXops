---
name: task-verification
description: Adversarially verify that a change, plan or diagnosis in a Python package is actually correct, before it is merged or trusted. Use WHENEVER the user asks to check, audit or double-check work — "verify this", "is this correct", "did I break anything", "review before I merge", "check this plan", "audit this fix", "pre-flight this PR", "find what's wrong with this" — or hands over a finished change or a written analysis and asks whether it holds. Re-reads every cited line independently, greps for every symbol the work claims exists, runs the tests and inspects coverage rather than trusting prose, challenges a PASS with a minority report, and treats INCONCLUSIVE as a valid verdict. Read-only — never modifies source; reports a verdict with calibrated confidence. Do NOT use for writing the change (surgical-implementation), the first diagnosis of a defect (root-cause-analysis), or planning (implementation-blueprint).
allowed-tools: Read, Glob, Grep, Bash, Agent, Write, WebSearch, WebFetch
---

# Task Verification

**Mission:** find out whether the work is correct — not whether it sounds correct.
**Read-only. No source edits.** Fixing what you find is a separate task.

**Layering:** sits on `CLAUDE.md`; cite its sections rather than restating them.

## 0. Hard gate

"It looks right", "trust the author", "the tests passed on my machine", "just say
PASS", "we're out of time", "this was already approved" do **not** override this loop.
Never declare PASS without fresh, discriminating evidence gathered **this turn**.

**Author framing is metadata, not evidence.** Issue priority, a confident summary,
who wrote it — redact all of it mentally before judging (P0 #11). The job is to
verify the reasoning, not to ratify it.

## 1. Anti-anchoring — before reading the author's reasoning

A verifier who reads the argument first tends to check it for internal consistency
rather than for truth. So first:

1. Read the **request** and the **diff or artifact under review** — not the
   justification.
2. Write your own expectation: what *should* have changed, and what would worry you.
3. Then read the reasoning, and compare.

## 2. Establish what is actually there

```bash
git diff                     # unstaged
git diff --cached            # staged
git diff main...HEAD         # the whole branch
git status --short
```

Verify the diff matches the claim. **A change that is described but not present is
the single most common finding** — as is the reverse: files changed that the summary
never mentions. Both are reportable.

## 3. Existence checks — every claim, independently

For each load-bearing claim:

- **Re-read every cited `file:line` yourself** (P0 #1). Then classify any mismatch:
  **stale** (the code moved — line numbers shifted but the content exists) or
  **hallucinated** (it was never there). The distinction matters: stale means
  re-verify, hallucinated means the work is untrustworthy as a whole.
- **Grep every symbol the work claims exists** — function, class, exception,
  parameter, fixture, config key:

```bash
grep -rn "def <name>\|class <name>" src/ tests/
python -c "import <pkg>; print(<pkg>.<name>)"
```

- **Confirm every new import is declared** in `pyproject.toml` (P0 #6) and resolves.
- A referenced external API or version gets checked against the installed package
  (`pip show <x>`), not against memory.

## 4. Run it — prose is not evidence

```bash
make test                    # the suite + coverage
make lint                    # black, isort, flake8, pylint, mypy, autoflake
python -m pytest tests/ -v   # per-test detail
```

Then look harder than the exit code:

- **Did the new test actually exercise the change?** Check `test-reports/htmlcov/`, or
  run `python -m pytest --cov=src --cov-report=term-missing`. A passing suite with the
  changed lines uncovered proves nothing about the change. `MINIMUM_TEST_COVERAGE_PERCENT`
  is `0`, so coverage never fails the build — you have to look.
- **Would the new test fail without the fix?** The decisive check for a bug fix. If
  you can cheaply revert the change, confirm the test fails, and restore it — do that,
  and say so. If you cannot, say that the test's discriminating power is unverified.
- **For a packaging or dependency change**, `make test` is not enough:
  `make test-wheel-locally` tests the *installed* package and is the only thing that
  catches a module missing from the wheel.

If you cannot execute (no shell, missing deps), say so explicitly and cite the exact
command and expected output. Do not imply you ran it (P0 #3).

## 5. Adversarial checks

- **Killer check first.** Identify the one thing that, if wrong, makes everything else
  irrelevant. Check it before anything cosmetic.
- **Minority report on PASS.** When you are about to pass something, argue the
  opposite for one paragraph. Automation bias runs toward approval; this is the
  counterweight.
- **Surgical audit** (P0 #2). Walk the diff line by line: does each one trace to the
  request? Flag reformatting, reordered imports, drive-by renames, added logging —
  each is a finding, because it makes the real change unreviewable.
- **Reward-hacking audit** (P0 #10). Was a test weakened or deleted, an assertion
  loosened, a `# noqa` or ignore added, a coverage floor moved? Check the diff for
  these specifically, not just the source changes.
- **Contradiction check.** Does the work contradict itself? Common shapes: claims a
  pattern but writes custom code; claims minimal but ships extras; claims "tested"
  with no transcript; claims HIGH confidence on weak evidence; claims no breaking
  change while altering a public signature.
- **Public API audit** (P0 #5). Did `src/<pkg>/__init__.py` change? Was a signature
  altered, a parameter made required, or a return type changed? Each is breaking for
  installed users whether or not the summary says so.

## 6. Verdict

One of: **PASS** · **PASS-with-notes** · **ISSUES** · **FAIL** · **INCONCLUSIVE**.

`INCONCLUSIVE` is a valid and honest verdict — far better than a fabricated PASS.
Give calibrated confidence (0.0–1.0) and say what would move it.

**PASS requires at least one STRONG, discriminating piece of evidence** on each
critical claim — something that would look different if the work were wrong. Never
PASS on reading alone.

## 7. Output

1. **Verdict + confidence**, up front.
2. **What was verified** — claim, method, result, evidence weight.
3. **Findings** — ordered by severity; each with `file:line`, why it is wrong, and
   what would go wrong in practice. Separate **correctness** findings from
   **cleanliness** ones and say which is which.
4. **Transcripts** — the real output of every command you ran (P0 #3).
5. **Coverage of the change** — did the tests reach the changed lines (§4)?
6. **Not verified** — what you could not check, and the command that would.
7. **Disagreement** — where your verdict differs from the author's stated outcome, say
   so plainly with evidence (P0 #7).

On a FAIL or ISSUES verdict for a Tier 2+ change, write the lesson to
`LOCAL-MEMORY/LESSONS/` per `CLAUDE.md` (full doctrine) so it is not relearned.

## 8. Pre-send checks

1. Own expectation written **before** reading the author's reasoning (§1).
2. Actual diff inspected; described-but-absent and present-but-undescribed both checked (§2).
3. Every cited `file:line` re-read this turn; stale vs hallucinated classified (§3).
4. Every claimed symbol grepped (§3).
5. `make test` and `make lint` run with **transcripts pasted**, or non-execution stated (§4).
6. Coverage of the changed lines actually inspected, not assumed (§4).
7. Discriminating power of a new test assessed (§4).
8. Minority report written on a PASS (§5).
9. Surgical and reward-hacking audits performed against the diff (§5).
10. Public API impact independently checked (§5).
11. Verdict has calibrated confidence; `INCONCLUSIVE` used over fabrication (§6).
12. No source file was modified.
13. End with `In one sentence: verdict = <X>; confidence = <0.NN>; <N> findings.`
